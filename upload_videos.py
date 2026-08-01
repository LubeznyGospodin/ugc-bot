"""
Заливка роликов креаторов (project=base) в Kinescope и сбор hash'ей для карточек Тильды.

Для каждого ролика (creator_works.file_id): качает из Telegram → грузит в Kinescope →
берёт play-слаг (это и есть PRIVATE HASH для видео-опции Тильды). Всё пишется в ledger
(kinescope_ledger.json) — перезапуск пропускает уже залитое (resume) и переживает обрывы.

⚠️ Telegram Bot API отдаёт файлы только до ~20 МБ (getFile). Ролики крупнее помечаются
'toobig' и пропускаются (их зальёт владелец вручную или через MTProto-качалку отдельно).

Итог: kinescope_map.csv — tg_id ; Имя ; hashes (через пробел) — для привязки к карточкам.

Запуск:  python upload_videos.py            # все base-ролики (resume)
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import asyncpg
from dotenv import load_dotenv

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

SP = "/private/tmp/claude-501/-Users-nastasyapopova-Desktop-ugc-bot-v2/86555545-d7d7-4b55-9291-d96df2873370/scratchpad"
BOT = json.load(open(f"{SP}/vars_bot.json"))["BOT_TOKEN"]
DB = json.load(open(f"{SP}/vars_pg.json"))["DATABASE_PUBLIC_URL"].split("?")[0].replace("postgres://", "postgresql://")
TOK = os.getenv("KINESCOPE_TOKEN")
PROJECT = "6d8b24d4-2b50-4d2c-9be5-c1ff7b3b9b8d"

LEDGER = "/Users/nastasyapopova/Desktop/ugc_bot_v2/kinescope_ledger.json"
MAP_CSV = "/Users/nastasyapopova/Desktop/ugc_bot_v2/kinescope_map.csv"


def _retry(fn, tries=4, pause=2.0):
    for t in range(tries):
        try:
            return fn()
        except Exception:  # noqa: BLE001
            if t == tries - 1:
                raise
            time.sleep(pause * (t + 1))


def load_ledger() -> dict:
    if os.path.exists(LEDGER):
        return json.load(open(LEDGER))
    return {}


def save_ledger(led: dict) -> None:
    json.dump(led, open(LEDGER, "w"), ensure_ascii=False, indent=1)


def tg_getfile(file_id: str):
    """(file_path, size) или None если файл >20МБ. Bot API на крупных отдаёт HTTP 400
    'file is too big' (исключением, не ok:false) — ловим и трактуем как toobig."""
    url = f"https://api.telegram.org/bot{BOT}/getFile?file_id={urllib.parse.quote(file_id)}"
    try:
        r = _retry(lambda: json.load(urllib.request.urlopen(url, timeout=30)))
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None  # too big for Bot API
        raise
    if not r.get("ok"):
        return None
    return r["result"]["file_path"], r["result"].get("file_size", 0)


_TRANSLIT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
    "abvgdeejziyklmnoprstufhccss'y'euaABVGDEEJZIYKLMNOPRSTUFHCCSS'Y'EUA")


def _ascii(s: str) -> str:
    """HTTP-заголовки только latin-1 → транслитерируем имя, чистим не-ASCII."""
    t = (s or "").translate(_TRANSLIT)
    return "".join(ch for ch in t if ch.isascii()).strip() or "creator"


def kinescope_upload(local: str, title: str) -> str:
    """Возвращает play-слаг (hash для Тильды)."""
    data = open(local, "rb").read()
    req = urllib.request.Request(
        "https://uploader.kinescope.io/v2/video", data=data, method="POST",
        headers={"Authorization": "Bearer " + TOK, "X-Parent-ID": PROJECT,
                 "X-Video-Title": _ascii(title), "Content-Type": "video/mp4"})
    out = json.load(urllib.request.urlopen(req, timeout=600))["data"]
    return out["play_link"].rstrip("/").rsplit("/", 1)[-1]


async def fetch_works() -> list[dict]:
    c = await asyncpg.connect(DB)
    rows = await c.fetch("""
        select w.id as wid, w.tg_id, w.file_id, cr.full_name
        from creator_works w
        join creators cr on cr.tg_id = w.tg_id
        where w.project='base' and w.file_id is not null
        order by w.tg_id, w.id""")
    await c.close()
    return [dict(r) for r in rows]


def write_map(led: dict) -> None:
    """Группирует по креатору → строка tg_id ; Имя ; hash1 hash2 …"""
    by_creator: dict[str, dict] = {}
    for rec in led.values():
        if not rec.get("hash"):
            continue
        k = str(rec["tg_id"])
        by_creator.setdefault(k, {"name": rec.get("name", ""), "hashes": []})
        by_creator[k]["hashes"].append(rec["hash"])
    with open(MAP_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_ALL)
        w.writerow(["tg_id", "Имя", "kinescope_hashes"])
        for tg, v in by_creator.items():
            w.writerow([tg, v["name"], " ".join(v["hashes"])])


async def main() -> None:
    works = await fetch_works()
    led = load_ledger()
    print(f"роликов: {len(works)}, в ledger уже: {len(led)}")

    # индекс ролика в рамках креатора (для названия в Kinescope)
    idx_by_creator: dict[int, int] = {}
    ok = skip = toobig = err = 0
    os.makedirs(f"{SP}/vid", exist_ok=True)

    for row in works:
        wid = str(row["wid"])
        idx_by_creator[row["tg_id"]] = idx_by_creator.get(row["tg_id"], 0) + 1
        idx = idx_by_creator[row["tg_id"]]
        prev = led.get(wid)
        if prev and (prev.get("hash") or prev.get("error") == "toobig"):
            skip += 1
            continue  # уже залито или заведомо крупное — не трогаем; прочие ошибки ретраим
        name = row["full_name"] or str(row["tg_id"])
        try:
            gf = tg_getfile(row["file_id"])
            if gf is None or (gf[1] or 0) > 20 * 1024 * 1024:
                led[wid] = {"tg_id": row["tg_id"], "name": name, "idx": idx, "error": "toobig"}
                toobig += 1
                save_ledger(led)
                print(f"  ⤴ {name} #{idx} — >20МБ, пропуск (владельцу вручную)")
                continue
            local = f"{SP}/vid/{wid}.mp4"
            _retry(lambda: urllib.request.urlretrieve(
                f"https://api.telegram.org/file/bot{BOT}/{gf[0]}", local))
            h = _retry(lambda: kinescope_upload(local, f"{name} #{idx}"), tries=3, pause=3)
            led[wid] = {"tg_id": row["tg_id"], "name": name, "idx": idx, "hash": h}
            ok += 1
            print(f"  ✓ {name} #{idx} → {h}")
            os.remove(local)
            save_ledger(led)
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            led[wid] = {"tg_id": row["tg_id"], "name": name, "idx": idx, "error": str(e)[:80]}
            err += 1
            save_ledger(led)
            print(f"  ✗ {name} #{idx} — {str(e)[:80]}")

    write_map(led)
    print(f"итог: ok={ok} skip={skip} toobig={toobig} err={err}")
    print(f"ledger: {LEDGER}\nmap: {MAP_CSV}")


if __name__ == "__main__":
    asyncio.run(main())
