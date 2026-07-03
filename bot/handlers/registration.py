"""
Анкета нового креатора. Линейный FSM-опросник, как и раньше, но:
  - каждый шаг рендерится через render_screen (одно эволюционирующее сообщение,
    сообщения пользователя удаляются) — критерий приёмки #1;
  - на confirm пишем в Google Sheets (action=register, как раньше) И
    дополнительно делаем lookup, чтобы узнать номер строки и сохранить
    его локально (sheet_row) — на будущее, для doUpdateRow_.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.categories import categories_keyboard
from bot.config import settings
from bot.keyboards import BTN_ADMIN, BTN_BRANDS, BTN_HELP, BTN_PROFILE, main_menu, profile_edit_keyboard
from bot.sheets import SheetsError, sheets_client
from bot.states import Registration
from bot.utils.chat_cleanup import render_screen, send_persistent_menu
from bot.utils.db_helpers import get_creator_by_tg_id, upsert_creator

logger = logging.getLogger(__name__)
router = Router(name="registration")

STEP_PROMPTS = {
    Registration.telegram_contact: "Твой контакт в Telegram (с @)?",
    Registration.instagram: "Ссылка на Instagram (или другую соцсеть с примерами работ)?",
    Registration.other_socials: "Другие соцсети (TikTok/YouTube и т.д.)? Если нет — напиши «нет».",
    Registration.rate: "Желаемая оплата за 1 ролик под ключ?",
    Registration.portfolio: "Ссылка на портфолио/примеры работ?",
    Registration.age: "Сколько тебе лет?",
    Registration.city: "В каком городе живёшь?",
    Registration.phone: "Номер телефона (с 8)?",
}

STEP_ORDER = [
    Registration.telegram_contact,
    Registration.instagram,
    Registration.other_socials,
    Registration.rate,
    Registration.portfolio,
    Registration.age,
    Registration.city,
    Registration.phone,
    Registration.categories,
]

FIELD_BY_STATE = {
    Registration.telegram_contact: "telegram",
    Registration.instagram: "instagram",
    Registration.other_socials: "other_socials",
    Registration.rate: "rate",
    Registration.portfolio: "portfolio",
    Registration.age: "age",
    Registration.city: "city",
    Registration.phone: "phone",
}


@router.message(Registration.full_name, ~F.text.in_([BTN_PROFILE, BTN_BRANDS, BTN_HELP, BTN_ADMIN]))
async def step_full_name(message: Message, state: FSMContext, bot: Bot):
    await state.update_data(full_name=message.text.strip())
    await state.set_state(Registration.telegram_contact)
    await render_screen(bot, message.chat.id, STEP_PROMPTS[Registration.telegram_contact], delete_trigger=message)


def _next_state(current) -> "State | None":
    idx = STEP_ORDER.index(current)
    return STEP_ORDER[idx + 1] if idx + 1 < len(STEP_ORDER) else None


async def _generic_step(message: Message, state: FSMContext, bot: Bot):
    current = await state.get_state()
    from bot.states import Registration as R

    current_enum = next(s for s in STEP_ORDER if s.state == current)
    field = FIELD_BY_STATE[current_enum]
    await state.update_data(**{field: message.text.strip()})

    nxt = _next_state(current_enum)
    if nxt is None:
        await _show_categories(message.chat.id, state, bot, message)
        return

    await state.set_state(nxt)
    if nxt == Registration.categories:
        await _show_categories(message.chat.id, state, bot, message)
    else:
        await render_screen(bot, message.chat.id, STEP_PROMPTS[nxt], delete_trigger=message)


for _state in STEP_ORDER[:-1]:  # все, кроме categories (у неё инлайн-кнопки, не текст)
    router.message.register(
        _generic_step,
        _state,
        ~F.text.in_([BTN_PROFILE, BTN_BRANDS, BTN_HELP, BTN_ADMIN])
    )


async def _show_categories(chat_id: int, state: FSMContext, bot: Bot, trigger: Message | None = None):
    await state.update_data(categories=[])
    await state.set_state(Registration.categories)
    await render_screen(
        bot,
        chat_id,
        "Выбери свои категории контента (можно несколько), затем «Готово»:",
        reply_markup=categories_keyboard(set()),
        delete_trigger=trigger,
    )


@router.callback_query(Registration.categories, F.data.startswith("cat:"))
async def pick_category(call: CallbackQuery, state: FSMContext, bot: Bot):
    value = call.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("categories") or [])

    if value == "done":
        await state.update_data(categories=list(selected))
        await _show_confirm(call.message.chat.id, state, bot)
        await call.answer()
        return

    if value in selected:
        selected.discard(value)
    else:
        selected.add(value)
    await state.update_data(categories=list(selected))
    await call.message.edit_reply_markup(reply_markup=categories_keyboard(selected))
    await call.answer()


async def _show_confirm(chat_id: int, state: FSMContext, bot: Bot):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    data = await state.get_data()
    summary = (
        f"Проверь анкету:\n\n"
        f"Имя: {data.get('full_name')}\n"
        f"Telegram: {data.get('telegram')}\n"
        f"Instagram: {data.get('instagram')}\n"
        f"Соцсети: {data.get('other_socials')}\n"
        f"Оплата: {data.get('rate')}\n"
        f"Портфолио: {data.get('portfolio')}\n"
        f"Возраст: {data.get('age')}\n"
        f"Город: {data.get('city')}\n"
        f"Телефон: {data.get('phone')}\n"
        f"Категории: {', '.join(data.get('categories') or [])}\n"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно, отправить", callback_data="reg:submit"),
                InlineKeyboardButton(text="🔄 Начать заново", callback_data="reg:restart"),
            ]
        ]
    )
    await state.set_state(Registration.confirm)
    await render_screen(bot, chat_id, summary, reply_markup=kb)


@router.callback_query(Registration.confirm, F.data == "reg:restart")
async def restart(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.clear()
    await state.set_state(Registration.full_name)
    await render_screen(bot, call.message.chat.id, "Хорошо, начнём заново. Как тебя зовут (имя и фамилия)?")


@router.callback_query(Registration.confirm, F.data == "reg:submit")
async def submit(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    data["category"] = ", ".join(data.pop("categories", []) or [])
    chat_id = call.message.chat.id
    tg_id = call.from_user.id

    logger.info(f"Registration submit for user {tg_id}: {data.get('full_name')}")
    await call.answer("Отправляю...")
    await render_screen(bot, chat_id, "⏳ Сохраняю анкету...")

    # 1. Проверь есть ли уже в локальной БД
    existing = await get_creator_by_tg_id(tg_id)
    is_edit = existing is not None and existing.sheet_row is not None
    sheet_row = existing.sheet_row if is_edit else None

    # 2. Если нет sheet_row, сначала ищем в Google Sheets (ПЕРЕД отправкой!)
    if sheet_row is None:
        try:
            lookup = await sheets_client.lookup(data.get("telegram", ""), data.get("full_name", ""))
            if lookup.found:
                sheet_row = lookup.row
                is_edit = True  # найдено в таблице = нужно обновлять, а не добавлять
        except SheetsError as e:
            logger.warning("lookup failed (non-fatal): %s", e)

    # 3. Теперь отправляй в Google Sheets (обновляй или добавляй)
    try:
        logger.info(f"Saving to sheets: is_edit={is_edit}, sheet_row={sheet_row}")
        if is_edit and sheet_row is not None:
            # Правим существующую строку, а не плодим дубликат
            logger.info(f"Updating row {sheet_row}")
            await sheets_client.update_row(sheet_row, data, chat_id=chat_id)
        else:
            # Новая регистрация
            logger.info("Creating new row")
            result = await sheets_client.register_creator(data, chat_id=chat_id)
            logger.info(f"Register result: {result}")
    except SheetsError as e:
        logger.error("register/update failed: %s", e)
        await render_screen(
            bot, chat_id, "😔 Не получилось сохранить анкету — попробуйте ещё раз чуть позже (/start)."
        )
        await state.clear()
        return

    # 4. Сохрани в локальной БД с sheet_row
    await upsert_creator(tg_id, username=call.from_user.username, fields=data, sheet_row=sheet_row)
    await state.clear()

    await render_screen(
        bot,
        chat_id,
        "🎉 Готово! Анкета сохранена. Мы на связи, если появятся подходящие проекты.",
        reply_markup=profile_edit_keyboard(),
    )
    await send_persistent_menu(bot, chat_id, "Меню всегда под рукой 👇", main_menu(settings.is_admin(tg_id)))
