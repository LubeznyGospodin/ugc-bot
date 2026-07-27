from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.config import settings
from bot.models import Base

# pool_pre_ping — важно для Postgres: отсекает «протухшие» соединения (Railway
# может разрывать простаивающие), иначе первый запрос после паузы падал бы.
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы. Работает и для SQLite, и для Postgres (asyncpg)."""
    url = settings.database_url
    # Для файловой SQLite создаём директорию, если её нет.
    if url.startswith("sqlite") and ":///" in url:
        db_path = url.split(":///", 1)[1]
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            Path(db_dir).mkdir(parents=True, exist_ok=True)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Мягкая миграция: create_all НЕ добавляет колонки в уже существующую таблицу.
    # Для БД, созданной до появления поля photo, доливаем колонку идемпотентно
    # (повторный запуск/новая таблица — ALTER падает на «дубликат» и гасится).
    await _add_column_if_missing("creators", "photo", "TEXT")
    # Размещение в канале @ugc_creatory по кнопке «Разместить»/«Нахер».
    await _add_column_if_missing("creators", "placement", "VARCHAR(16)")
    await _add_column_if_missing("creators", "channel_msg_id", "BIGINT")
    await _add_column_if_missing("creators", "tilda_uid", "VARCHAR(64)")
    await _add_column_if_missing("creators", "site_queued", "BOOLEAN DEFAULT FALSE")
    # Расширяем age до TEXT: креаторы вводят «Возраст» свободно, varchar(16) ронял синк.
    await _alter_column_type("creators", "age", "TEXT")
    # Пуш-напоминание незарегистрированным: когда отправили (NULL — не слали).
    await _add_column_if_missing("bot_visits", "nudged_at", "TIMESTAMP")
    # Метка источника из deep-link (?start=МЕТКА) — атрибуция переходов по ссылкам.
    await _add_column_if_missing("bot_visits", "source", "VARCHAR(64)")
    # Реквизиты оплаты в пайплайне «Сингл» (таблица создана раньше — доливаем колонки).
    await _add_column_if_missing("single_pipeline", "payment_phone", "VARCHAR(64)")
    await _add_column_if_missing("single_pipeline", "payment_bank", "TEXT")
    await _add_column_if_missing("single_pipeline", "payment_at", "TIMESTAMP")
    # Зеркало колонок «Подтвердил»/«Ссылка на ролик» в кэше откликов — для воронки «Сингл».
    await _add_column_if_missing("cached_applications", "confirmed", "VARCHAR(16)")
    await _add_column_if_missing("cached_applications", "video", "TEXT")
    # Дата первого сбора охвата — для заморозки старых роликов.
    await _add_column_if_missing("reach_rows", "first_seen", "TIMESTAMP")
    # Охват вписан руками в таблице → не парсим и не перезаписываем.
    await _add_column_if_missing("reach_rows", "manual", "BOOLEAN DEFAULT FALSE")
    # Ссылка добавлена через бота (не из листа «Отклики») → reach_run её не гасит.
    await _add_column_if_missing("reach_rows", "pinned", "BOOLEAN DEFAULT FALSE")
    # Проект работы (база @ugc_creatory / клиент PapKids) — чтобы не смешивать.
    await _add_column_if_missing("creator_works", "project", "VARCHAR(32) DEFAULT 'base'")
    # «Подтвердил участие» иногда пишут фразой — varchar(16) ронял sync_applications.
    await _alter_column_type("cached_applications", "confirmed", "TEXT")


async def _add_column_if_missing(table: str, column: str, coltype: str) -> None:
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
    except Exception:  # noqa: BLE001 — колонка уже есть: и Postgres, и SQLite бросают ошибку
        pass


async def _alter_column_type(table: str, column: str, coltype: str) -> None:
    # Postgres расширяет тип (данные сохраняются). SQLite не поддерживает ALTER COLUMN
    # TYPE, но там длина varchar и не enforce-ится — ошибку просто гасим.
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {coltype}")
            )
    except Exception:  # noqa: BLE001
        pass


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
