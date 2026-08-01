"""
Конвейер авто-карточек креаторов для каталога Тильды.

Для каждого креатора: тянет данные из прод-БД → качает фото из Telegram по file_id →
льёт в публичный Selectel-бакет `packman` под `creators/<slug>/` → генерит title/descr/text
→ пишет строку в CSV для импорта в Тильду (раздел «UGC-креаторы»).

Импорт CSV в Тильду делает владелец руками (браузер): Каталог → «…» → «Импортировать из CSV».
Финальную запись в магазин из этого окружения не выполнить (песочница + харнесс), а всё
до неё — да. Видео (per-photo hash на Kinescope) — отдельная фаза, CSV его не несёт.

Запуск:  python build_import.py 1065071728 441039734 ...     # список tg_id
         python build_import.py --works                      # все с работами (project=base)
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import asyncpg
import boto3
import botocore
from dotenv import load_dotenv

from catalog_creators import catalog_fields

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

SP = "/private/tmp/claude-501/-Users-nastasyapopova-Desktop-ugc-bot-v2/86555545-d7d7-4b55-9291-d96df2873370/scratchpad"
BOT_TOKEN = json.load(open(f"{SP}/vars_bot.json")).get("BOT_TOKEN")
DB_URL = json.load(open(f"{SP}/vars_pg.json")).get("DATABASE_PUBLIC_URL").split("?")[0].replace("postgres://", "postgresql://")

BUCKET = "packman"
PUBLIC_BASE = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru"  # публичный домен packman
UGC_PARTUID = "380691270782"  # раздел «UGC-креаторы»

_s3 = boto3.client(
    "s3", endpoint_url="https://s3.ru-7.storage.selcloud.ru",
    aws_access_key_id=os.getenv("SELECTEL_S3_KEY"),
    aws_secret_access_key=os.getenv("SELECTEL_S3_SECRET"),
    region_name="ru-7", config=botocore.config.Config(signature_version="s3v4"),
)


def _slug(name: str, tg_id: int) -> str:
    """Латинский слаг из имени + tg_id (уникальность и предсказуемый путь)."""
    translit = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejziyklmnoprstufhccss'y'eua")
    base = (name or "").lower().translate(translit)
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-") or "creator"
    return f"{base}-{tg_id}"


def _retry(fn, tries: int = 4, pause: float = 2.0):
    """Повтор на транзиентных сетевых ошибках (SSL EOF, таймауты TG/Selectel)."""
    import time
    for t in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — сеть капризная, ретраим всё
            if t == tries - 1:
                raise
            time.sleep(pause * (t + 1))


def _tg_download(file_id: str, dst: str) -> None:
    api = f"https://api.telegram.org/bot{BOT_TOKEN}"
    r = _retry(lambda: json.load(urllib.request.urlopen(
        f"{api}/getFile?file_id={urllib.parse.quote(file_id)}", timeout=30)))
    fp = r["result"]["file_path"]
    _retry(lambda: urllib.request.urlretrieve(
        f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fp}", dst))


def _s3_exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:  # noqa: BLE001
        return False


def _upload_photos(tg_id: int, name: str, file_ids: list[str]) -> list[str]:
    """Качает фото из TG, льёт в Selectel, возвращает чистые публичные URL.
    Идемпотентно: уже залитые ключи пропускает (быстрый resume при перезапуске)."""
    slug = _slug(name, tg_id)
    urls = []
    os.makedirs(f"{SP}/dl", exist_ok=True)
    for i, fid in enumerate(file_ids, 1):
        key = f"creators/{slug}/{i}.jpg"
        if not _s3_exists(key):
            local = f"{SP}/dl/{slug}-{i}.jpg"
            _tg_download(fid, local)
            _retry(lambda: _s3.upload_file(local, BUCKET, key,
                                           ExtraArgs={"ContentType": "image/jpeg"}))
        urls.append(f"{PUBLIC_BASE}/{key}")
    return urls


async def _fetch(tg_ids: list[int] | None) -> list[dict]:
    c = await asyncpg.connect(DB_URL)
    if tg_ids:
        rows = await c.fetch(
            "select tg_id, full_name, city, age, categories from creators where tg_id = any($1::bigint[])",
            tg_ids)
    else:  # все с работами project=base и с фото
        rows = await c.fetch("""
            select cr.tg_id, cr.full_name, cr.city, cr.age, cr.categories
            from creators cr
            where cr.photo is not null
              and exists (select 1 from creator_works w where w.tg_id=cr.tg_id and w.project='base')
            order by cr.created_at desc""")
    out = []
    for r in rows:
        phs = await c.fetch(
            "select file_id from creator_photos where tg_id=$1 and kind='photo' order by created_at",
            r["tg_id"])
        out.append({**dict(r), "file_ids": [p["file_id"] for p in phs]})
    await c.close()
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tg_ids", nargs="*", type=int)
    ap.add_argument("--works", action="store_true", help="все креаторы с работами")
    ap.add_argument("-o", "--out", default=f"{SP}/import_ugc.csv")
    args = ap.parse_args()
    if not args.tg_ids and not args.works:
        ap.error("укажи tg_id или --works")

    creators = await _fetch(args.tg_ids or None)
    print(f"креаторов: {len(creators)}")

    # ТОЧНЫЙ формат импорта Тильды: разделитель ЗАПЯТАЯ, заголовки как в экспорте,
    # фото через ПРОБЕЛ в одной ячейке Photo, Text — HTML, Category — имя раздела.
    header = ["Category", "Title", "Description", "Text", "Photo", "Price",
              "External ID", "SEO title", "SEO descr", "SEO keywords",
              "FB title", "FB descr"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        ok = skip = err = 0
        for cr in creators:
            if not cr["file_ids"]:
                print(f"  ! {cr['full_name']} — нет фото, пропуск"); skip += 1; continue
            try:
                fields = catalog_fields(cr["full_name"], cr["city"], cr["age"], cr["categories"])
                urls = _upload_photos(cr["tg_id"], cr["full_name"], cr["file_ids"])
                w.writerow(["UGC-креаторы", fields["title"], fields["descr"], fields["text"],
                            " ".join(urls), "0", str(cr["tg_id"]), fields["seo_title"],
                            fields["seo_descr"], fields["seo_keywords"],
                            fields["fb_title"], fields["fb_descr"]])
                f.flush()
                print(f"  ✓ {fields['title']} — {len(urls)} фото"); ok += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ✗ {cr['full_name']} — ошибка: {str(e)[:80]}"); err += 1
        print(f"итог: ok={ok} skip={skip} err={err}")
    print(f"CSV готов: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
