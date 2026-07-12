from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from bot.database import get_session
from bot.models import Creator

# Момент запуска фичи «пуш-напоминание». Все заходы ДО него — бэклог: авто-луп их не
# трогает, они помечаются nudged_at == этой меткой на старте (grandfather) и уходят
# ТОЛЬКО вручную командой /nudge_backlog. Заходы после эпохи → авто-нудж через 2ч.
NUDGE_EPOCH = datetime(2026, 7, 12, 20, 40, 0)  # UTC
NUDGE_DELAY = timedelta(hours=2)


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


async def seed_visits_from_known() -> int:
    """Разово/идемпотентно: засеять bot_visits теми, кто ТОЧНО нажимал /start — у кого
    есть chat_id (креаторы в боте + откликнувшиеся). Заходы до появления трекинга иначе
    не восстановить (в логах /start не писался). Дедуп по tg_id; вернёт число добавленных."""
    from bot.models import BotVisit, CachedApplication

    now = datetime.utcnow()
    async with get_session() as session:
        existing = {v.tg_id for v in (await session.execute(select(BotVisit))).scalars().all()}
        creators = {c.tg_id for c in (await session.execute(select(Creator))).scalars().all()}
        apps = {a.chat_id for a in (await session.execute(select(CachedApplication))).scalars().all()}
        added = 0
        for tg in (creators | apps):
            if tg and tg not in existing:
                session.add(BotVisit(tg_id=tg, first_seen=now, last_seen=now))
                added += 1
        if added:
            await session.commit()
    return added


async def grandfather_nudges() -> int:
    """Разово/идемпотентно: пометить все заходы ДО NUDGE_EPOCH как «зачищенные»
    (nudged_at = NUDGE_EPOCH), чтобы авто-луп не разослал им пуш пачкой при деплое.
    Бэклог уходит отдельно, вручную (/nudge_backlog). Новые заходы (first_seen после
    эпохи) сюда не попадают — их шлём автоматически через 2ч."""
    from bot.models import BotVisit

    async with get_session() as session:
        rows = (
            await session.execute(
                select(BotVisit).where(
                    BotVisit.nudged_at.is_(None), BotVisit.first_seen < NUDGE_EPOCH
                )
            )
        ).scalars().all()
        for v in rows:
            v.nudged_at = NUDGE_EPOCH
        if rows:
            await session.commit()
    return len(rows)


async def _registered_ids(session) -> set[int]:
    return {c.tg_id for c in (await session.execute(select(Creator))).scalars().all()}


async def due_nudges() -> list[int]:
    """tg_id заходов, которым ПОРА слать напоминание: ещё не слали (nudged_at IS NULL),
    заход был ≥2ч назад, и человек не зарегистрировался (нет в анкетах)."""
    from bot.models import BotVisit

    cutoff = datetime.utcnow() - NUDGE_DELAY
    async with get_session() as session:
        rows = (
            await session.execute(
                select(BotVisit).where(
                    BotVisit.nudged_at.is_(None), BotVisit.first_seen <= cutoff
                )
            )
        ).scalars().all()
        reg = await _registered_ids(session)
    return [v.tg_id for v in rows if v.tg_id not in reg]


async def backlog_unregistered() -> list[int]:
    """tg_id бэклога (заходы до эпохи, помеченные grandfather) без регистрации — для
    ручной рассылки /nudge_backlog. После отправки отмечаем mark_nudged → повторно не уйдёт."""
    from bot.models import BotVisit

    async with get_session() as session:
        rows = (
            await session.execute(
                select(BotVisit).where(BotVisit.nudged_at == NUDGE_EPOCH)
            )
        ).scalars().all()
        reg = await _registered_ids(session)
    return [v.tg_id for v in rows if v.tg_id not in reg]


async def mark_nudged(tg_id: int) -> None:
    """Отметить, что пуш отправлен (реальным временем) — больше этому tg_id не шлём."""
    from bot.models import BotVisit

    async with get_session() as session:
        v = (await session.execute(select(BotVisit).where(BotVisit.tg_id == tg_id))).scalar_one_or_none()
        if v is not None:
            v.nudged_at = datetime.utcnow()
            await session.commit()


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
