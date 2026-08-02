"""Проставить в reach-таблицу реальную дату ПУБЛИКАЦИИ ролика.

Парсинг охватов завели 21.07, а креаторы публиковались с ~15.07 — поэтому в
колонке «Дата добавления» стояла дата попадания в трекинг, а не выхода поста.

Источники даты (все уже подключены): YouTube Data API, VK API (video.get),
ScrapeCreators (Instagram/TikTok/Threads). Telegram/Likee/Facebook пропускаем —
API не отдают дату, там останется что было.

    .venv/bin/python fix_pub_dates.py          # собрать и записать
    .venv/bin/python fix_pub_dates.py --dry    # только показать, без записи
"""

import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import requests

from singl_seed import ENV, SECRET, SHEET_ID, WEBHOOK, _grid_dump

MSK = timezone.utc  # даты приводим к МСК вручную (+3)
SC_KEY = ENV.get("SCRAPECREATORS_API_KEY", "")
VK_TOKEN = ENV.get("SINGL_VK_USER", "")
YT_KEY = subprocess.run(
    "railway variables --service spectacular-art --kv 2>/dev/null | grep '^YOUTUBE_API_KEY=' | cut -d= -f2",
    shell=True, capture_output=True, text=True).stdout.strip()


def _fmt(ts: float) -> str:
    """unix → ДД.ММ.ГГГГ по МСК."""
    return datetime.fromtimestamp(ts + 3 * 3600, tz=timezone.utc).strftime("%d.%m.%Y")


def _get(url: str, headers: dict | None = None, timeout: int = 45):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def yt_date(url: str) -> str | None:
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/)([\w-]{11})", url)
    if not m or not YT_KEY:
        return None
    d = _get(f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={m.group(1)}&key={YT_KEY}")
    items = d.get("items") or []
    if not items:
        return None
    pub = items[0]["snippet"]["publishedAt"]          # ISO UTC
    return (datetime.fromisoformat(pub.replace("Z", "+00:00"))
            .astimezone(timezone(__import__("datetime").timedelta(hours=3))).strftime("%d.%m.%Y"))


def vk_date(url: str) -> str | None:
    m = re.search(r"(?:clip|video)(-?\d+)_(\d+)", url)
    if not m or not VK_TOKEN:
        return None
    d = json.load(urllib.request.urlopen(
        "https://api.vk.com/method/video.get",
        data=urllib.parse.urlencode({"videos": f"{m.group(1)}_{m.group(2)}",
                                     "access_token": VK_TOKEN, "v": "5.199"}).encode(), timeout=45))
    items = (d.get("response") or {}).get("items") or []
    return _fmt(items[0]["date"]) if items and items[0].get("date") else None


def sc_date(url: str, platform: str) -> str | None:
    """Instagram/TikTok/Threads через ScrapeCreators (1 кредит на запрос)."""
    if not SC_KEY:
        return None
    ep = {"instagram": "/v1/instagram/post", "tiktok": "/v2/tiktok/video",
          "threads": "/v1/threads/post"}.get(platform)
    if not ep:
        return None
    d = _get(f"https://api.scrapecreators.com{ep}?url={urllib.parse.quote(url, safe='')}",
             headers={"x-api-key": SC_KEY})
    ts = None
    if platform == "instagram":
        ts = ((d.get("data") or {}).get("xdt_shortcode_media") or {}).get("taken_at_timestamp")
    elif platform == "tiktok":
        ts = (d.get("aweme_detail") or {}).get("create_time")
    elif platform == "threads":
        ts = (d.get("post") or {}).get("taken_at")
    return _fmt(float(ts)) if ts else None


def main() -> None:
    dry = "--dry" in sys.argv
    rows = [v for v in _grid_dump()[2:]
            if len(v) >= 7 and str(v[3]).strip().startswith("http")]
    # только креаторские: посевные строки датируются днём публикации при заливке
    rows = [r for r in rows
            if not (str(r[1]).startswith("Посев") or "посев" in str(r[6]).lower())]

    dates, misses, used = {}, [], 0
    for r in rows:
        url, platform = str(r[3]).strip(), str(r[2]).strip().lower()
        try:
            if platform == "youtube":
                d = yt_date(url)
            elif platform == "vk":
                d = vk_date(url)
            elif platform in ("instagram", "tiktok", "threads"):
                d = sc_date(url, platform)
                used += 1
                time.sleep(0.4)
            else:
                d = None            # telegram/likee/facebook — даты нет
        except Exception as e:  # noqa: BLE001
            print(f"  {platform}: {url[:50]} — {e}")
            d = None
        if d:
            dates[url] = d
        else:
            misses.append(f"{platform}: {url[:60]}")

    print(f"дат собрано: {len(dates)} из {len(rows)} (кредитов ScrapeCreators потрачено ~{used})")
    if misses:
        print(f"без даты осталось {len(misses)}:")
        for m in misses[:8]:
            print("   ", m)
    if dry or not dates:
        return
    resp = requests.post(WEBHOOK, json={"secret": SECRET, "action": "dates_write",
                                        "sheet_id": SHEET_ID, "dates": dates}, timeout=180)
    print("запись:", resp.status_code, resp.text[:120])


if __name__ == "__main__":
    main()
