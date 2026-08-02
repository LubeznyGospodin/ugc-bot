#!/usr/bin/env python3
"""Ролики продакшена для главной сайта: канал @packmanprod → Selectel → data/reels.json.

Канал публичный, поэтому ни бота-админа, ни Telethon не нужно: посты и прямые
mp4 забираем из веб-превью t.me/s/. Права админа тут не помогли бы вовсе —
Bot API не отдаёт историю канала, только новые апдейты.

Что делает:
  1. Обходит превью канала постранично, собирает посты, хештеги и ссылки на mp4.
  2. Берёт выборку по форматам (лайфстайл, проходка, обзор, wow, 360),
     не больше двух роликов из одного поста — иначе веер крутит один товар.
  3. Жмёт до 720px без звука и снимает постер с первой секунды
     (первый кадр у видео часто чёрный — docs/CHANNEL_POSTING.md).
  4. Заливает в Selectel под reels/ и пишет packman_site/data/reels.json.

Запуск: .venv/bin/python site_reels.py [сколько роликов]

⚠️ Ролики тяжелее ~20 МБ веб-превью не отдаёт: ссылки на них в HTML нет.
Такие забирает `site_reels_big.py` через Telethon (сессия data/creators_user).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict

import boto3
from dotenv import load_dotenv

load_dotenv()

CHANNEL = "packmanprod"
BUCKET = "packman"
PUBLIC_BASE = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru"
SITE = os.path.expanduser("~/Desktop/packman_site")
TMP = "/tmp/packman-reels"
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://t.me/",
}

# хештег поста → формат съёмки; всё остальное в выборку не идёт
FORMATS = {
    "лайфстайл": "lifestyle",
    "видеообзор": "review",
    "видеопроходка": "walk",
    "wowвидео": "wow",
    "видео360": "r360",
    "ии_видео": "ai",
}
# сколько роликов каждого формата берём при limit=40
SHARE = {"lifestyle": 0.38, "walk": 0.20, "review": 0.17, "wow": 0.15, "r360": 0.05, "ai": 0.05}

s3 = boto3.client(
    "s3",
    endpoint_url="https://s3.ru-7.storage.selcloud.ru",
    aws_access_key_id=os.getenv("SELECTEL_S3_KEY"),
    aws_secret_access_key=os.getenv("SELECTEL_S3_SECRET"),
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")


def scan_channel() -> list[dict]:
    """Все посты канала из публичного превью, от старых к новым."""
    posts, before, seen = {}, None, set()
    for _ in range(60):
        url = f"https://t.me/s/{CHANNEL}" + (f"?before={before}" if before else "")
        html = fetch(url)
        chunks = re.findall(
            r'<div class="tgme_widget_message [\s\S]*?(?=<div class="tgme_widget_message |\Z)',
            html,
        )
        ids = []
        for c in chunks:
            m = re.search(rf'data-post="{CHANNEL}/(\d+)"', c)
            if not m:
                continue
            pid = int(m.group(1))
            ids.append(pid)
            if pid in posts:
                continue
            raw = (re.search(r"js-message_text[^>]*>([\s\S]*?)</div>", c) or ["", ""])[1]
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", raw)).strip()
            posts[pid] = {
                "id": pid,
                "text": text,
                "tags": re.findall(r"#([\wЀ-ӿ]+)", text),
                "mp4": sorted(set(re.findall(r'https://cdn\d+\.telesco\.pe/file/[^"?]+\.mp4[^"]*', c))),
            }
        if not ids or min(ids) in seen:
            break
        seen.add(min(ids))
        before = min(ids)
        time.sleep(0.4)
    return sorted(posts.values(), key=lambda p: p["id"])


def pick(posts: list[dict], limit: int) -> list[dict]:
    by = defaultdict(list)
    for p in posts:
        fmts = [FORMATS.get(t.lower().replace("ё", "е")) for t in p["tags"]]
        fmts = [f for f in fmts if f]
        if not fmts or not p["mp4"]:
            continue
        for url in p["mp4"]:
            by[fmts[0]].append({"fmt": fmts[0], "post": p["id"], "text": p["text"], "url": url})

    out = []
    for fmt, share in SHARE.items():
        need = max(1, round(limit * share))
        per_post: dict[int, int] = defaultdict(int)
        for v in by.get(fmt, []):
            if sum(1 for x in out if x["fmt"] == fmt) >= need:
                break
            if per_post[v["post"]] >= 2:
                continue
            per_post[v["post"]] += 1
            out.append(v)
    return out


def prepare(v: dict) -> tuple[str, str, str]:
    """Скачать, ужать, снять постер. Возвращает (имя, путь к mp4, путь к jpg)."""
    os.makedirs(TMP, exist_ok=True)
    name = f"{v['fmt']}-{v['post']}-{hashlib.sha1(v['url'].split('?')[0].encode()).hexdigest()[:6]}"
    raw, mp4, jpg = f"{TMP}/{name}.raw.mp4", f"{TMP}/{name}.mp4", f"{TMP}/{name}.jpg"
    if not os.path.exists(raw):
        data = urllib.request.urlopen(urllib.request.Request(v["url"], headers=UA), timeout=120).read()
        if len(data) < 10_000:
            raise RuntimeError(f"пустой файл: {v['url']}")
        open(raw, "wb").write(data)
    if not os.path.exists(mp4):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", raw, "-an", "-vf", "scale=720:-2",
             "-c:v", "libx264", "-preset", "slow", "-crf", "27", "-movflags", "+faststart", mp4],
            check=True,
        )
    if not os.path.exists(jpg):
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", mp4, "-frames:v", "1",
             "-vf", "scale=540:-2", "-q:v", "4", jpg],
            check=True,
        )
    return name, mp4, jpg


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    posts = scan_channel()
    chosen = pick(posts, limit)
    print(f"постов: {len(posts)} · роликов отобрано: {len(chosen)}")

    out = []
    for i, v in enumerate(chosen, 1):
        name, mp4, jpg = prepare(v)
        for path, ext, ctype in ((mp4, "mp4", "video/mp4"), (jpg, "jpg", "image/jpeg")):
            s3.upload_file(path, BUCKET, f"reels/{name}.{ext}", ExtraArgs={"ContentType": ctype})
        out.append({
            "fmt": v["fmt"],
            "post": v["post"],
            "text": v["text"],
            "src": f"{PUBLIC_BASE}/reels/{name}.mp4",
            "poster": f"{PUBLIC_BASE}/reels/{name}.jpg",
        })
        if i % 10 == 0:
            print(f"  {i}…", flush=True)

    with open(f"{SITE}/data/reels.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"✓ {SITE}/data/reels.json — {len(out)} роликов")
    print("  дальше: обновить FAN_SLOTS в lib/works.ts, если состав менялся")


if __name__ == "__main__":
    main()
