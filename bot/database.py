from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.config import settings
from bot.models import Base

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)


def init_db() -> None:
    # используем sync engine для инициализации, чтобы избежать greenlet
    db_url = settings.database_url.replace("sqlite+aiosqlite", "sqlite")

    # создаём директорию если это файл SQLite и её нет
    if db_url.startswith("sqlite:///"):
        db_path = db_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            Path(db_dir).mkdir(parents=True, exist_ok=True)

    sync_engine = create_engine(db_url)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()


@asynccontextmanager
async def get_session():
    async with async_session() as session:
        yield session
