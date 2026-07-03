"""
Локальная SQLite — используется как быстрый кэш (чтобы не дёргать Google
Sheets на каждое нажатие кнопки) и хранилище номера строки в таблице
(sheet_row), чтобы doUpdateRow_ знал, какую строку править.

ВАЖНО: это НЕ источник правды для дедупа. Источник правды — Google Sheet
(см. bot/sheets.py, doLookup_). Локальная БД на Railway эфемерна и
обнуляется при каждом передеплое — если её нет, бот просто на следующий
/start заново спросит lookup у таблицы и восстановит sheet_row.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instagram: Mapped[str | None] = mapped_column(Text, nullable=True)
    other_socials: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portfolio: Mapped[str | None] = mapped_column(Text, nullable=True)
    age: Mapped[str | None] = mapped_column(String(16), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    categories: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
