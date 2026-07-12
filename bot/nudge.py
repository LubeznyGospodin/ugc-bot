"""
Пуш-напоминание тем, кто зашёл в бот (/start), но не заполнил анкету.

- Авто-режим: фоновый луп раз в N минут находит заходы старше 2ч без регистрации,
  которым ещё не слали, шлёт текст и помечает — СТРОГО один раз на человека.
- Бэклог (те, кто зашёл ДО запуска фичи) авто-луп не трогает: они помечены «эпохой»
  на старте (grandfather) и уходят отдельно, вручную — /nudge_backlog в админке.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from bot.keyboards import nudge_keyboard
from bot.utils.db_helpers import backlog_unregistered, due_nudges, mark_nudged

logger = logging.getLogger(__name__)

# Текст пуша — согласован с заказчиком дословно.
NUDGE_TEXT = (
    "Не переставай двигаться! Продолжай путь, и откроешь бесплатный доступ к лучшим "
    "коммерческим заказам брендов. Заполни анкету и выбирай!"
)


async def _send_one(bot: Bot, tg_id: int) -> bool:
    """Шлёт пуш одному. Отмечаем mark_nudged в ЛЮБОМ случае (даже если заблокировал
    бота) — чтобы не долбить повторно. Возвращает True, если доставлено."""
    ok = False
    try:
        await bot.send_message(tg_id, NUDGE_TEXT, reply_markup=nudge_keyboard())
        ok = True
    except Exception as e:  # noqa: BLE001 — заблокировал/удалил чат и т.п.
        logger.info("nudge to %s not delivered: %s", tg_id, e)
    finally:
        await mark_nudged(tg_id)
    return ok


async def run_nudge_loop(bot: Bot, interval: int = 600) -> None:
    """Раз в interval сек досылает пуш заходам, у кого прошло ≥2ч и нет регистрации."""
    # Небольшая пауза на старте — дать синку/сидингу подняться.
    await asyncio.sleep(60)
    while True:
        try:
            ids = await due_nudges()
            for tg_id in ids:
                await _send_one(bot, tg_id)
                await asyncio.sleep(0.05)  # лимиты Telegram
            if ids:
                logger.info("nudge loop: отправлено %s напоминаний", len(ids))
        except Exception as e:  # noqa: BLE001 — луп не должен падать
            logger.warning("nudge loop error: %s", e)
        await asyncio.sleep(interval)


async def send_backlog(bot: Bot) -> tuple[int, int]:
    """Ручная разовая рассылка по бэклогу (заходы до запуска фичи, без регистрации).
    После отправки каждый помечается mark_nudged → повторный вызов их уже не захватит."""
    ids = await backlog_unregistered()
    sent = 0
    for tg_id in ids:
        if await _send_one(bot, tg_id):
            sent += 1
        await asyncio.sleep(0.05)
    return sent, len(ids) - sent
