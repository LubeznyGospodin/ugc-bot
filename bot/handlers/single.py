"""
Креатор-часть пайплайна «Сингл»: подтверждение участия → срок ролика → ссылки на посты.
Оффер-сообщение с кнопкой «Участвую» отправляет sync (при статусе «оффер» в таблице).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.keyboards import single_submit_keyboard
from bot.sheets import SheetsError, sheets_client
from bot.single import (
    ACCEPT_TEXT,
    ASK_DEADLINE_RESUME,
    ASK_LINKS_TEXT,
    ASK_PAYMENT_TEXT,
    BAD_DEADLINE_TEXT,
    BAD_PAYMENT_TEXT,
    DONE_TEXT,
    NOT_A_LINK_TEXT,
    PAYMENT_SAVED_TEXT,
    get_pipeline,
    mark_accepted,
    parse_deadline,
    parse_payment,
    producing_text,
    save_payment,
    set_deadline,
    submit_links,
)
from bot.states import SingleFSM

logger = logging.getLogger(__name__)
router = Router(name="single")

# В пайплайне «Сингл» НЕ редактируем сообщения «на месте» и не удаляем ответы креатора —
# вся переписка сохраняется (просьба заказчика). Поэтому шлём обычные новые сообщения.


def _mirror(chat_id: int, fields: dict) -> None:
    """Зеркалим поле пайплайна в лист «Отклики» в фоне (вебхук медленный)."""
    async def _run():
        try:
            await sheets_client.single_update(chat_id, fields)
        except SheetsError as e:  # noqa: BLE001
            logger.warning("single_update mirror failed for %s: %s", chat_id, e)

    asyncio.create_task(_run())


@router.callback_query(F.data == "single:accept")
async def single_accept(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await mark_accepted(call.from_user.id)
    _mirror(call.from_user.id, {"confirm": "да"})
    await state.set_state(SingleFSM.waiting_deadline)
    await bot.send_message(call.message.chat.id, ACCEPT_TEXT)


@router.message(SingleFSM.waiting_deadline, F.text)
async def single_deadline(message: Message, state: FSMContext, bot: Bot):
    dl = parse_deadline(message.text)
    if dl is None:
        await message.answer(BAD_DEADLINE_TEXT)
        return
    await set_deadline(message.from_user.id, dl, message.text.strip())
    _mirror(message.from_user.id, {"deadline": dl.strftime("%d.%m.%Y")})
    await state.clear()
    await message.answer(producing_text(dl), reply_markup=single_submit_keyboard())


@router.callback_query(F.data == "single:resume_deadline")
async def single_resume_deadline(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Вернуться к вводу срока из «Мои проекты» (шаг восстановлен из БД)."""
    await call.answer()
    await state.set_state(SingleFSM.waiting_deadline)
    await bot.send_message(call.message.chat.id, ASK_DEADLINE_RESUME)


@router.callback_query(F.data == "single:submit")
async def single_submit_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.set_state(SingleFSM.waiting_links)
    await bot.send_message(call.message.chat.id, ASK_LINKS_TEXT)


@router.callback_query(F.data == "single:resume_payment")
async def single_resume_payment(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Вернуться к вводу реквизитов из «Мои проекты»."""
    await call.answer()
    await state.set_state(SingleFSM.waiting_payment)
    await bot.send_message(call.message.chat.id, ASK_PAYMENT_TEXT)


@router.message(SingleFSM.waiting_links)
async def single_links(message: Message, state: FSMContext, bot: Bot):
    text = (message.text or message.caption or "").strip()
    if "http" not in text.lower():
        # файл/видео/просто текст без ссылки — не принимаем, объясняем куда что.
        await message.answer(NOT_A_LINK_TEXT)
        return
    await submit_links(message.from_user.id, text)
    _mirror(message.from_user.id, {"links": text})
    # После ссылок → просим реквизиты для оплаты по СБП.
    await state.set_state(SingleFSM.waiting_payment)
    await message.answer(DONE_TEXT)

    # Уведомляем админов о сдаче ролика.
    p = await get_pipeline(message.from_user.id)
    name = (p.full_name if p else None) or message.from_user.full_name
    tg = (p.telegram if p else None) or (f"@{message.from_user.username}" if message.from_user.username else "—")
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, f"✅ <b>{name}</b> ({tg}) сдал(а) ролик по «Сингл»:\n{text}")
        except Exception:  # noqa: BLE001
            logger.warning("notify admin %s about single done failed", admin_id)


@router.message(SingleFSM.waiting_payment)
async def single_payment(message: Message, state: FSMContext, bot: Bot):
    parsed = parse_payment(message.text or message.caption or "")
    if parsed is None:
        await message.answer(BAD_PAYMENT_TEXT)
        return
    phone, bank = parsed
    await save_payment(message.from_user.id, phone, bank)
    _mirror(message.from_user.id, {"phone": phone, "bank": bank})
    await state.clear()
    await message.answer(PAYMENT_SAVED_TEXT)

    p = await get_pipeline(message.from_user.id)
    name = (p.full_name if p else None) or message.from_user.full_name
    tg = (p.telegram if p else None) or (f"@{message.from_user.username}" if message.from_user.username else "—")
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"💳 <b>{name}</b> ({tg}) прислал(а) реквизиты по «Сингл»:\n"
                f"Телефон: <code>{phone}</code>\nБанк: {bank or '—'}",
            )
        except Exception:  # noqa: BLE001
            logger.warning("notify admin %s about single payment failed", admin_id)
