from __future__ import annotations

from typing import Any

from sqlalchemy import select

from bot.database import get_session
from bot.models import Creator


async def get_creator_by_tg_id(tg_id: int) -> Creator | None:
    async with get_session() as session:
        result = await session.execute(select(Creator).where(Creator.tg_id == tg_id))
        return result.scalar_one_or_none()


async def upsert_creator(tg_id: int, username: str | None, fields: dict[str, Any], sheet_row: int | None) -> Creator:
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
