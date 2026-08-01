#!/usr/bin/env python3
"""upload-post: сбор статистики по аккаунту (в т.ч. РУЧНЫЕ посты).

Проверено на бою (27.07.2026): reach по ручным постам приходит. Ключ — брать
нативный platform_post_id из /media, а НЕ выводить из shortcode: у Graph API
своя нумерация id (shortcode-декод даёт HTTP 500).

Поток: /media (все посты аккаунта: id + caption с #WW + permalink) →
post-analytics по каждому id → метрики (reach/views/likes/saves...).

Ключ из UPLOAD_POST_API_KEY (env или ./.env). В чат ключ не нужен.

    python papkids_uploadpost.py media instagram Test          # список постов (1 вызов)
    python papkids_uploadpost.py stats instagram Test 5        # +метрики по 5 постам (1+5 вызовов)
    python papkids_uploadpost.py --selftest                    # офлайн
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request

BASE = "https://api.upload-post.com/api/uploadposts"
_WW = re.compile(r"#?(WW\d+)")


def _key() -> str:
    k = os.getenv("UPLOAD_POST_API_KEY", "").strip()
    if not k and os.path.exists(".env"):
        for ln in open(".env", encoding="utf-8"):
            if ln.startswith("UPLOAD_POST_API_KEY="):
                k = ln.split("=", 1)[1].strip().strip('"').strip("'")
    return k


def _get(path: str, params: dict) -> dict:
    key = _key()
    if not key:
        raise SystemExit("нет UPLOAD_POST_API_KEY (положи в .env)")
    url = f"{BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Apikey {key}"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)


def ww(caption: str) -> str:
    m = _WW.search(caption or "")
    return m.group(1) if m else ""


def media(user: str, platform: str) -> list[dict]:
    """Все посты аккаунта: [{id, permalink, caption, timestamp, ww}]."""
    d = _get("/media", {"user": user, "platform": platform})
    out = []
    for m in d.get("media", []):
        cap = m.get("caption", "")
        out.append({"id": m.get("id"), "permalink": m.get("permalink"),
                    "ts": m.get("timestamp"), "ww": ww(cap), "caption": cap[:60]})
    return out


def analytics(user: str, platform: str, pid: str) -> dict:
    d = _get("/post-analytics", {"platform_post_id": pid, "platform": platform, "user": user})
    b = (d.get("platforms") or {}).get(platform, d)
    return b.get("post_metrics") or b


def selftest() -> None:
    assert ww("формат #WW645447 текст") == "WW645447"
    assert ww("без артикула") == ""
    assert ww("WW649527 без решётки") == "WW649527"
    print("selftest OK")


def main(argv: list[str]) -> None:
    if "--selftest" in argv:
        selftest()
        return
    if len(argv) < 3:
        print(__doc__)
        return
    cmd, platform, user = argv[0], argv[1], argv[2]
    posts = media(user, platform)
    if cmd == "media":
        for p in posts:
            print(f"{p['ts']}  {p['id']}  #{p['ww'] or '—'}  {p['permalink']}")
        print(f"\n{len(posts)} постов")
    elif cmd == "stats":
        n = int(argv[3]) if len(argv) > 3 else 5
        for p in posts[:n]:
            m = analytics(user, platform, p["id"])
            print(f"{p['permalink']}  #{p['ww'] or '—'}  "
                  f"reach={m.get('reach')} views={m.get('views')} "
                  f"likes={m.get('likes')} saves={m.get('saves')}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
