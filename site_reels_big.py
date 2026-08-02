#!/usr/bin/env python3
"""Тяжёлые ролики канала @packmanprod, которых нет в веб-превью.

Веб отдаёт прямые mp4 только для лёгких видео: всё, что тяжелее ~20 МБ, в HTML
превью не попадает вовсе. Такие забираем через Telethon пользовательской
сессией (`data/creators_user`, та же, что у channel_post.py). Бот тут не
поможет: Bot API не отдаёт историю канала и режет файлы на 20 МБ.

Порядок: `site_reels.py` собирает лёгкую часть, этот скрипт дополняет её
тяжёлой. Оба пишут в один `packman_site/data/reels.json` и один префикс
`reels/` в Selectel.

Качаем по одному и сразу жмём, удаляя оригинал: сорок роликов по 40 МБ — это
почти два гигабайта, а места на диске обычно нет.

Запуск: .venv/bin/python site_reels_big.py [сколько добрать]
"""

import asyncio
import json
import os
import re
import subprocess
import sys

import boto3
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

CHANNEL = "packmanprod"
BUCKET = "packman"
PUBLIC_BASE = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru"
SITE = os.path.expanduser("~/Desktop/packman_site")
TMP = "/tmp/packman-reels-big"
SESSION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data/creators_user")

FORMATS = {
    "лайфстайл": "lifestyle",
    "видеообзор": "review",
    "видеопроходка": "walk",
    "wowвидео": "wow",
    "видео360": "r360",
    "ии_видео": "ai",
}

s3 = boto3.client(
    "s3",
    endpoint_url="https://s3.ru-7.storage.selcloud.ru",
    aws_access_key_id=os.getenv("SELECTEL_S3_KEY"),
    aws_secret_access_key=os.getenv("SELECTEL_S3_SECRET"),
)


def fmt_of(text: str) -> str:
    """Формат съёмки из хештега. У постов из клиента разметка жирного (**), чистим."""
    for tag in re.findall(r"#([\wЀ-ӿ]+)", (text or "").replace("**", "")):
        f = FORMATS.get(tag.lower().replace("ё", "е"))
        if f:
            return f
    return "misc"


def convert(raw: str, mp4: str, jpg: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", raw, "-an", "-vf", "scale=720:-2",
         "-c:v", "libx264", "-preset", "slow", "-crf", "27", "-movflags", "+faststart", mp4],
        check=True,
    )
    # постер с первой секунды: первый кадр часто чёрный (docs/CHANNEL_POSTING.md)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", mp4, "-frames:v", "1",
         "-vf", "scale=540:-2", "-q:v", "4", jpg],
        check=True,
    )


async def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    os.makedirs(TMP, exist_ok=True)

    path = f"{SITE}/data/reels.json"
    reels = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else []
    have_posts = {r["post"] for r in reels}

    async with TelegramClient(SESSION, int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as c:
        ch = await c.get_entity(CHANNEL)
        added, caption = 0, {}
        async for m in c.iter_messages(ch, reverse=True):
            if m.text:
                caption[m.grouped_id or m.id] = m.text
            if added >= limit:
                break
            if not m.video or m.id in have_posts:
                continue
            if m.file.size < 20 * 1024 * 1024:
                continue  # лёгкие уже забрал site_reels.py

            text = caption.get(m.grouped_id or m.id, m.text or "")
            name = f"{fmt_of(text)}-{m.id}-tl"
            raw, mp4, jpg = f"{TMP}/{m.id}.src", f"{TMP}/{name}.mp4", f"{TMP}/{name}.jpg"
            if not os.path.exists(mp4):
                await c.download_media(m, raw)
                convert(raw, mp4, jpg)
                os.remove(raw)
            for p, ext, ctype in ((mp4, "mp4", "video/mp4"), (jpg, "jpg", "image/jpeg")):
                s3.upload_file(p, BUCKET, f"reels/{name}.{ext}", ExtraArgs={"ContentType": ctype})
            reels.append({
                "fmt": fmt_of(text),
                "post": m.id,
                "text": re.sub(r"\s+", " ", text.replace("**", "")).strip(),
                "src": f"{PUBLIC_BASE}/reels/{name}.mp4",
                "poster": f"{PUBLIC_BASE}/reels/{name}.jpg",
            })
            added += 1
            print(f"  {added}. {name}", flush=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(reels, f, ensure_ascii=False, indent=1)
    print(f"✓ добавлено {added}, всего роликов {len(reels)}")


if __name__ == "__main__":
    asyncio.run(main())
