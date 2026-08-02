"""Ядро уникализации: строит рандомизированные ffmpeg-команды.

Два слоя на каждую копию:
1. Пиксельный — микрокроп, поворот, цветовой джиттер, зерно, новые метаданные.
2. Монтажный — замедление/ускорение 2-5%, подрезка краёв, медленный дрейф кадра
   (Ken Burns), виньетка, другой CRF/битрейт.

Параметры каждой копии рандомятся независимо, так что копии одного ролика
отличаются и от оригинала, и друг от друга.

CLI для локального прогона:
    python uniquify.py input.mp4 output_dir 3
"""

import asyncio
import json
import math
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


class ProbeError(Exception):
    pass


def probe(src: str) -> dict:
    """Читает параметры ролика через ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(src),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise ProbeError(f"ffprobe не смог прочитать файл: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout)

    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ProbeError("В файле нет видеодорожки")
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)

    num, _, den = video["r_frame_rate"].partition("/")
    fps = float(num) / (float(den or 1) or 1)  # у фото бывает 0/0

    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": fps,
        "duration": float(info["format"].get("duration", 0)),
        "sample_rate": int(audio["sample_rate"]) if audio else None,
    }


def _rand_creation_time(rnd: random.Random) -> str:
    dt = datetime.now(timezone.utc) - timedelta(
        days=rnd.randint(1, 30), hours=rnd.randint(0, 23), minutes=rnd.randint(0, 59)
    )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# сила уникализации: множитель всех искажений
PRESETS = {"light": 0.6, "medium": 1.0, "strong": 1.6}


def build_command(src: str, dst: str, meta: dict, seed: int | None = None,
                  preset: str = "medium", mirror: bool = False,
                  max_quality: bool = False) -> list[str]:
    """Собирает ffmpeg-команду с уникальным набором искажений.

    max_quality=True — без потери качества: держим исходное разрешение и fps,
    почти визуально-lossless CRF. Для посева (алгоритмы площадок режут охват
    низкому разрешению); дороже по CPU, поэтому не дефолт.
    """
    rnd = random.Random(seed)
    k = PRESETS.get(preset, 1.0)
    w, h = meta["width"], meta["height"]
    dur = meta["duration"]

    # кап выхода: >1080p жрёт память на хостинге (OOM), площадки всё равно пережмут
    # Даже в max_quality держим потолок Full HD: площадки всё равно отдают ≤1080p,
    # а 4K-копия весит сотни МБ (переполняли том) и кодируется в разы дольше.
    out_w, out_h = w, h
    if max(w, h) > 1920:
        f = 1920 / max(w, h)
        out_w, out_h = int(w * f) // 2 * 2, int(h * f) // 2 * 2

    # --- монтажный слой ---
    delta = min(rnd.uniform(0.02, 0.05) * k, 0.12)
    speed = 1 + delta * rnd.choice([-1, 1])
    head_trim = rnd.uniform(0.15, 0.45) * k if dur > 3 else 0.0
    tail_trim = rnd.uniform(0.1, 0.4) * k if dur > 3 else 0.0
    out_dur = max(dur - head_trim - tail_trim, 0.5)

    # --- пиксельный слой ---
    angle = rnd.uniform(0.3, 0.6) * k * rnd.choice([-1, 1])   # градусы
    # rotate оставляет холст того же размера — по краям чёрные клинья глубиной
    # ~ (сторона/2)*sin(a); кроп обязан их перекрыть с запасом на джиттер+дрейф
    wedge_x = math.ceil(h / 2 * math.sin(math.radians(abs(angle)))) + 2
    wedge_y = math.ceil(w / 2 * math.sin(math.radians(abs(angle)))) + 2
    jitter, drift_total = 4, 3                             # px
    margin_x = max(wedge_x + jitter + drift_total, int(w * 0.006))
    margin_y = max(wedge_y + jitter + drift_total, int(h * 0.006))
    cw, ch = (w - 2 * margin_x) // 2 * 2, (h - 2 * margin_y) // 2 * 2
    jx, jy = rnd.randint(-jitter, jitter), rnd.randint(-jitter, jitter)
    # дрейф кадра (Ken Burns): суммарно не больше drift_total px за весь ролик
    dx = rnd.uniform(1.0, drift_total) / max(out_dur, 1) * rnd.choice([-1, 1])
    dy = rnd.uniform(1.0, drift_total) / max(out_dur, 1) * rnd.choice([-1, 1])

    brightness = rnd.uniform(-0.025, 0.025) * k
    contrast = 1 + rnd.uniform(-0.03, 0.04) * k
    saturation = 1 + rnd.uniform(-0.05, 0.06) * k
    gamma = 1 + rnd.uniform(-0.03, 0.03) * k
    hue_shift = rnd.uniform(-2.5, 2.5) * k
    grain = max(int(rnd.randint(2, 5) * k), 1)

    vf_parts = (["hflip"] if mirror else []) + [
        f"rotate={angle}*PI/180:bilinear=1:fillcolor=black",
        (
            f"crop={cw}:{ch}"
            f":x='clip((iw-ow)/2+{jx}+{dx:.2f}*t,0,iw-ow)'"
            f":y='clip((ih-oh)/2+{jy}+{dy:.2f}*t,0,ih-oh)'"
        ),
        f"scale={out_w}:{out_h}:flags=bicubic",
        f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}"
        f":saturation={saturation:.4f}:gamma={gamma:.4f}",
        f"hue=h={hue_shift:.2f}",
        f"noise=alls={grain}:allf=t+u",
    ]
    if rnd.random() < 0.5:
        vf_parts.append(f"vignette=PI/{rnd.uniform(7.0, 9.0):.2f}")
    vf_parts.append(f"setpts=PTS/{speed:.5f}")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if head_trim:
        cmd += ["-ss", f"{head_trim:.3f}"]
    cmd += ["-i", str(src)]
    if tail_trim:
        cmd += ["-t", f"{out_dur:.3f}"]
    cmd += ["-vf", ",".join(vf_parts)]

    if meta["sample_rate"]:
        sr = meta["sample_rate"]
        pitch = rnd.uniform(0.998, 1.002)
        volume = rnd.uniform(0.96, 1.03)
        # asetrate меняет и темп: компенсируем в atempo, чтобы не уплыла синхра
        af = (
            f"asetrate={sr}*{pitch:.5f},aresample={sr},"
            f"atempo={speed / pitch:.5f},volume={volume:.3f}"
        )
        a_br = rnd.choice(["192k", "256k"]) if max_quality else rnd.choice(["128k", "160k", "192k"])
        cmd += ["-af", af, "-c:a", "aac", "-b:a", a_br]
    else:
        cmd += ["-an"]

    # кап fps 30 (60fps вдвое дороже по памяти/времени, площадкам не нужен)
    fps_out = meta["fps"] if max_quality else min(meta["fps"], 30.0)
    # strong: форсим смежный fps (30 -> 29.97) — ещё один сдвиг тайминга
    if k > 1 and abs(fps_out - 30) < 0.5:
        fps_out = rnd.choice([29.97, 30.0])
    cmd += [
        "-c:v", "libx264",
        # preset влияет на РАЗМЕР файла, а не на картинку при фиксированном CRF:
        # medium даёт то же качество втрое быстрее slow (важно для пачек копий)
        "-preset", "medium" if max_quality else "veryfast",
        "-crf", str(rnd.randint(16, 18) if max_quality else rnd.randint(20, 24)),
        "-pix_fmt", "yuv420p",
        "-r", f"{fps_out:.3f}",
        "-map_metadata", "-1",
        "-metadata", f"creation_time={_rand_creation_time(rnd)}",
        "-movflags", "+faststart",
        str(dst),
    ]
    return cmd


def build_image_command(src: str, dst: str, meta: dict, seed: int | None = None,
                        preset: str = "medium", mirror: bool = False) -> list[str]:
    """То же для фото: кроп, поворот, цвет, зерно, чистка метаданных."""
    rnd = random.Random(seed)
    k = PRESETS.get(preset, 1.0)
    w, h = meta["width"], meta["height"]

    angle = rnd.uniform(0.3, 0.6) * k * rnd.choice([-1, 1])
    wedge_x = math.ceil(h / 2 * math.sin(math.radians(abs(angle)))) + 2
    wedge_y = math.ceil(w / 2 * math.sin(math.radians(abs(angle)))) + 2
    margin_x = max(wedge_x + 4, int(w * 0.006))
    margin_y = max(wedge_y + 4, int(h * 0.006))
    cw, ch = (w - 2 * margin_x) // 2 * 2, (h - 2 * margin_y) // 2 * 2
    jx, jy = rnd.randint(-4, 4), rnd.randint(-4, 4)

    vf_parts = (["hflip"] if mirror else []) + [
        f"rotate={angle}*PI/180:bilinear=1:fillcolor=black",
        f"crop={cw}:{ch}:{(w - cw) // 2 + jx}:{(h - ch) // 2 + jy}",
        f"scale={w}:{h}:flags=bicubic",
        f"eq=brightness={rnd.uniform(-0.025, 0.025) * k:.4f}"
        f":contrast={1 + rnd.uniform(-0.03, 0.04) * k:.4f}"
        f":saturation={1 + rnd.uniform(-0.05, 0.06) * k:.4f}"
        f":gamma={1 + rnd.uniform(-0.03, 0.03) * k:.4f}",
        f"hue=h={rnd.uniform(-2.5, 2.5) * k:.2f}",
        f"noise=alls={max(int(rnd.randint(2, 5) * k), 1)}:allf=u",
    ]
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-vf", ",".join(vf_parts),
        "-frames:v", "1",
        "-q:v", str(rnd.randint(2, 4)),
        "-map_metadata", "-1",
        str(dst),
    ]


async def _run_ffmpeg(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()[:400]
        if not err and proc.returncode < 0:
            err = "процесс убит системой (вероятно, не хватило памяти)"
        raise RuntimeError(f"ffmpeg упал (код {proc.returncode}): {err}")


async def uniquify(src: str, dst: str, meta: dict | None = None,
                   preset: str = "medium", mirror: bool = False,
                   max_quality: bool = False) -> None:
    """Делает одну уникализированную копию src -> dst."""
    if meta is None:
        meta = probe(src)
    await _run_ffmpeg(build_command(src, dst, meta, preset=preset, mirror=mirror,
                                    max_quality=max_quality))


async def uniquify_image(src: str, dst: str, meta: dict | None = None,
                         preset: str = "medium", mirror: bool = False) -> None:
    """Уникализированная копия фото src -> dst (.jpg)."""
    if meta is None:
        meta = probe(src)
    await _run_ffmpeg(build_image_command(src, dst, meta, preset=preset, mirror=mirror))


def main() -> None:
    if len(sys.argv) != 4:
        print("usage: python uniquify.py input.mp4 output_dir N")
        sys.exit(1)
    src, out_dir, n = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = probe(str(src))
    print(f"{src.name}: {meta['width']}x{meta['height']} @{meta['fps']:.2f}fps, {meta['duration']:.1f}s")
    for i in range(1, n + 1):
        dst = out_dir / f"{src.stem}_u{i}.mp4"
        asyncio.run(uniquify(str(src), str(dst), meta))
        print(f"  -> {dst}")


if __name__ == "__main__":
    main()
