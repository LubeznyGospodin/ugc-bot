"""
Эксплуатация: (1) пинг внешнего монитора (dead-man's-switch — алерт, если хостинг встал),
(2) контроль оплаты хостинга — напоминания админам за 3 дня и в день оплаты (и далее
ежедневно, пока не отметят /paid).

Дата оплаты и отметка «оплачено» хранятся в AppState (ключи hosting_due / hosting_paid_for
/ hosting_last_notify). Всё по МСК.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import aiohttp

from bot.config import settings
from bot.single import MSK, _get_state, _set_state

logger = logging.getLogger(__name__)


def _today_msk() -> date:
    return (datetime.utcnow() + MSK).date()


def _next_month(d: date) -> date:
    """Та же дата в следующем месяце (с поправкой на короткие месяцы)."""
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    day = d.day
    while day > 28:
        try:
            return date(y, m, day)
        except ValueError:
            day -= 1
    return date(y, m, day)


async def get_hosting_status() -> dict:
    due_s = await _get_state("hosting_due")
    paid_s = await _get_state("hosting_paid_for")
    due = date.fromisoformat(due_s) if due_s else None
    return {"due": due, "paid": bool(due and paid_s == due_s), "due_str": due_s}


async def set_hosting_due(d: date) -> None:
    await _set_state("hosting_due", d.isoformat())
    await _set_state("hosting_paid_for", "")  # новая дата — снова не оплачено


async def mark_paid() -> date:
    """Отметить текущую дату оплаченной и перенести срок на следующий месяц. Возвращает новую дату."""
    st = await get_hosting_status()
    if not st["due"]:
        return None
    nxt = _next_month(st["due"])
    await _set_state("hosting_due", nxt.isoformat())
    await _set_state("hosting_paid_for", "")
    return nxt


async def _heartbeat() -> None:
    url = settings.heartbeat_url
    if not url:
        return
    try:
        async with aiohttp.ClientSession() as s:
            await s.get(url, timeout=aiohttp.ClientTimeout(total=10))
    except Exception as e:  # noqa: BLE001 — пинг не критичен
        logger.info("heartbeat ping failed: %s", e)


async def _hosting_check(bot) -> None:
    st = await get_hosting_status()
    due = st["due"]
    if not due or st["paid"]:
        return
    today = _today_msk()
    days = (due - today).days
    msg = None
    if days == 3:
        msg = (
            f"⏳ <b>Оплата хостинга через 3 дня</b> — {due.strftime('%d.%m')}.\n"
            "Не забудь оплатить Railway, иначе бот встанет. Когда оплатишь — жми /paid."
        )
    elif days <= 0:
        overdue = f" (просрочка {-days} дн.)" if days < 0 else ""
        msg = (
            f"🔴 <b>Оплата хостинга сегодня{overdue}</b> — {due.strftime('%d.%m')}!\n"
            "Если не проплатить — бот скоро встанет. Оплати Railway и жми /paid."
        )
    if not msg:
        return
    # не чаще одного напоминания в день
    if await _get_state("hosting_last_notify") == today.isoformat():
        return
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, msg)
        except Exception as e:  # noqa: BLE001
            logger.warning("hosting reminder to %s failed: %s", admin_id, e)
    await _set_state("hosting_last_notify", today.isoformat())


async def run_ops_loop(bot, interval: int = 300) -> None:
    """Каждые interval сек: пинг монитора + проверка оплаты хостинга."""
    await asyncio.sleep(30)
    while True:
        try:
            await _heartbeat()
            await _hosting_check(bot)
        except Exception as e:  # noqa: BLE001
            logger.warning("ops loop error: %s", e)
        await asyncio.sleep(interval)
