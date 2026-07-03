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
