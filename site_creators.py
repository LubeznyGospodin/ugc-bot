"""
Снимок каталога креаторов для сайта packman-prod.ru (Next.js, ~/Desktop/packman_site).

Берёт из прод-БД всех креаторов с placement='placed', доливает недостающие медиа в
публичный Selectel-бакет `packman` и пишет `data/creators.json` для сайта:

    фото   creators/<slug>-<tg_id>/<n>.jpg      (как build_import.py — те же ключи)
    видео  creators/<slug>-<tg_id>/v<n>.mp4     (для превью в каталоге и карточке)

Идемпотентно: что уже лежит в бакете — не перекачивает. Тексты (descr, «отлично
подойдёт для») генерит catalog_creators.py — тот же копирайт, что уходил в Tilda.

Запуск:  .venv/bin/python site_creators.py                 # фото + видео + json
         .venv/bin/python site_creators.py --no-video      # только фото и json
         .venv/bin/python site_creators.py --videos 3      # сколько видео на креатора
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

import asyncpg
import boto3
import botocore
from dotenv import load_dotenv

from bot.cards import guess_gender, short_name
from catalog_creators import catalog_fields, fit_for, niche_phrase, _age_phrase

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

SP = "/private/tmp/claude-501/-Users-nastasyapopova-Desktop-ugc-bot-v2/92cb9723-db7d-473f-b12e-e132f58cda70/scratchpad"
SITE = "/Users/nastasyapopova/Desktop/packman_site"
BOT_TOKEN = json.load(open(f"{SP}/vars_bot.json"))["BOT_TOKEN"]
DB_URL = (json.load(open(f"{SP}/vars_pg.json"))["DATABASE_PUBLIC_URL"]
          .split("?")[0].replace("postgres://", "postgresql://"))

BUCKET = "packman"

# Карточки, которые не показываем на сайте, хотя в БД они одобрены.
# 752020860 — «Валерий Г»: в анкете мужское имя, а на всех кадрах девушка.
HIDDEN: set[int] = {752020860}

# Имя для сайта, когда в анкете оно записано задом наперёд или с опечаткой.
# В БД не правим: её ведёт бот, и там имя связано с постом в канале и таблицей.
RENAME: dict[int, str] = {5221880749: "Кристина Щедрина"}
PUBLIC_BASE = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru"

_s3 = boto3.client(
    "s3", endpoint_url="https://s3.ru-7.storage.selcloud.ru",
    aws_access_key_id=os.getenv("SELECTEL_S3_KEY"),
    aws_secret_access_key=os.getenv("SELECTEL_S3_SECRET"),
    region_name="ru-7", config=botocore.config.Config(signature_version="s3v4"),
)

_TRANSLIT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "abvgdeejziyklmnoprstufhccss'y'eua")


def _translit(s: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").lower().translate(_TRANSLIT))
    return out.strip("-")


def media_prefix(name: str, tg_id: int) -> str:
    """Путь медиа в бакете — тот же, что у build_import.py (уже залитое не ломаем)."""
    return f"creators/{_translit(name) or 'creator'}-{tg_id}"


# Для адреса карточки нужен читаемый транслит, а не тот же, что для ключей S3:
# у того «Яна» → «ana», «Наталья» → «natal-a». URL люди видят, поэтому здесь по-людски.
_SEO = {"ж": "zh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ю": "yu", "я": "ya",
        "ё": "yo", "й": "y", "ы": "y", "ь": "", "ъ": "", "э": "e", "х": "h"}


def seo_translit(s: str) -> str:
    out = "".join(_SEO.get(ch, ch) for ch in (s or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", out.translate(_TRANSLIT)).strip("-")


def site_slug(full_name: str, tg_id: int, taken: set[str]) -> str:
    """Адрес карточки: транслит имени, а не tg_id (это URL в индексе Google).
    Тёзок разводим фамилией, потом — хвостом tg_id."""
    parts = [p for p in seo_translit(full_name).split("-") if p]
    for n in (1, 2, 3):
        cand = "-".join(parts[:n]) or "creator"
        if cand not in taken:
            return cand
    return f"{'-'.join(parts[:2]) or 'creator'}-{tg_id % 10000}"


def _retry(fn, tries: int = 4, pause: float = 2.0):
    import time
    for t in range(tries):
        try:
            return fn()
        except Exception:
            if t == tries - 1:
                raise
            time.sleep(pause * (t + 1))


def _tg_file_path(file_id: str) -> tuple[str, int]:
    api = f"https://api.telegram.org/bot{BOT_TOKEN}"
    r = _retry(lambda: json.load(urllib.request.urlopen(
        f"{api}/getFile?file_id={urllib.parse.quote(file_id)}", timeout=30)))
    res = r["result"]
    return res["file_path"], int(res.get("file_size") or 0)


def _tg_download(file_id: str, dst: str) -> None:
    fp, _ = _tg_file_path(file_id)
    _retry(lambda: urllib.request.urlretrieve(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", dst))


def _exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def _upload(local: str, key: str, ctype: str) -> None:
    _retry(lambda: _s3.upload_file(local, BUCKET, key, ExtraArgs={"ContentType": ctype}))


def push_photos(prefix: str, file_ids: list[str]) -> list[str]:
    urls = []
    os.makedirs(f"{SP}/dl", exist_ok=True)
    for i, fid in enumerate(file_ids, 1):
        key = f"{prefix}/{i}.jpg"
        if not _exists(key):
            local = f"{SP}/dl/{key.replace('/', '_')}"
            _tg_download(fid, local)
            _upload(local, key, "image/jpeg")
            os.remove(local)
        urls.append(f"{PUBLIC_BASE}/{key}")
    return urls


def push_videos(prefix: str, works: list[dict], limit: int) -> list[dict]:
    """Кладёт до `limit` роликов и постер к первому (первый кадр видео — часто чёрный,
    поэтому берём кадр с 1-й секунды, как в docs/CHANNEL_POSTING.md)."""
    os.makedirs(f"{SP}/dl", exist_ok=True)
    out = []
    for w in works:
        if len(out) >= limit:
            break
        n = len(out) + 1
        key, pkey = f"{prefix}/v{n}.mp4", f"{prefix}/v{n}.jpg"
        url = f"{PUBLIC_BASE}/{key}"
        if _exists(key):
            out.append({"src": url, "poster": f"{PUBLIC_BASE}/{pkey}" if _exists(pkey) else None,
                        "w": w.get("vw"), "h": w.get("vh")})
            continue
        local = f"{SP}/dl/{key.replace('/', '_')}"
        try:
            fp, size = _tg_file_path(w["file_id"])
            if size and size > 20_000_000:      # Bot API отдаёт файлы только до 20 МБ
                continue
            _retry(lambda: urllib.request.urlretrieve(
                f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", local))
        except Exception as e:
            print(f"    ! видео пропущено: {str(e)[:60]}")
            continue
        poster = local.replace(".mp4", ".jpg")
        vw = vh = None
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height", "-of", "csv=p=0", local],
                capture_output=True, text=True, timeout=60).stdout.strip()
            vw, vh = (int(x) for x in probe.split(",")[:2])
            subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", local, "-frames:v", "1",
                            "-vf", "scale=540:-2", poster],
                           capture_output=True, timeout=90)
        except Exception:
            poster = None
        _upload(local, key, "video/mp4")
        if poster and os.path.exists(poster):
            _upload(poster, pkey, "image/jpeg")
            os.remove(poster)
        os.remove(local)
        out.append({"src": url, "poster": f"{PUBLIC_BASE}/{pkey}" if poster else None,
                    "w": vw or w.get("vw"), "h": vh or w.get("vh")})
    return out


async def fetch_placed() -> list[dict]:
    c = await asyncpg.connect(DB_URL)
    rows = await c.fetch("""
        select tg_id, full_name, city, age, categories, channel_msg_id
        from creators where placement = 'placed' order by created_at desc""")
    rows = [r for r in rows if r["tg_id"] not in HIDDEN]
    out = []
    for r in rows:
        phs = await c.fetch("select file_id from creator_photos where tg_id=$1 and kind='photo'"
                            " order by created_at", r["tg_id"])
        wks = await c.fetch("""select file_id, vw, vh from creator_works
              where tg_id=$1 and project='base' and coalesce(too_big,false)=false
              order by normalized desc nulls last, position nulls last, id""", r["tg_id"])
        out.append({**dict(r), "file_ids": [p["file_id"] for p in phs],
                    "works": [dict(w) for w in wks]})
    await c.close()
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=int, default=2, help="сколько роликов на креатора")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("-o", "--out", default=f"{SITE}/data/creators.json")
    args = ap.parse_args()
    vlimit = 0 if args.no_video else args.videos

    creators = await fetch_placed()
    print(f"одобренных креаторов: {len(creators)}")

    taken: set[str] = set()
    items = []
    for cr in creators:
        tg = cr["tg_id"]
        name = RENAME.get(tg, cr["full_name"])
        if not cr["file_ids"]:
            print(f"  ! {name} — нет фото, пропуск")
            continue
        # путь к медиа считаем по имени из БД: переименование на витрине
        # не должно приводить к повторной заливке тех же файлов
        prefix = media_prefix(cr["full_name"], tg)
        try:
            photos = push_photos(prefix, cr["file_ids"])
            videos = push_videos(prefix, cr["works"], vlimit) if vlimit else []
        except Exception as e:
            print(f"  ✗ {name}: {str(e)[:80]}")
            continue
        f = catalog_fields(name, cr["city"], cr["age"], cr["categories"], seed=str(tg))
        slug = site_slug(name, tg, taken)
        taken.add(slug)
        items.append({
            "tg": tg,
            "slug": slug,
            "name": short_name(name),
            "city": cr["city"],
            "age": _age_phrase(cr["age"]) or None,
            "categories": [x.strip() for x in (cr["categories"] or "").replace(";", ",").split(",") if x.strip()],
            "gender": guess_gender(name),
            "descr": f["descr"],
            "niche": niche_phrase(cr["categories"], limit=3),
            "fitFor": fit_for(cr["categories"]),
            "photos": photos,
            "videos": videos,
            "post": f"https://t.me/ugc_creatory/{cr['channel_msg_id']}" if cr["channel_msg_id"] else None,
        })
        print(f"  ✓ {short_name(name):<16} /{slug:<22} {len(photos)} фото, {len(videos)} видео")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    print(f"\n{args.out}: {len(items)} креаторов, "
          f"{sum(len(i['photos']) for i in items)} фото, {sum(len(i['videos']) for i in items)} видео")


def _demo() -> None:
    taken: set[str] = set()
    a = site_slug("Анастасия Иванова", 1, taken); taken.add(a)
    b = site_slug("Анастасия Петрова", 2, taken); taken.add(b)
    c = site_slug("Анастасия", 3, taken); taken.add(c)
    assert a == "anastasiya", a
    assert b == "anastasiya-petrova", b
    assert c == "anastasiya-3", c     # тёзка без фамилии — хвост tg_id
    assert seo_translit("Яна Бельолова") == "yana-belolova", seo_translit("Яна Бельолова")
    assert seo_translit("Наталья") == "natalya" and seo_translit("Дарья") == "darya"
    # префикс медиа обязан совпадать с тем, что уже залил build_import.py
    assert media_prefix("Айым", 7633725839) == "creators/ayym-7633725839"
    print("ok:", a, b, c)


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
    else:
        asyncio.run(main())
