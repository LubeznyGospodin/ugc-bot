"""
/start — сердце дедупа (критерий приёмки #4) и UX-анимации проверки базы (#6).

Сценарий:
1. Показываем "🔎 Ищу тебя в наших списках..." и пару кадров анимации.
2. Дёргаем doLookup_ в Google Sheet (fuzzy-match по telegram-хендлу + имени).
3. Три исхода:
   a) точного/явного совпадения нет -> "Не нашли, давай знакомиться" -> анкета.
   b) совпадение с высокой уверенностью (>=92% или точный хендл) -> считаем
      что это тот же человек, подтягиваем его строку, ставим "Есть в боте"=да
      и chat_id, показываем профиль с кнопкой редактирования.
   c) совпадение среднее (70-92%) -> просим подтвердить по Instagram
      ("это твой профиль — @xxx?"), только после ответа "да" считаем найденным.

Во всех случаях после первого /start ставится постоянное нижнее меню.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.keyboards import BTN_HELP, confirm_dedup_keyboard, main_menu, profile_edit_keyboard
from bot.sheets import LookupResult, SheetsError, sheets_client
from bot.states import Dedup
from bot.utils.chat_cleanup import forget_screen, render_screen, send_persistent_menu
from bot.utils.db_helpers import upsert_creator

logger = logging.getLogger(__name__)
router = Router(name="start")


def _mask_instagram(value: str | None) -> str:
    if not value:
        return "(не указан)"
    # Показываем не всю ссылку, а узнаваемый, но не 100% полный фрагмент —
    # так подтверждение остаётся содержательной проверкой, а не подсказкой-ответом.
    tail = value.strip().split("/")[-1] or value.strip()
    if len(tail) <= 4:
        return tail
    return tail[:2] + "•" * (len(tail) - 4) + tail[-2:]


def _profile_summary(data: dict) -> str:
    return (
        f"👤 <b>{data.get('full_name') or '—'}</b>\n"
        f"Telegram: {data.get('telegram') or '—'}\n"
        f"Instagram: {data.get('instagram') or '—'}"
    )


async def _run_lookup_animation(bot: Bot, chat_id: int, screen: Message) -> None:
    frames = ["🔎 Ищу тебя в наших списках", "🔎 Ищу тебя в наших списках.", "🔎 Ищу тебя в наших списках.."]
    for frame in frames:
        try:
            await bot.edit_message_text(frame, chat_id=chat_id, message_id=screen.message_id)
        except Exception:
            pass
        await asyncio.sleep(0.4)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    forget_screen(message.chat.id)

    screen = await render_screen(bot, message.chat.id, "🔎 Ищу тебя в наших списках...", delete_trigger=message)
    await _run_lookup_animation(bot, message.chat.id, screen)

    telegram_handle = f"@{message.from_user.username}" if message.from_user.username else ""
    full_name_guess = " ".join(
        filter(None, [message.from_user.first_name, message.from_user.last_name])
    )

    try:
        result = await sheets_client.lookup(telegram_handle, full_name_guess)
    except SheetsError as e:
        logger.warning("lookup failed: %s", e)
        result = LookupResult(found=False)

    if not result.found:
        await bot.edit_message_text(
            "🆕 Не нашёл тебя в наших списках — давай знакомиться!\n\n"
            "Как тебя зовут (имя и фамилия)?",
            chat_id=message.chat.id,
            message_id=screen.message_id,
        )
        await send_persistent_menu(
            bot, message.chat.id, "Меню всегда под рукой 👇", main_menu(settings.is_admin(message.from_user.id))
        )
        from bot.states import Registration

        await state.set_state(Registration.full_name)
        return

    if result.needs_confirmation:
        masked = _mask_instagram(result.confirm_value)
        await bot.edit_message_text(
            f"🤔 Похоже, мы уже знакомы — <b>{result.data.get('full_name')}</b>?\n"
            f"Твой Instagram: <code>{masked}</code> — это ты?",
            chat_id=message.chat.id,
            message_id=screen.message_id,
            reply_markup=confirm_dedup_keyboard(),
        )
        await state.update_data(lookup_row=result.row, lookup_data=result.data)
        await state.set_state(Dedup.waiting_confirmation)
        return

    # Явное совпадение — сразу считаем найденным.
    await _finish_recognized(bot, message.chat.id, message.from_user.id, result)


async def _finish_recognized(bot: Bot, chat_id: int, tg_id: int, result: LookupResult) -> None:
    try:
        await sheets_client.update_row(result.row, {}, chat_id=chat_id)
    except SheetsError as e:
        logger.warning("update_row on recognize failed: %s", e)

    await upsert_creator(tg_id, username=None, fields=result.data or {}, sheet_row=result.row)

    text = "✅ Нашёл! Рад видеть снова.\n\n" + _profile_summary(result.data or {})
    await render_screen(bot, chat_id, text, reply_markup=profile_edit_keyboard())
    await send_persistent_menu(bot, chat_id, "Меню всегда под рукой 👇", main_menu(settings.is_admin(tg_id)))


@router.callback_query(F.data == "dedup:confirm")
async def dedup_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    row = data.get("lookup_row")
    profile = data.get("lookup_data") or {}
    result = LookupResult(found=True, row=row, data=profile)
    await state.clear()
    await call.answer("Отлично, обновил твои данные!")
    await _finish_recognized(bot, call.message.chat.id, call.from_user.id, result)


@router.callback_query(F.data == "dedup:reject")
async def dedup_reject(call: CallbackQuery, state: FSMContext, bot: Bot):
    await state.clear()
    await call.answer()
    from bot.states import Registration

    await render_screen(
        bot,
        call.message.chat.id,
        "Хорошо, оформим тебя как нового креатора 🙂\n\nКак тебя зовут (имя и фамилия)?",
    )
    await state.set_state(Registration.full_name)


@router.message(F.text == BTN_HELP)
async def help_handler(message: Message, bot: Bot):
    await render_screen(
        bot,
        message.chat.id,
        "ℹ️ Это бот UGC-креаторов Packman Production.\n\n"
        "👤 Моя анкета — посмотреть/обновить свои данные\n"
        "📢 Запросы брендов — актуальные проекты для отклика\n\n"
        "Если что-то не работает — напишите в чат команды.",
        delete_trigger=message,
    )
