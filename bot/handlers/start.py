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
from bot.keyboards import BTN_HELP, confirm_dedup_keyboard, main_menu
from bot.sheets import LookupResult, SheetsError, sheets_client
from bot.states import Dedup
from bot.utils.chat_cleanup import ensure_menu, render_loading, render_screen
from bot.utils.db_helpers import record_visit, upsert_creator

logger = logging.getLogger(__name__)
router = Router(name="start")


def _profile_summary(data: dict) -> str:
    return (
        f"👤 <b>{data.get('full_name') or '—'}</b>\n"
        f"Telegram: {data.get('telegram') or '—'}\n"
        f"Instagram: {data.get('instagram') or '—'}"
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()

    # Фиксируем ЗАХОД в бот (для воронки CJM) — неблокирующе, дедуп по tg_id. Считаем
    # всех, кто нажал /start, даже если дальше не пошли.
    _u = message.from_user
    _visit_name = " ".join(filter(None, [_u.first_name, _u.last_name])) or None
    asyncio.create_task(
        record_visit(_u.id, f"@{_u.username}" if _u.username else None, _visit_name)
    )

    # НЕ забываем прошлый экран: пусть render_screen ниже удалит его (иначе после
    # /start в чате повисает старый экран, напр. «Готово, анкета сохранена»).

    # Нижнее меню ставим ОТДЕЛЬНЫМ сообщением-якорем (один раз на чат). Оно НЕ входит
    # в цепочку render_screen, поэтому не удаляется при смене экранов — кнопки не
    # пропадают (фикс бага «при узнавании всё удаляется»). И это не спам: одно сообщение.
    await ensure_menu(bot, message.chat.id, main_menu(settings.is_admin(message.from_user.id)))

    telegram_handle = f"@{message.from_user.username}" if message.from_user.username else ""
    full_name_guess = " ".join(
        filter(None, [message.from_user.first_name, message.from_user.last_name])
    )

    # Поиск идёт ПАРАЛЛЕЛЬНО с анимацией: запрос к таблице летит, пока крутится
    # индикатор, и мы его дожидаемся сразу как пришёл ответ.
    async def _do_lookup() -> LookupResult:
        try:
            return await sheets_client.lookup(telegram_handle, full_name_guess)
        except SheetsError as e:
            logger.warning("lookup failed: %s", e)
            return LookupResult(found=False)

    lookup_task = asyncio.create_task(_do_lookup())

    # Экран загрузки с анимированным custom-emoji (сам эмодзи и есть анимация). /start
    # — это ввод юзера, поэтому delete_trigger=message: индикатор появится НИЖЕ эха
    # команды (самым нижним → скролл к нему). Результат ниже правит его на месте.
    await render_loading(bot, message.chat.id, "Ищу тебя в базе…", delete_trigger=message)
    result = await lookup_task

    if not result.found:
        # Кнопки/ввода ниже нет → render_screen ПРАВИТ экран-загрузку на месте (внизу).
        await render_screen(
            bot,
            message.chat.id,
            "🆕 Не нашёл тебя в наших списках — давай знакомиться!\n\n"
            "Как тебя зовут (имя и фамилия)?",
        )
        from bot.states import Registration

        await state.set_state(Registration.full_name)
        return

    # Найден в базе (любая уверенность) — показываем Имя/Telegram/Instagram и просим
    # подтвердить «это я?». Кнопка «Да, это я!» отметит в таблице «Есть в боте»=да и
    # Chat ID (см. dedup_confirm → _finish_recognized). Заново заполнять НЕ просим.
    profile = result.data or {}
    await render_screen(
        bot,
        message.chat.id,
        "🔎 Нашёл тебя в базе:\n\n" + _profile_summary(profile) + "\n\nЭто ты?",
        reply_markup=confirm_dedup_keyboard(),
    )
    await state.update_data(lookup_row=result.row, lookup_data=profile)
    await state.set_state(Dedup.waiting_confirmation)


async def _finish_recognized(bot: Bot, chat_id: int, tg_id: int, result: LookupResult) -> None:
    # Экран показываем СРАЗУ (правка дедуп-экрана на месте — мгновенно, внизу). Запись
    # «Есть в боте»=да/Chat ID в таблицу и БД — в ФОНЕ, чтобы вебхук не задерживал показ.
    text = (
        "✅ Отлично, узнал тебя! Ты в базе Packman.\n\n"
        + _profile_summary(result.data or {})
        + "\n\nОбновить данные можно в «🧾 Моя анкета»."
    )
    await render_screen(bot, chat_id, text)

    async def _persist() -> None:
        try:
            await sheets_client.update_row(result.row, {}, chat_id=chat_id)
        except SheetsError as e:
            logger.warning("update_row on recognize failed: %s", e)
        await upsert_creator(tg_id, username=None, fields=result.data or {}, sheet_row=result.row)

    asyncio.create_task(_persist())


@router.callback_query(F.data == "dedup:confirm")
async def dedup_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    row = data.get("lookup_row")
    profile = data.get("lookup_data") or {}
    result = LookupResult(found=True, row=row, data=profile)
    await state.clear()
    await call.answer()  # без попапа — результат виден на экране
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


@router.callback_query(F.data == "nudge:register")
async def nudge_register(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Кнопка из пуш-напоминания — сразу заводим анкету (эти люди ещё не в базе)."""
    await state.clear()
    await call.answer()
    await ensure_menu(bot, call.message.chat.id, main_menu(settings.is_admin(call.from_user.id)))
    from bot.states import Registration

    await render_screen(
        bot,
        call.message.chat.id,
        "🚀 Отлично! Давай знакомиться.\n\nКак тебя зовут (имя и фамилия)?",
    )
    await state.set_state(Registration.full_name)


@router.message(F.text == BTN_HELP)
async def help_handler(message: Message, bot: Bot):
    await render_screen(
        bot,
        message.chat.id,
        "ℹ️ Это бот UGC-креаторов Packman Production.\n\n"
        "🧾 Моя анкета — посмотреть/обновить свои данные\n"
        "🎯 Запросы брендов — актуальные проекты для отклика\n"
        "📨 Мои отклики — статус твоих откликов на бренды\n\n"
        "Если что-то не работает — напишите в чат команды.",
        delete_trigger=message,
    )
