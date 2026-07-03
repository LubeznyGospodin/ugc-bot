"""
Каталог "Запросы брендов" (критерий приёмки #2). Инлайн-список карточек,
данные тянутся живьём из вкладки "Бренды" в Google Sheet через doBrands_ —
значит, чтобы добавить/убрать проект, не нужно трогать код бота, достаточно
отредактировать таблицу (колонка "Активен" = да/нет).
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.keyboards import BTN_BRANDS, brand_card_keyboard, brands_list_keyboard
from bot.sheets import SheetsError, sheets_client
from bot.utils.chat_cleanup import render_screen

logger = logging.getLogger(__name__)
router = Router(name="brands")


async def _fetch_brands():
    try:
        return await sheets_client.brands()
    except SheetsError as e:
        logger.warning("brands fetch failed: %s", e)
        return []


@router.message(F.text == BTN_BRANDS)
async def show_brands(message: Message, bot: Bot):
    brands = await _fetch_brands()
    text = "📢 Актуальные запросы от брендов:" if brands else "Пока нет активных запросов от брендов — загляните позже 🙂"
    await render_screen(bot, message.chat.id, text, reply_markup=brands_list_keyboard(brands), delete_trigger=message)


@router.callback_query(F.data == "brands:list")
async def back_to_list(call: CallbackQuery, bot: Bot):
    await call.answer()
    brands = await _fetch_brands()
    text = "📢 Актуальные запросы от брендов:" if brands else "Пока нет активных запросов — загляните позже 🙂"
    await render_screen(bot, call.message.chat.id, text, reply_markup=brands_list_keyboard(brands))


@router.callback_query(F.data.startswith("brand:"))
async def brand_card(call: CallbackQuery, bot: Bot):
    brand_id = call.data.split(":", 1)[1]

    brands = await _fetch_brands()
    brand = next((b for b in brands if b.id == brand_id), None)
    await call.answer()
    if brand is None:
        await render_screen(bot, call.message.chat.id, "Этот запрос уже неактуален.", reply_markup=brands_list_keyboard(brands))
        return

    text = f"<b>{brand.title}</b>\nКатегория: {brand.category}\n\n{brand.description}"
    await render_screen(bot, call.message.chat.id, text, reply_markup=brand_card_keyboard(brand.id))


@router.callback_query(F.data.startswith("brand_apply:"))
async def brand_apply(call: CallbackQuery, bot: Bot):
    brand_id = call.data.split(":", 1)[1]
    await call.answer("Отклик отправлен! Мы свяжемся с тобой, если подойдёшь.", show_alert=True)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"🙋 Отклик на бренд <code>{brand_id}</code>\n"
                f"От: {call.from_user.full_name} (@{call.from_user.username or '—'}), id {call.from_user.id}",
            )
        except Exception:
            logger.warning("failed to notify admin %s about brand response", admin_id)
