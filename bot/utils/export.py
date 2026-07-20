"""Экспорт локальной базы креаторов в xlsx (команда /export для админов)."""
from __future__ import annotations

import io

from openpyxl import Workbook
from sqlalchemy import select

from bot.database import get_session
from bot.models import Creator

COLUMNS = [
    ("full_name", "Имя и фамилия"),
    ("telegram_contact", "Telegram"),
    ("instagram", "Instagram"),
    ("other_socials", "Другие соцсети"),
    ("rate", "Оплата"),
    ("portfolio", "Портфолио"),
    ("age", "Возраст"),
    ("city", "Город"),
    ("phone", "Телефон"),
    ("categories", "Категории"),
    ("created_at", "Добавлен"),
]


async def export_creators_xlsx() -> io.BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Креаторы"
    ws.append([label for _, label in COLUMNS])

    async with get_session() as session:
        result = await session.execute(select(Creator).order_by(Creator.created_at))
        for creator in result.scalars().all():
            ws.append([str(getattr(creator, field) or "") for field, _ in COLUMNS])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


async def export_unregistered_xlsx() -> io.BytesIO:
    """ТОЛЬКО те, кто заходил в бот, но НЕ зарегистрировался — ровно те, кому уйдёт
    пуш-напоминание. Отдельно от «Экспорта заходов»: тот выгружает ВСЕХ заходивших
    (включая зарегистрированных), из-за чего казалось, что «они все есть в таблице»."""
    from bot.models import BotVisit, Creator

    wb = Workbook()
    ws = wb.active
    ws.title = "Не зарегались"
    ws.append(["Telegram ID", "Username", "Имя", "Первый заход", "Пуш отправлен"])

    async with get_session() as session:
        creators = {c.tg_id for c in (await session.execute(select(Creator))).scalars().all()}
        visits = (
            await session.execute(select(BotVisit).order_by(BotVisit.first_seen))
        ).scalars().all()
        for v in visits:
            if v.tg_id in creators:
                continue
            ws.append(
                [
                    v.tg_id,
                    str(v.username or ""),
                    str(v.full_name or ""),
                    v.first_seen.strftime("%Y-%m-%d %H:%M") if v.first_seen else "",
                    v.nudged_at.strftime("%Y-%m-%d %H:%M") if v.nudged_at else "нет",
                ]
            )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


async def export_visits_xlsx() -> io.BytesIO:
    """Список ВСЕХ, кто нажал /start (заходы в бот) — и зарегистрированных тоже.
    Если нужны только незарегавшиеся — см. export_unregistered_xlsx."""
    from bot.models import BotVisit

    wb = Workbook()
    ws = wb.active
    ws.title = "Заходы"
    ws.append(["Telegram ID", "Username", "Имя", "Источник", "Первый заход", "Последний заход"])

    async with get_session() as session:
        result = await session.execute(select(BotVisit).order_by(BotVisit.first_seen))
        for v in result.scalars().all():
            ws.append(
                [
                    v.tg_id,
                    str(v.username or ""),
                    str(v.full_name or ""),
                    str(getattr(v, "source", None) or ""),
                    v.first_seen.strftime("%Y-%m-%d %H:%M") if v.first_seen else "",
                    v.last_seen.strftime("%Y-%m-%d %H:%M") if v.last_seen else "",
                ]
            )

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
