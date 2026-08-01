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
HOUR, MINUTE = 8, 0            # старт сбора по МСК
REPORT_H, REPORT_M = 8, 30     # во сколько отчёт падает в чат (сбор к этому моменту готов)
RETRY = 30 * 60                # сбой (API/сеть) — повтор через полчаса, до 3 попыток


def _seconds_to(hour: int, minute: int) -> float:
    """Секунд до ближайшего hour:minute по МСК (сегодня или завтра)."""
    now = datetime.datetime.now(MSK)
    nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += datetime.timedelta(days=1)
    return (nxt - now).total_seconds()


def _sleep_to_next_run() -> float:
    return _seconds_to(HOUR, MINUTE)


def _sleep_to_report() -> float:
    """Ждём 8:30 — но если сбор затянулся и время прошло, шлём сразу (не сутки ждать)."""
    now = datetime.datetime.now(MSK)
    target = now.replace(hour=REPORT_H, minute=REPORT_M, second=0, microsecond=0)
    return max(0.0, (target - now).total_seconds())


def _run_once() -> str:
    """Синхронный прогон (вызывается в to_thread). → краткая сводка для лога."""
    import papkids_push as pp

    summary = pp.run()          # сбор → merge с архивом («Удалён») → запись → «Замеры»

    import papkids_wb_api as wb
    import papkids_wb_queries as wq

    for name, fn in (("WB ключи", wq.run), ("WB карточки", wb.run)):
        try:                    # WB API упал — не рушим прогон, цифры останутся вчерашние
            summary += f", {name}: {fn()}"
        except Exception as e:  # noqa: BLE001
            logger.warning("papkids %s: %s", name, e)

    import papkids_dash_publish as pub

    pub.upload(pub.render(pub.build_data()))
    return summary + ", дашборд опубликован"


async def _send_report(bot) -> None:
    from bot.config import settings

    import papkids_push as pp

    text = await asyncio.to_thread(pp.report_text)      # HTML: жирный заголовок + цитата
    if settings.papkids_report_chat:            # чат-отчётов (тема — PAPKIDS_REPORT_TOPIC)
        kw = {"message_thread_id": int(settings.papkids_report_topic)} if settings.papkids_report_topic else {}
        try:
            await bot.send_message(int(settings.papkids_report_chat), text, parse_mode="HTML", **kw)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("papkids отчёт не ушёл в чат %s: %s", settings.papkids_report_chat, e)
    for admin_id in settings.admin_ids:         # запасной путь — админам в личку
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:  # noqa: BLE001
            logger.warning("papkids отчёт не ушёл %s: %s", admin_id, e)


async def run_papkids_stats_loop(bot) -> None:
    while True:
        await asyncio.sleep(_sleep_to_next_run())
        for attempt in range(3):
            try:
                summary = await asyncio.to_thread(_run_once)
                logger.info("papkids_stats: %s", summary)
                await asyncio.sleep(_sleep_to_report())     # цифры готовы — ждём 8:30
                await _send_report(bot)
                break
            except Exception as e:  # noqa: BLE001
                logger.warning("papkids_stats error (попытка %s): %s", attempt + 1, e)
                await asyncio.sleep(RETRY)


if __name__ == "__main__":                     # самопроверка расписания
    s = _sleep_to_next_run()
    assert 0 < s <= 24 * 3600, s
    nxt = datetime.datetime.now(MSK) + datetime.timedelta(seconds=s)
    assert (nxt.hour, nxt.minute) == (HOUR, MINUTE), nxt
    assert _sleep_to_report() <= 24 * 3600
    print("следующий сбор:", nxt.strftime("%d.%m %H:%M МСК"), f"(через {s / 3600:.1f} ч)",
          f"→ отчёт в {REPORT_H}:{REPORT_M:02d}")
