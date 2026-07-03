"""
Админ-панель (критерий приёмки #3): аналитика (кол-во креаторов) + рассылка.
Доступ — только settings.admin_ids. Рассылка идёт по chat_id, известным
локальной SQLite (кто хоть раз нажимал /start у этого бота) — рассылать
по всей гугл-таблице бессмысленно, т.к. там половина строк — люди, которых
никогда не было в боте (chat_id пуст).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from bot.config import settings
from bot.database import get_session
from bot.keyboards import admin_menu_keyboard, broadcast_confirm_keyboard
from bot.models import Creator
from bot.sheets import SheetsError, sheets_client
from bot.states import BroadcastFSM
from bot.utils.chat_cleanup import render_screen
from bot.utils.db_helpers import count_creators
from bot.utils.export import export_creators_xlsx

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _admin_only(user_id: int) -> bool:
    return settings.is_admin(user_id)


@router.message(Command("admin"))
@router.message(F.text == "⚙️ Админка")
async def admin_menu(message: Message, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    await render_screen(bot, message.chat.id, "⚙️ Админ-панель", reply_markup=admin_menu_keyboard(), delete_trigger=message)


@router.callback_query(F.data == "admin:stats")
async def admin_stats(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()

    local_count = await count_creators()
    try:
        sheet_stats = await sheets_client.stats()
        sheet_line = (
            f"Всего в таблице: {sheet_stats.total_creators}\n"
            f"Есть в боте: {sheet_stats.in_bot}\n"
            f"Нет в боте: {sheet_stats.not_in_bot}"
        )
    except SheetsError as e:
        sheet_line = f"(не удалось получить данные таблицы: {e})"

    text = f"📊 Аналитика\n\nЛокально знает бот: {local_count}\n\n{sheet_line}"
    await render_screen(bot, call.message.chat.id, text, reply_markup=admin_menu_keyboard())


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()
    await state.set_state(BroadcastFSM.waiting_text)
    await render_screen(bot, call.message.chat.id, "Пришлите текст рассылки одним сообщением.")


@router.message(BroadcastFSM.waiting_text)
async def admin_broadcast_text(message: Message, state: FSMContext, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    await state.update_data(broadcast_text=message.text)
    count = await count_creators()
    await state.set_state(BroadcastFSM.waiting_confirm)
    await render_screen(
        bot,
        message.chat.id,
        f"Отправить это сообщение {count} креаторам?\n\n---\n{message.text}\n---",
        reply_markup=broadcast_confirm_keyboard(),
        delete_trigger=message,
    )


@router.callback_query(BroadcastFSM.waiting_confirm, F.data == "admin:broadcast_cancel")
async def admin_broadcast_cancel(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer("Отменено")
    await state.clear()
    await render_screen(bot, call.message.chat.id, "⚙️ Админ-панель", reply_markup=admin_menu_keyboard())


@router.callback_query(BroadcastFSM.waiting_confirm, F.data == "admin:broadcast_send")
async def admin_broadcast_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    await state.clear()
    await call.answer("Начинаю рассылку...")
    await render_screen(bot, call.message.chat.id, "📣 Рассылаю...")

    async with get_session() as session:
        result = await session.execute(select(Creator.tg_id))
        chat_ids = [row[0] for row in result.all()]

    sent, failed = 0, 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
            sent += 1
        except Exception as e:
            logger.warning("broadcast to %s failed: %s", chat_id, e)
            failed += 1
        await asyncio.sleep(0.05)  # не упереться в лимиты Telegram (30 msg/sec)

    await render_screen(
        bot,
        call.message.chat.id,
        f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе удалось: {failed}",
        reply_markup=admin_menu_keyboard(),
    )


@router.callback_query(F.data == "admin:export")
async def admin_export(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer("Формирую файл...")
    buf = await export_creators_xlsx()
    await bot.send_document(
        call.message.chat.id,
        BufferedInputFile(buf.read(), filename="creators.xlsx"),
    )


@router.message(Command("export"))
async def export_command(message: Message, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    buf = await export_creators_xlsx()
    await bot.send_document(
        message.chat.id,
        BufferedInputFile(buf.read(), filename="creators.xlsx"),
    )
