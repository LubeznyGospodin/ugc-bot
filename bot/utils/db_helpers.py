from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from bot.database import get_session
from bot.models import Creator


async def record_visit(tg_id: int, username: str | None, full_name: str | None) -> None:
    """Зафиксировать заход в бот (/start), уникально по tg_id. Для воронки CJM —
    считаем ВСЕХ, кто нажал старт, даже если дальше не пошли."""
    from bot.models import BotVisit

    now = datetime.utcnow()
    async with get_session() as session:
        v = (await session.execute(select(BotVisit).where(BotVisit.tg_id == tg_id))).scalar_one_or_none()
        if v is None:
            session.add(
                BotVisit(tg_id=tg_id, username=username, full_name=full_name, first_seen=now, last_seen=now)
            )
        else:
            v.last_seen = now
            if username:
                v.username = username
            if full_name:
                v.full_name = full_name
        await session.commit()


async def get_creator_by_tg_id(tg_id: int) -> Creator | None:
    async with get_session() as session:
        result = await session.execute(select(Creator).where(Creator.tg_id == tg_id))
        return result.scalar_one_or_none()


async def upsert_creator(
    tg_id: int, username: str | None, fields: dict[str, Any], sheet_row: int | None = None
) -> Creator:
    async with get_session() as session:
        result = await session.execute(select(Creator).where(Creator.tg_id == tg_id))
        creator = result.scalar_one_or_none()
        if creator is None:
            creator = Creator(tg_id=tg_id, username=username)
            session.add(creator)
        else:
            creator.username = username or creator.username
        for key, value in fields.items():
            if hasattr(creator, key) and value not in (None, ""):
                setattr(creator, key, value)
        if sheet_row is not None:
            creator.sheet_row = sheet_row
        await session.commit()
        await session.refresh(creator)
        return creator


async def count_creators() -> int:
    async with get_session() as session:
        result = await session.execute(select(Creator))
        return len(result.scalars().all())


async def db_stats() -> dict[str, int]:
    """Аналитика из БД (мгновенно, без запроса к таблице)."""
    from bot.models import CachedApplication

    async with get_session() as session:
        creators = len((await session.execute(select(Creator))).scalars().all())
        apps = (await session.execute(select(CachedApplication))).scalars().all()
    return {
        "creators": creators,
        "apps": len(apps),
        "offers": sum(1 for a in apps if a.status == "оффер"),
        "rejects": sum(1 for a in apps if a.status == "отказ"),
        "pending": sum(1 for a in apps if a.status == "на рассмотрении"),
    }


async def funnel_stats() -> dict[str, int]:
    """Воронка CJM: заходы (/start) → регистрации (креаторы в боте) → уникальные
    отклики (сколько РАЗНЫХ пользователей откликнулись, а не сколько всего откликов)."""
    from bot.models import BotVisit, CachedApplication

    async with get_session() as session:
        visits = len((await session.execute(select(BotVisit))).scalars().all())
        creators = len((await session.execute(select(Creator))).scalars().all())
        apps = (await session.execute(select(CachedApplication))).scalars().all()
    return {
        "visits": visits,
        "registrations": creators,
        "unique_applicants": len({a.chat_id for a in apps}),
        "apps": len(apps),
        "offers": sum(1 for a in apps if a.status == "оффер"),
        "rejects": sum(1 for a in apps if a.status == "отказ"),
        "pending": sum(1 for a in apps if a.status == "на рассмотрении"),
    }
