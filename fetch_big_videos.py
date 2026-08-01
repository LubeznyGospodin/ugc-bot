"""
Догрузка КРУПНЫХ рилсов (>20МБ, Bot API их не отдаёт) через Telethon (MTProto качает любой размер).
Берёт видео из группы «Креаторы Packman Prod» под постом «🎬 Работы креатора … id <tg>»,
качает до 3 шт., льёт в Kinescope, пишет хэши в big_video_map.json (tg → [slug]).

Дальше хэши вешаются на карточки Тильды тем же store-API (attach в браузере).

Запуск:  python fetch_big_videos.py 344399147 1423762984
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import urllib.request

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

SP = "/private/tmp/claude-501/-Users-nastasyapopova-Desktop-ugc-bot-v2/86555545-d7d7-4b55-9291-d96df2873370/scratchpad"
GROUP = -1004313489669
TOK = os.environ["KINESCOPE_TOKEN"]
PROJECT = "6d8b24d4-2b50-4d2c-9be5-c1ff7b3b9b8d"
OUT = "/Users/nastasyapopova/Desktop/ugc_bot_v2/big_video_map.json"
MAX_PER = 3

_HDR_WORKS = re.compile(r"Работы креатора.*?id\s+(\d+)", re.S)
_HDR_PHOTO = re.compile(r"Фото креатора.*?id\s+(\d+)", re.S)


def kinescope_upload(path: str, title: str) -> str:
    data = open(path, "rb").read()
    req = urllib.request.Request(
        "https://uploader.kinescope.io/v2/video", data=data, method="POST",
        headers={"Authorization": "Bearer " + TOK, "X-Parent-ID": PROJECT,
                 "X-Video-Title": title, "Content-Type": "video/mp4"})
    out = json.load(urllib.request.urlopen(req, timeout=900))["data"]
    return out["play_link"].rstrip("/").rsplit("/", 1)[-1]


async def main() -> None:
    targets = set(sys.argv[1:]) or {"344399147", "1423762984"}
    api_id = int(os.environ["TG_API_ID"]); api_hash = os.environ["TG_API_HASH"]
    os.makedirs(f"{SP}/big", exist_ok=True)

    async with TelegramClient("data/creators_user", api_id, api_hash) as client:
        grp = await client.get_entity(GROUP)
        msgs = [m async for m in client.iter_messages(grp, limit=None)]
        msgs.sort(key=lambda m: m.id)  # по порядку: заголовок, потом его медиа

        # собрать видео-сообщения под «🎬 Работы» нужных креаторов
        by_tg: dict[str, list] = {}
        cur = None
        for m in msgs:
            t = m.message or ""
            hw = _HDR_WORKS.search(t)
            hp = _HDR_PHOTO.search(t)
            if hw:
                cur = hw.group(1) if hw.group(1) in targets else None
                continue
            if hp:
                cur = None  # пошли фото — не наш контекст
                continue
            is_video = bool(getattr(m, "video", None)) or (
                m.document and "video" in ((getattr(m.file, "mime_type", "") or "")))
            if cur and is_video:
                by_tg.setdefault(cur, []).append(m)

        result = json.load(open(OUT)) if os.path.exists(OUT) else {}
        for tg, vids in by_tg.items():
            hashes = []
            for i, m in enumerate(vids[:MAX_PER], 1):
                path = f"{SP}/big/{tg}_{i}.mp4"
                print(f"⬇️  id {tg} #{i}: качаю {round((m.file.size or 0)/1e6,1)}МБ…", flush=True)
                await client.download_media(m, path)
                print(f"⬆️  id {tg} #{i}: в Kinescope…", flush=True)
                hashes.append(kinescope_upload(path, f"creator {tg} #{i}"))
                os.remove(path)
            result[tg] = hashes
            json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=1)
            print(f"✅ id {tg}: {len(hashes)} видео → {hashes}", flush=True)

        print("готово:", {k: len(v) for k, v in result.items()})


if __name__ == "__main__":
    asyncio.run(main())
