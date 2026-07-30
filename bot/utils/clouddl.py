"""
Забор медиа из публичных облачных ссылок (Яндекс.Диск).

Зачем: часть креаторов вместо загрузки файлов в бота присылает ссылку на папку
(«вот тут все фото и видео»). Раньше такие анкеты оставались без медиа — карточку
в канал собрать было нечем. Теперь тянем файлы по публичному API (без токена).

API: GET /v1/disk/public/resources?public_key=<url>       — список файлов
     GET /v1/disk/public/resources/download?public_key=…&path=… — прямая ссылка
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
UA = {"User-Agent": "Mozilla/5.0 (packman-bot)"}

# Ссылка на Яндекс.Диск в свободном тексте анкеты.
YADISK_RE = re.compile(r"https?://(?:disk\.)?yandex\.[a-z]+/[di]/[\w-]+", re.I)


def find_yadisk(text: str | None) -> str | None:
    m = YADISK_RE.search(text or "")
    return m.group(0) if m else None


def _get(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers=UA)
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def list_public(public_key: str, limit: int = 100) -> list[dict]:
    """Плоский список файлов публичной ссылки (папка или одиночный файл)."""
    r = _get(f"{API}?" + urllib.parse.urlencode({"public_key": public_key, "limit": limit}))
    if r.get("type") == "file":
        return [r]
    return [i for i in (r.get("_embedded") or {}).get("items", []) if i.get("type") == "file"]


def download(public_key: str, path: str, timeout: int = 180) -> bytes:
    """Скачать файл из публичной папки по его path (например «/IMG_2258.JPG»)."""
    href = _get(
        f"{API}/download?" + urllib.parse.urlencode({"public_key": public_key, "path": path})
    )["href"]
    return urllib.request.urlopen(urllib.request.Request(href, headers=UA), timeout=timeout).read()


def split_media(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """(фото, видео) по mime_type; прочее отбрасываем."""
    photos = [i for i in items if (i.get("mime_type") or "").startswith("image/")]
    videos = [i for i in items if (i.get("mime_type") or "").startswith("video/")]
    return photos, videos
