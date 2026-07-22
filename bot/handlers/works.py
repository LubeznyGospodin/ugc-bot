"""
Сбор «лучших работ» креатора — материал для карточки в канале @ugc_creatory.

Почему отдельный поток, а не шаг анкеты: анкета — 10 шагов с нумерацией, добавление
11-го шага сбило бы её и ударило по конверсии регистрации. Здесь креатор в любой момент
досылает ролики, а фото у нас уже есть с шага «фото» (см. CreatorPhoto).

Файлы принимаем именно ФАЙЛАМИ (не ссылками): тогда они лежат в Telegram и карточку
можно собрать репостом — ничего не надо скачивать со сторонних площадок.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from bot.database import get_session
from bot.models import CreatorWork
from bot.states import Works

logger = logging.getLogger(__name__)
router = Router(name="works")

MAX_WORKS = 4

ASK_TEXT = (
    "🎬 <b>Твои лучшие работы</b>\n\n"
    f"Пришли до {MAX_WORKS} своих лучших роликов — <b>видеофайлами</b> прямо сюда "
    "(можно по одному).\n\n"
    "Их увидят бренды в нашей базе креаторов — чем сильнее работы, тем чаще зовут "
    "на проекты 🚀\n\n"
    "Как закончишь — жми «Готово»."
)


def _kb(done: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"✅ Готово ({done}/{MAX_WORKS})", callback_data="works:done")]]
    if done:
        rows.append([InlineKeyboardButton(text="🗑 Очистить и прислать заново", callback_data="works:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def count_works(tg_id: int) -> int:
    async with get_session() as s:
        return int(
            (await s.execute(select(func.count()).select_from(CreatorWork).where(CreatorWork.tg_id == tg_id))).scalar()
            or 0
        )


async def add_work(tg_id: int, file_id: str | None, url: str | None = None, source: str = "self") -> int:
    """→ сколько работ стало у креатора (не больше MAX_WORKS)."""
    async with get_session() as s:
        n = int(
            (await s.execute(select(func.count()).select_from(CreatorWork).where(CreatorWork.tg_id == tg_id))).scalar()
            or 0
        )
        if n >= MAX_WORKS:
            return n
        s.add(CreatorWork(tg_id=tg_id, file_id=file_id, url=url, source=source, position=n))
        await s.commit()
        return n + 1


@router.callback_query(F.data == "works:add")
async def works_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.set_state(Works.collecting)
    n = await count_works(call.from_user.id)
    await bot.send_message(call.message.chat.id, ASK_TEXT, reply_markup=_kb(n))


@router.callback_query(F.data == "works:reset")
async def works_reset(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer("Очистил")
    async with get_session() as s:
        await s.execute(delete(CreatorWork).where(CreatorWork.tg_id == call.from_user.id))
        await s.commit()
    await state.set_state(Works.collecting)
    await bot.send_message(call.message.chat.id, "🗑 Готово, присылай заново.", reply_markup=_kb(0))


@router.message(Works.collecting, F.video | F.document | F.animation)
async def works_collect(message: Message, state: FSMContext, bot: Bot):
    obj = message.video or message.document or message.animation
    mime = (getattr(obj, "mime_type", "") or "").lower()
    if message.document and "video" not in mime:
        await message.answer("Это не видео 🙈 Пришли ролик видеофайлом.")
        return
    n = await add_work(message.from_user.id, file_id=obj.file_id)
    if n >= MAX_WORKS:
        await state.clear()
        await message.answer(
            f"✅ Отлично, собрал {MAX_WORKS} работы — этого достаточно!\n"
            "Добавлю тебя в базу креаторов для брендов 🚀"
        )
        await publish_works(bot, message.from_user.id)
        return
    await message.answer(f"➕ Принял ({n}/{MAX_WORKS}). Присылай ещё или жми «Готово».", reply_markup=_kb(n))


@router.callback_query(Works.collecting, F.data == "works:done")
async def works_done(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.clear()
    n = await count_works(call.from_user.id)
    if not n:
        await bot.send_message(
            call.message.chat.id,
            "Ок, вернёмся к этому позже — работы можно добавить в «🧾 Моя анкета».",
        )
        return
    await bot.send_message(
        call.message.chat.id,
        f"✅ Сохранил работы: {n}. Спасибо!\nБренды увидят их в нашей базе креаторов 🚀",
    )
    await publish_works(bot, call.from_user.id)


async def publish_works(bot: Bot, tg_id: int) -> None:
    """Работы → в рабочую группу (там их видно глазами) + счётчик в колонку «Работы»
    листа креаторов. Без этого ролики оседали только в БД бота и наружу не попадали."""
    from datetime import datetime, timedelta

    from bot.config import settings
    from bot.sheets import SheetsError, sheets_client
    from bot.utils.db_helpers import get_creator_by_tg_id

    async with get_session() as s:
        works = (
            await s.execute(
                select(CreatorWork).where(CreatorWork.tg_id == tg_id).order_by(CreatorWork.position)
            )
        ).scalars().all()
    if not works:
        return

    creator = await get_creator_by_tg_id(tg_id)
    name = (getattr(creator, "full_name", None) if creator else None) or "—"
    tg = (getattr(creator, "telegram_contact", None) if creator else None) or "—"

    # 1) в рабочую группу (как фото; если группа не задана — админам)
    targets = [settings.photos_chat_id] if settings.photos_chat_id else list(settings.admin_ids)
    for target in targets:
        try:
            await bot.send_message(target, f"🎬 Работы креатора {name} ({tg}, id {tg_id}) — {len(works)} шт.")
            for w in works:
                if w.file_id:
                    await bot.send_video(target, w.file_id, supports_streaming=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("send works to %s failed: %s", target, e)

    # 2) счётчик в таблицу креаторов
    when = (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y")
    try:
        await sheets_client.works_update(tg_id, f"{len(works)} ролика(ов) · {when}")
    except SheetsError as e:  # noqa: BLE001
        logger.warning("works_update failed for %s: %s", tg_id, e)
