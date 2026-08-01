"""Отложенные рассылки по базе креаторов.

Задание лежит в таблице scheduled_posts (переживает передеплой), фоновый цикл раз в
минуту забирает те, у кого подошло время. Имя подставляется в шаблон по {name}:
берём его из строки отклика, иначе из анкеты; если имени нет — обращение без него.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiogram import Bot
from sqlalchemy import select

from bot.config import settings
from bot.database import get_session
from bot.models import CachedApplication, Creator, ScheduledPost

logger = logging.getLogger(__name__)

SEND_PAUSE = 0.05  # лимит Telegram — 30 сообщений/сек


def render(text: str, name: str | None) -> str:
    """{name} → имя получателя. Без имени убираем обращение целиком, чтобы не
    получилось «Привет, !»."""
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    if not first:
        return text.replace(", {name}!", "!").replace(" {name}", "").replace("{name}", "")
    return text.replace("{name}", first)


async def audience() -> list[tuple[int, str]]:
    """Вся база бота: [(chat_id, имя)]. Имя — из строки отклика, иначе из анкеты."""
    async with get_session() as s:
        creators = (await s.execute(select(Creator))).scalars().all()
        apps = (await s.execute(select(CachedApplication))).scalars().all()
    from_sheet = {a.chat_id: (a.name or "").strip() for a in apps if (a.name or "").strip()}
    return [(c.tg_id, from_sheet.get(c.tg_id) or (c.full_name or "")) for c in creators]


async def _run_job(bot: Bot, job_id: int) -> None:
    async with get_session() as s:
        job = await s.get(ScheduledPost, job_id)
        if job is None or job.status != "pending":
            return
        job.status = "sending"          # до первой отправки — иначе рестарт задвоит рассылку
        await s.commit()
        text, file_id, name = job.text, job.file_id, job.file_name

    sent, failed = 0, 0
    for chat_id, who in await audience():
        body = render(text, who)
        try:
            if file_id:
                await bot.send_document(chat_id, file_id, caption=body)
            else:
                await bot.send_message(chat_id, body)
            sent += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduled %s → %s failed: %s", job_id, chat_id, e)
            failed += 1
        await asyncio.sleep(SEND_PAUSE)

    async with get_session() as s:
        job = await s.get(ScheduledPost, job_id)
        if job:
            job.status, job.sent, job.failed, job.done_at = "done", sent, failed, datetime.utcnow()
            await s.commit()
    logger.info("scheduled %s: доставлено %s, не удалось %s", job_id, sent, failed)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"📬 Отложенная рассылка отправлена{f' (файл: {name})' if name else ''}.\n"
                f"Доставлено: {sent}\nНе удалось: {failed}",
            )
        except Exception:  # noqa: BLE001
            logger.warning("scheduled report to %s failed", admin_id)


async def run_scheduler(bot: Bot, interval: int = 60) -> None:
    await asyncio.sleep(30)
    while True:
        try:
            async with get_session() as s:
                due = (await s.execute(
                    select(ScheduledPost.id).where(
                        ScheduledPost.status == "pending",
                        ScheduledPost.run_at <= datetime.utcnow(),
                    ).order_by(ScheduledPost.run_at)
                )).scalars().all()
            for job_id in due:
                await _run_job(bot, job_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("scheduler error: %s", e)
        await asyncio.sleep(interval)


def demo() -> None:
    """Самопроверка подстановки имени (без имени обращение не должно ломаться)."""
    t = "Привет, {name}! Как дела?"
    assert render(t, "Лилия Рождественская") == "Привет, Лилия! Как дела?"
    assert render(t, "") == "Привет! Как дела?"
    assert render(t, None) == "Привет! Как дела?"
    assert render("Здорово, {name}", "Миша Бауэр") == "Здорово, Миша"
    print("scheduler.demo ok")


if __name__ == "__main__":
    demo()
