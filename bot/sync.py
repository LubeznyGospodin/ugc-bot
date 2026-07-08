"""
Фоновая синхронизация Google-таблица → БД.

Идея: тяжёлый запрос к таблице (Apps Script) делаем НЕ на каждый тап пользователя,
а раз в N секунд в фоне, и складываем результат в БД. Хэндлеры читают из БД
мгновенно. Это убирает тормоза и «работает через раз».

Сейчас синхронизируем бренды (самый частый поток). Профиль и отклики —
stale-while-revalidate прямо в хэндлерах (см. соответствующие handlers).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import delete, select

from bot.database import get_session
from bot.models import CachedApplication, CachedBrand, Creator
from bot.sheets import Application, Brand, SheetsError, sheets_client

# Ключи выгрузки all_creators -> поля модели Creator.
_CREATOR_MAP = {
    "full_name": "full_name",
    "telegram": "telegram_contact",
    "instagram": "instagram",
    "other_socials": "other_socials",
    "rate": "rate",
    "portfolio": "portfolio",
    "age": "age",
    "city": "city",
    "phone": "phone",
    "category": "categories",
}

logger = logging.getLogger(__name__)


async def sync_brands() -> int:
    """Тянет бренды из таблицы и полностью пере-заливает кэш в БД.
    Возвращает число активных брендов (для лога)."""
    brands = await sheets_client.brands(ttl=0.0)  # свежие, минуя in-memory кэш
    now = datetime.utcnow()
    async with get_session() as session:
        # Полная замена: удаляем всё и вставляем актуальное — так исчезнувшие
        # (Активен=нет) бренды сами уходят из кэша.
        await session.execute(delete(CachedBrand))
        for b in brands:
            session.add(
                CachedBrand(
                    brand_id=b.id,
                    title=b.title,
                    description=b.description,
                    category=b.category,
                    synced_at=now,
                )
            )
        await session.commit()
    return len(brands)


async def get_cached_brands() -> list[Brand]:
    """Мгновенное чтение брендов из БД."""
    async with get_session() as session:
        rows = (await session.execute(select(CachedBrand))).scalars().all()
    return [Brand(id=r.brand_id, title=r.title, description=r.description, category=r.category) for r in rows]


async def cache_applications(chat_id: int, apps: list[Application]) -> None:
    """Складываем отклики пользователя в БД (полная замена по chat_id)."""
    now = datetime.utcnow()
    async with get_session() as session:
        await session.execute(delete(CachedApplication).where(CachedApplication.chat_id == chat_id))
        for a in apps:
            session.add(
                CachedApplication(
                    chat_id=chat_id,
                    brand_title=a.brand_title,
                    status=a.status,
                    reason=a.reason,
                    date=a.date,
                    synced_at=now,
                )
            )
        await session.commit()


async def add_cached_application(chat_id: int, brand_title: str) -> None:
    """Мгновенно добавляет один отклик в БД-кэш (при нажатии «Откликнуться»),
    чтобы он сразу был виден в «Мои отклики», не дожидаясь записи в таблицу."""
    async with get_session() as session:
        session.add(
            CachedApplication(
                chat_id=chat_id,
                brand_title=brand_title,
                status="на рассмотрении",
                reason="",
                date="",
                synced_at=datetime.utcnow(),
            )
        )
        await session.commit()


async def get_cached_applications(chat_id: int) -> list[Application]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(CachedApplication).where(CachedApplication.chat_id == chat_id)
            )
        ).scalars().all()
    return [Application(brand_title=r.brand_title, status=r.status, date=r.date, reason=r.reason) for r in rows]


async def sync_creators() -> int:
    """Тянет всех креаторов (с Chat ID) из таблицы → апсертит в БД по tg_id.
    Пустые поля из таблицы НЕ затирают уже заполненные в БД."""
    items = await sheets_client.all_creators()
    now = datetime.utcnow()
    async with get_session() as session:
        for it in items:
            try:
                tg = int(str(it.get("chat_id")).strip())
            except (TypeError, ValueError):
                continue
            c = (await session.execute(select(Creator).where(Creator.tg_id == tg))).scalar_one_or_none()
            if c is None:
                c = Creator(tg_id=tg)
                session.add(c)
            for k, field in _CREATOR_MAP.items():
                v = it.get(k)
                if v not in (None, "") and hasattr(c, field):
                    setattr(c, field, v)
            c.synced_at = now
        await session.commit()
    return len(items)


async def sync_applications() -> int:
    """Тянет все отклики из таблицы → полностью пере-заливает кэш откликов в БД."""
    items = await sheets_client.all_applications()
    now = datetime.utcnow()
    async with get_session() as session:
        await session.execute(delete(CachedApplication))
        for it in items:
            try:
                cid = int(str(it.get("chat_id")).strip())
            except (TypeError, ValueError):
                continue
            session.add(
                CachedApplication(
                    chat_id=cid,
                    brand_title=str(it.get("brand_title") or ""),
                    status=(str(it.get("status") or "на рассмотрении")).strip().lower(),
                    reason=str(it.get("reason") or ""),
                    date=str(it.get("date") or ""),
                    synced_at=now,
                )
            )
        await session.commit()
    return len(items)


async def run_sync_loop(interval: int = 120) -> None:
    """Бесконечный фоновой цикл синхронизации таблица → БД. Ошибки не роняют бота."""
    while True:
        for name, fn in (("brands", sync_brands), ("creators", sync_creators), ("applications", sync_applications)):
            try:
                n = await fn()
                logger.info("sync_%s: %s записей в кэше", name, n)
            except SheetsError as e:
                logger.warning("sync_%s failed (таблица недоступна): %s", name, e)
            except Exception as e:  # noqa: BLE001
                logger.warning("sync_%s error: %s", name, e)
        await asyncio.sleep(interval)
