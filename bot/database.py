from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

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


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
