"""Планировщик посева «Сингл» для Railway — замена launchd-агентов на маке.

Один долгоживущий процесс: каждую минуту смотрит на часы (МСК) и запускает
задачу, если её слот наступил и сегодня она ещё не отрабатывала.

Расписание (МСК):
  06:00  ingest  — новые ролики креаторов из таблицы → src/ (и 15:30)
  07:00  wave    — план дня + уникализация + постановка в отложку
  09:30  scan    — просмотры + алерты (и 12:30, 15:00, 17:30, 22:30)
  10:10  daily   — утренний сводный отчёт в ТГ
  13:00  links   — ссылки свежевышедших постов → база охвата (и 17:00, 21:00, 23:45)
  20:00  stats   — вечерняя сводка по посеву
  03:00  clean   — чистка out/ (копии старше 2 суток) — volume не резиновый

Данные — на volume (env SEED_DIR=/data). Состояние прогонов, чтобы не гонять
задачу дважды за день, лежит в том же state.json (ключ `worker_done`).
"""

import asyncio
import logging
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import singl_seed as S

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("seed_worker")

MSK = timezone(timedelta(hours=3))

# (час, минута, имя задачи)
SCHEDULE = [
    (3, 0, "clean"),
    (6, 0, "ingest"),   # ДО волны: свежие ролики креаторов должны попасть в план дня
    (7, 0, "wave"),
    (9, 30, "scan"),
    (10, 10, "daily"),
    (12, 30, "scan"),
    (13, 0, "links"),
    (15, 0, "scan"),
    (15, 30, "ingest"),
    (17, 0, "links"),
    (17, 30, "scan"),
    (20, 0, "stats"),
    (21, 0, "links"),
    (22, 30, "scan"),
    (23, 45, "links"),
]

# сколько ждём после пропущенного слота (мак спал / деплой шёл) — добираем задачу
CATCH_UP_MIN = 90


def clean() -> None:
    """Удалить уникализированные копии старше 2 суток (volume ограничен)."""
    cutoff = time.time() - 2 * 86400
    freed = 0
    for f in (S.SEED_DIR / "out").glob("*.mp4"):
        if f.stat().st_mtime < cutoff:
            freed += f.stat().st_size
            f.unlink()
    log.info("clean: освобождено %.1f МБ", freed / 1e6)


TASKS = {
    "wave": S.wave,
    "ingest": S.ingest,
    "links": S.links,
    "stats": S.stats,
    "scan": lambda: S.stats(quiet=True),
    "daily": S.daily,
    "clean": clean,
}


def _bootstrap_state() -> None:
    """Первый старт на пустом volume — поднять state.json из репо, если он там есть."""
    if S.STATE.exists():
        return
    seed = Path(__file__).parent / "seed_state_bootstrap.json"
    if seed.exists():
        S.STATE.write_text(seed.read_text())
        log.info("state.json поднят из bootstrap (%d Б)", S.STATE.stat().st_size)


def _due(now: datetime, done: list[str]) -> list[str]:
    """Задачи, чьё время наступило (с добором пропущенных) и которые сегодня не шли."""
    out = []
    for h, m, name in SCHEDULE:
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        key = f"{name}@{h:02d}:{m:02d}"
        if slot <= now <= slot + timedelta(minutes=CATCH_UP_MIN) and key not in done:
            out.append((key, name))
    return out


async def main() -> None:
    _bootstrap_state()
    have = len(list((S.SEED_DIR / "src").glob("*.mp4")))
    log.info("seed_worker запущен, SEED_DIR=%s, исходников=%d", S.SEED_DIR, have)
    if have < 10:  # пустой/новый volume — наполняем базу сразу, не ждём слота
        log.info("исходников мало — стартовый ingest")
        try:
            await asyncio.to_thread(S.ingest)
        except Exception:  # noqa: BLE001
            log.error("стартовый ingest:\n%s", traceback.format_exc()[:800])
    while True:
        try:
            now = datetime.now(MSK).replace(tzinfo=None)
            today = now.strftime("%Y-%m-%d")
            st = S.load_state()
            done_map = st.setdefault("worker_done", {})
            done = done_map.setdefault(today, [])
            for key, name in _due(now, done):
                log.info("→ %s", key)
                try:
                    # блокирующие задачи (ffmpeg/сеть) — в отдельный поток,
                    # чтобы цикл планировщика оставался живым
                    await asyncio.to_thread(TASKS[name])
                    log.info("✓ %s", key)
                except Exception:  # noqa: BLE001
                    log.error("✗ %s:\n%s", key, traceback.format_exc()[:1500])
                finally:  # даже упавшую не гоняем весь день по кругу
                    st = S.load_state()
                    st.setdefault("worker_done", {}).setdefault(today, []).append(key)
                    for old in [d for d in st["worker_done"] if d < today]:
                        st["worker_done"].pop(old)  # мусор за прошлые дни
                    S.save_state(st)
        except Exception:  # noqa: BLE001
            log.error("цикл планировщика:\n%s", traceback.format_exc()[:800])
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
