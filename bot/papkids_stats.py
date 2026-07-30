"""PapKids: серверный цикл сбора статистики и публикации дашборда.

Каждые 6 часов (в потоке, чтобы не блокировать бота):
  1. papkids_push      — upload-post (IG/TT/YT) + VK wall.get → лист «Посты» + журнал «Замеры»
  2. papkids_dash_publish — свежий HTML-дашборд → публичный Selectel

Заменяет локальный launchd com.papkids.collect (Мак больше не нужен).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

INTERVAL = 6 * 3600
STARTUP_DELAY = 240  # даём боту подняться, не толкаемся с другими лупами на старте


def _run_once() -> str:
    """Синхронный прогон (вызывается в to_thread). → краткая сводка для лога."""
    import papkids_push as pp

    summary = pp.run()          # сбор → merge с архивом («Удалён») → запись → «Замеры»

    import papkids_dash_publish as pub

    pub.upload(pub.render(pub.build_data()))
    return summary + ", дашборд опубликован"


async def run_papkids_stats_loop(bot) -> None:
    await asyncio.sleep(STARTUP_DELAY)
    while True:
        try:
            summary = await asyncio.to_thread(_run_once)
            logger.info("papkids_stats: %s", summary)
        except Exception as e:  # noqa: BLE001
            logger.warning("papkids_stats error: %s", e)
        await asyncio.sleep(INTERVAL)
