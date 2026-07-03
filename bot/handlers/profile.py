"""
"👤 Моя анкета" — показывает сохранённые данные, кнопка "Обновить данные"
запускает анкету заново; при отправке используется doUpdateRow_ по
sheet_row вместо создания дубликата строки.
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StateFilter
from aiogram.types import CallbackQuery, Message

from bot.keyboards import BTN_ADMIN, BTN_BRANDS, BTN_HELP, BTN_PROFILE, profile_edit_keyboard
from bot.states import Dedup, BroadcastFSM, Registration
from bot.utils.chat_cleanup import render_screen
from bot.utils.db_helpers import get_creator_by_tg_id

router = Router(name="profile")


# Если юзер нажал на кнопку меню (включая профиль) ВО ВРЕМЯ регистрации/деdup/широковещания,
# обработаем это перед тем как регистрация обработает это как текстовый ввод.
@router.message(
    StateFilter(Registration, Dedup, BroadcastFSM),
    F.text.in_([BTN_PROFILE, BTN_BRANDS, BTN_HELP, BTN_ADMIN])
)
async def menu_button_during_fsm(message: Message, state: FSMContext, bot: Bot):
    """Обработать нажатие на кнопку меню во время FSM. Очистить FSM и обработать как обычно."""
    await state.clear()
    # Перенаправить на соответствующий обработчик
    if message.text == BTN_PROFILE:
        await show_profile(message, bot)
    elif message.text == BTN_BRANDS:
        # Пусть brands router обработает это
        pass
    elif message.text == BTN_HELP or message.text == BTN_ADMIN:
        # Пусть start router обработает это
        pass


@router.message(F.text == BTN_PROFILE)
async def show_profile(message: Message, bot: Bot):
    creator = await get_creator_by_tg_id(message.from_user.id)
    if creator is None:
        await render_screen(
            bot,
            message.chat.id,
            "У тебя пока нет анкеты. Нажми /start, чтобы заполнить.",
            delete_trigger=message,
        )
        return

    text = (
        f"👤 <b>{creator.full_name or '—'}</b>\n"
        f"Telegram: {creator.telegram_contact or '—'}\n"
        f"Instagram: {creator.instagram or '—'}\n"
        f"Соцсети: {creator.other_socials or '—'}\n"
        f"Оплата: {creator.rate or '—'}\n"
        f"Портфолио: {creator.portfolio or '—'}\n"
        f"Возраст: {creator.age or '—'}\n"
        f"Город: {creator.city or '—'}\n"
        f"Телефон: {creator.phone or '—'}\n"
        f"Категории: {creator.categories or '—'}"
    )
    await render_screen(bot, message.chat.id, text, reply_markup=profile_edit_keyboard(), delete_trigger=message)


@router.callback_query(F.data == "profile:edit")
async def edit_profile(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await render_screen(
        bot,
        call.message.chat.id,
        "Обновим анкету. Как тебя зовут (имя и фамилия)?",
    )
    await state.set_state(Registration.full_name)
