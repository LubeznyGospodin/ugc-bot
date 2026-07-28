"""
ffmpeg/ffprobe-хелперы для постинга видео в канал.

ТВЁРДОЕ ПРАВИЛО (см. docs/CHANNEL_POSTING.md): перезаливая работу как видео, ВСЕГДА
задавать обложку (не чёрную) и размеры (не квадрат). Иначе возвращаются баги, которые
уже чинились в channel_post.py — не повторять.

Требует ffmpeg на хосте (Railway: nixpacks.toml aptPkgs=["ffmpeg"]).
"""
from __future__ import annotations

import json
import subprocess


def probe_dims(path: str) -> tuple[int, int, int]:
    """(duration, w, h) — ДИСПЛЕЙНЫЕ размеры из файла, с учётом поворота (±90°→swap).
    Атрибуты исходного сообщения врут (часто 320×320/0) → берём с файла."""
    try:
        j = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration",
             "-of", "json", path], capture_output=True, text=True).stdout)
        st = (j.get("streams") or [{}])[0]
        w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        dur = int(float((j.get("format") or {}).get("duration") or 0))
        rot = (st.get("tags") or {}).get("rotate")
        if rot is None:
            for sd in st.get("side_data_list", []):
                if "rotation" in sd:
                    rot = sd["rotation"]
                    break
        if rot is not None and abs(int(rot)) % 180 == 90:
            w, h = h, w
        return dur, w, h
    except Exception:  # noqa: BLE001
        return 0, 0, 0


def make_thumb(path: str) -> bytes | None:
    """JPEG-байты кадра на ~1-й секунде (не чёрный fade-in). None если не вышло."""
    for ss in ("1", "0.5", "0"):
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", ss, "-i", path, "-frames:v", "1", "-vf", "scale=320:-2",
             "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    return None
