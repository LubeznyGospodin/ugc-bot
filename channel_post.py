"""
Публикация карточки креатора в канал @ugc_creatory (Telethon, юзер-сессия владельца).

Пост = альбом (до 2 фото + до 4 видео из группы «Креаторы Packman Prod») + подпись
build_creator_card() со ссылкой на прямую карточку Тильды:
    https://packman-prod.ru/ugc_creators/tproduct/<UID>

Медиа берём ИЗ ГРУППЫ (bot API file_id не переносится на юзер-сессию):
  «📷 Фото креатора … id <tg>»   → фото ниже
  «🎬 Работы креатора … id <tg>» → видео ниже

Идемпотентность: channel_published.json (tg → message_id). Уже опубликованных пропускаем.

Запуск:
  python channel_post.py --dry 676848432=603245427303 1134096230=710212130513
  python channel_post.py       676848432=603245427303      # реальная публикация
Формат аргумента: <tg_id>=<tilda_uid>.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, "/Users/nastasyapopova/Desktop/ugc_bot_v2")
import asyncpg
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import (DocumentAttributeVideo, InputMediaUploadedDocument,
                               InputMediaUploadedPhoto)

from bot.cards import build_creator_card, short_name

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

SP = "/private/tmp/claude-501/-Users-nastasyapopova-Desktop-ugc-bot-v2/64cb78ca-4669-4f1e-9e8d-4c28a3b6afab/scratchpad"
GROUP = -1004313489669
CHANNEL = "ugc_creatory"
CARD_URL = "https://packman-prod.ru/ugc_creators/tproduct/{uid}"
REG = "/Users/nastasyapopova/Desktop/ugc_bot_v2/channel_published.json"
MAX_PHOTOS = 2
MAX_VIDEOS = 4

_HDR_WORKS = re.compile(r"Работы креатора.*?id\s+(\d+)", re.S)
_HDR_PHOTO = re.compile(r"Фото креатора.*?id\s+(\d+)", re.S)

DB = json.load(open(f"{SP}/vars_pg.json"))["DATABASE_PUBLIC_URL"].split("?")[0].replace("postgres://", "postgresql://")


def _is_video(m) -> bool:
    return bool(getattr(m, "video", None)) or bool(
        getattr(m, "document", None) and "video" in (getattr(getattr(m, "file", None), "mime_type", "") or ""))


def _vid_dims(mm) -> tuple[int, int, int]:
    """(duration, w, h) из атрибутов сообщения — для корректного превью."""
    for a in (getattr(getattr(mm, "document", None), "attributes", None) or []):
        if isinstance(a, DocumentAttributeVideo):
            return int(a.duration or 0), int(a.w or 0), int(a.h or 0)
    return 0, 0, 0


def _probe(path: str, fallback=(0, 0, 0)) -> tuple[int, int, int]:
    """Реальные ДИСПЛЕЙНЫЕ (duration, w, h) из файла. Атрибуты исходного сообщения часто
    0/пустые или без учёта поворота → клиент рендерит видео квадратом/растянутым.
    Учитываем rotation (tags.rotate или side_data): при ±90/270 меняем w/h местами."""
    try:
        j = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
             "-of", "json", path], capture_output=True, text=True).stdout)
        st = j.get("streams", [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        dur = int(float(j.get("format", {}).get("duration") or 0))
        rot = (st.get("tags", {}) or {}).get("rotate")
        if rot is None:
            for sd in st.get("side_data_list", []):
                if "rotation" in sd:
                    rot = sd["rotation"]; break
        if rot is not None and abs(int(rot)) % 180 == 90:
            w, h = h, w
        if w and h:
            return dur, w, h
    except Exception:
        pass
    return fallback


def _thumb(src: str, dst: str) -> str | None:
    """Кадр на ~1-й секунде как обложка — иначе fade-in даёт ЧЁРНОЕ превью.
    ponytail: фикс. 1s; если ролик короче/чёрный и там — оператору проверить глазами."""
    for ss in ("1", "0.5", "0"):
        subprocess.run(["ffmpeg", "-y", "-ss", ss, "-i", src, "-frames:v", "1",
                        "-vf", "scale=320:-2", "-q:v", "3", dst],
                       capture_output=True)
        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            return dst
    return None


async def fetch_meta(tg_ids: list[int]) -> dict[int, dict]:
    c = await asyncpg.connect(DB)
    rows = await c.fetch(
        "select tg_id, full_name, city, categories from creators where tg_id = any($1::bigint[])", tg_ids)
    await c.close()
    return {r["tg_id"]: dict(r) for r in rows}


async def collect_media(client, targets: set[str]) -> dict[str, dict]:
    """{tg: {'photo':[msg...], 'video':[msg...]}} — по порядку появления в группе."""
    grp = await client.get_entity(GROUP)
    msgs = [m async for m in client.iter_messages(grp, limit=None)]
    msgs.sort(key=lambda m: m.id)
    out: dict[str, dict] = {}
    mode = cur = None
    for m in msgs:
        t = m.message or ""
        hp, hw = _HDR_PHOTO.search(t), _HDR_WORKS.search(t)
        if hp or hw:
            tg = (hp or hw).group(1)
            mode = "photo" if hp else "video"
            cur = tg if tg in targets else None
        if cur is None:
            continue
        slot = out.setdefault(cur, {"photo": [], "video": []})
        if mode == "photo" and getattr(m, "photo", None):
            slot["photo"].append(m)
        elif mode == "video" and _is_video(m):
            slot["video"].append(m)
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="+", help="<tg_id>=<tilda_uid>")
    ap.add_argument("--dry", action="store_true", help="только инвентаризация + превью подписи, без публикации")
    ap.add_argument("--replace", action="store_true", help="удалить старый альбом из канала и перезалить")
    args = ap.parse_args()

    pairs = dict(p.split("=", 1) for p in args.pairs)  # tg -> uid
    tg_ids = [int(t) for t in pairs]
    meta = await fetch_meta(tg_ids)
    reg = json.load(open(REG)) if os.path.exists(REG) else {}

    api_id = int(os.environ["TG_API_ID"]); api_hash = os.environ["TG_API_HASH"]
    os.makedirs(f"{SP}/ch", exist_ok=True)
    async with TelegramClient("data/creators_user", api_id, api_hash) as client:
        media = await collect_media(client, set(pairs))
        try:
            ch = await client.get_entity(CHANNEL)
        except Exception as e:
            ch = None
            print(f"⚠️  канал @{CHANNEL} недоступен: {e}")

        for tg, uid in pairs.items():
            m = meta.get(int(tg))
            if not m:
                print(f"❌ id {tg}: нет в creators (БД)"); continue
            phs = media.get(tg, {}).get("photo", [])[:MAX_PHOTOS]
            vids = media.get(tg, {}).get("video", [])[:MAX_VIDEOS]
            cap = build_creator_card(m["full_name"], m["city"], m["categories"],
                                     portfolio_url=CARD_URL.format(uid=uid))
            print("=" * 70)
            print(f"id {tg} · {short_name(m['full_name'])} · фото={len(phs)} видео={len(vids)}")
            print(cap)
            if args.dry:
                continue
            if not ch:
                print("   ↳ пропуск: канал недоступен"); continue
            if tg in reg:
                if not args.replace:
                    print(f"⏭  id {tg}: уже в канале (msg {reg[tg]})"); continue
                old = reg[tg]; n = len(phs) + len(vids)
                await client.delete_messages(ch, list(range(old, old + n)))
                del reg[tg]; json.dump(reg, open(REG, "w"), ensure_ascii=False, indent=1)
                print(f"   ♻️  replace: удалён старый альбом msg {old}..{old + n - 1}")
            if not phs and not vids:
                print("   ↳ пропуск: нет медиа"); continue
            album, tmp = [], []
            for i, mm in enumerate(phs):
                p = f"{SP}/ch/{tg}_p{i}.jpg"; await client.download_media(mm, p); tmp.append(p)
                album.append(InputMediaUploadedPhoto(file=await client.upload_file(p)))
            for i, mm in enumerate(vids):
                p = f"{SP}/ch/{tg}_v{i}.mp4"; await client.download_media(mm, p); tmp.append(p)
                th = _thumb(p, f"{SP}/ch/{tg}_v{i}.jpg")
                if th:
                    tmp.append(th)
                dur, w, h = _probe(p, _vid_dims(mm))
                album.append(InputMediaUploadedDocument(
                    file=await client.upload_file(p),
                    thumb=await client.upload_file(th) if th else None,
                    mime_type="video/mp4",
                    attributes=[DocumentAttributeVideo(duration=dur, w=w, h=h, supports_streaming=True)]))
            try:
                sent = await client.send_file(ch, album, caption=cap, parse_mode="html")
            except FloodWaitError as e:
                print(f"   ⏳ FloodWait {e.seconds}s — жду…")
                await asyncio.sleep(e.seconds + 3)
                sent = await client.send_file(ch, album, caption=cap, parse_mode="html")
            mid = sent[0].id if isinstance(sent, list) else sent.id
            reg[tg] = mid
            json.dump(reg, open(REG, "w"), ensure_ascii=False, indent=1)
            for p in tmp:
                if os.path.exists(p):
                    os.remove(p)
            print(f"   ✅ опубликовано: msg {mid}", flush=True)
            await asyncio.sleep(4)  # ponytail: троттлинг, чтоб не ловить FloodWait на серии альбомов


if __name__ == "__main__":
    asyncio.run(main())
