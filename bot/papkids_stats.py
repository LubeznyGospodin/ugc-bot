"""PapKids: суточный сбор статистики, публикация дашборда и утренний отчёт.

Раз в сутки в 07:00 МСК (данные к 10:00 заведомо актуальны):
  1. papkids_push          — upload-post (IG/TT/YT) + добор IG через ScrapeCreators
                             + VK wall.get → лист «Посты» + журнал «Замеры»
  2. papkids_dash_publish  — свежий HTML-дашборд → публичный Selectel
  3. отчёт админам в телеграм

Раз в сутки, а не каждые 6 часов: добор Instagram стоит кредиты ScrapeCreators
(5 за прогон), а цифры за сутки всё равно нужны один раз утром.
"""
from __future__ import annotations

import asyncio
import datetime
import logging

logger = logging.getLogger(__name__)

MSK = datetime.timezone(datetime.timedelta(hours=3))
HOUR = 7                 # час сбора по МСК
RETRY = 30 * 60          # сбой (API/сеть) — повтор через полчаса, до 3 попыток


def _sleep_to_next_run() -> float:
    now = datetime.datetime.now(MSK)
    nxt = now.replace(hour=HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += datetime.timedelta(days=1)
    return (nxt - now).total_seconds()


def _run_once() -> str:
    """Синхронный прогон (вызывается в to_thread). → краткая сводка для лога."""
    import papkids_push as pp

    summary = pp.run()          # сбор → merge с архивом («Удалён») → запись → «Замеры»

    import papkids_dash_publish as pub

    pub.upload(pub.render(pub.build_data()))
    return summary + ", дашборд опубликован"


async def _send_report(bot) -> None:
    from bot.config import settings

    import papkids_push as pp

    text = await asyncio.to_thread(pp.report_text)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception as e:  # noqa: BLE001
            logger.warning("papkids отчёт не ушёл %s: %s", admin_id, e)


async def run_papkids_stats_loop(bot) -> None:
    while True:
        await asyncio.sleep(_sleep_to_next_run())
        for attempt in range(3):
            try:
                summary = await asyncio.to_thread(_run_once)
                logger.info("papkids_stats: %s", summary)
                await _send_report(bot)
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("papkids_stats error (попытка %s): %s", attempt + 1, e)
                await asyncio.sleep(RETRY)


if __name__ == "__main__":                     # самопроверка расписания
    s = _sleep_to_next_run()
    assert 0 < s <= 24 * 3600, s
    nxt = datetime.datetime.now(MSK) + datetime.timedelta(seconds=s)
    assert (nxt.hour, nxt.minute) == (HOUR, 0), nxt
    print("следующий сбор:", nxt.strftime("%d.%m %H:%M МСК"), f"(через {s / 3600:.1f} ч)")
