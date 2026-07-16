"""
Отправка сохранённых фото креаторов в рабочую группу.

file_id фото копятся в таблице creator_photos (см. registration._save_photo_ids).
Здесь — разовая/повторяемая отправка их в группу: помечаем отправленные (sent_to_chat),
поэтому повторный запуск НЕ шлёт то же самое дважды.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.types import InputMediaDocument, InputMediaPhoto
from sqlalchemy import select

from bot.database import get_session
from bot.models import Creator, CreatorPhoto

logger = logging.getLogger(__name__)

_MAX_GROUP = 10  # столько медиа Telegram отдаёт одной медиагруппой


async def _send_batch(bot: Bot, chat: str | int, media_cls, ids: list[str]) -> None:
    if not ids:
        return
    if len(ids) == 1:
        if media_cls is InputMediaPhoto:
            await bot.send_photo(chat, ids[0])
        else:
            await bot.send_document(chat, ids[0])
        return
    for i in range(0, len(ids), _MAX_GROUP):
        chunk = ids[i : i + _MAX_GROUP]
        if len(chunk) == 1:
            await bot.send_photo(chat, chunk[0]) if media_cls is InputMediaPhoto else await bot.send_document(chat, chunk[0])
        else:
            await bot.send_media_group(chat, [media_cls(media=f) for f in chunk])
        await asyncio.sleep(0.3)


async def send_stored_photos(bot: Bot, chat: str | int) -> tuple[int, int, int]:
    """Отправить в chat все сохранённые фото, которые туда ещё не отправляли.
    Возвращает (креаторов, фото отправлено, не удалось)."""
    target = str(chat)
    async with get_session() as session:
        rows = (
            await session.execute(
                select(CreatorPhoto).where(CreatorPhoto.sent_to_chat.is_(None))
            )
        ).scalars().all()
        creators = {c.tg_id: c for c in (await session.execute(select(Creator))).scalars().all()}

        by_creator: dict[int, list[CreatorPhoto]] = {}
        for r in rows:
            by_creator.setdefault(r.tg_id, []).append(r)

        sent_photos, failed, done_creators = 0, 0, 0
        for tg_id, items in by_creator.items():
            c = creators.get(tg_id)
            name = getattr(c, "full_name", None) or "—"
            tg = getattr(c, "telegram_contact", None) or "—"
            try:
                await bot.send_message(target, f"📷 Фото креатора {name} ({tg}, id {tg_id})")
                await _send_batch(bot, target, InputMediaPhoto, [i.file_id for i in items if i.kind == "photo"])
                await _send_batch(bot, target, InputMediaDocument, [i.file_id for i in items if i.kind == "doc"])
                for i in items:
                    i.sent_to_chat = target
                sent_photos += len(items)
                done_creators += 1
            except Exception as e:  # noqa: BLE001 — один креатор не должен ронять всю отправку
                logger.warning("send stored photos for %s failed: %s", tg_id, e)
                failed += len(items)
            await asyncio.sleep(0.3)
        await session.commit()
    return done_creators, sent_photos, failed
