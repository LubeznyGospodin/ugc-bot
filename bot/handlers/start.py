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
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, MessageEntity

from bot.config import settings
from bot.keyboards import BTN_HELP, confirm_dedup_keyboard, main_menu
from bot.sheets import LookupResult, SheetsError, sheets_client
from bot.states import Dedup
from bot.utils.chat_cleanup import ensure_menu, forget_screen, render_screen
from bot.utils.db_helpers import upsert_creator

logger = logging.getLogger(__name__)
router = Router(name="start")


def _profile_summary(data: dict) -> str:
    return (
        f"👤 <b>{data.get('full_name') or '—'}</b>\n"
        f"Telegram: {data.get('telegram') or '—'}\n"
        f"Instagram: {data.get('instagram') or '—'}"
    )


# Пак анимированных custom-emoji для индикатора загрузки (t.me/addemoji/…).
# Бота может слать custom-emoji только если у ВЛАДЕЛЬЦА бота есть Telegram Premium;
# при любой ошибке отправки — мягкий фолбэк на текстовую анимацию (см. cmd_start).
LOADING_EMOJI_SET = "LoadingStatusByTimDesign"
# Кэш выбранного эмодзи на время жизни процесса: {"cid":.., "alt":..} при успехе.
_loading_emoji_cache: dict[str, str] = {}


async def _get_loading_emoji(bot: Bot) -> tuple[str, str] | None:
    """(custom_emoji_id, alt) анимированного loading-эмодзи из пака, с кэшем.
    None — если пак недоступен: тогда используется текстовая анимация."""
    if "cid" in _loading_emoji_cache:
        return _loading_emoji_cache["cid"], _loading_emoji_cache["alt"]
    try:
        sset = await bot.get_sticker_set(LOADING_EMOJI_SET)
        st = next((s for s in sset.stickers if s.custom_emoji_id), None)
        if st is None:
            return None
        _loading_emoji_cache["cid"] = st.custom_emoji_id
        _loading_emoji_cache["alt"] = st.emoji or "⏳"
        return _loading_emoji_cache["cid"], _loading_emoji_cache["alt"]
    except Exception as e:  # noqa: BLE001 — сеть/недоступность пака не должна ронять /start
        logger.warning("loading emoji fetch failed: %s", e)
        return None


async def _run_lookup_animation(bot: Bot, chat_id: int, screen: Message, lookup_task) -> None:
    # Крупная стильная анимация: тематический эмодзи + широкий прогресс-бар с
    # процентами, который заполняется и заново заполняется, ПОКА идёт поиск.
    STEPS = 10
    faces = ["🔎", "🛰", "📡", "🔍"]
    i = 0
    while not (lookup_task.done() and i >= STEPS):
        fill = (i % STEPS) + 1
        bar = "▰" * fill + "▱" * (STEPS - fill)
        pct = round(fill / STEPS * 100)
        face = faces[i % len(faces)]
        frame = f"{face} <b>Ищу тебя в базе…</b>\n\n<code>{bar}</code>  <b>{pct}%</b>"
        try:
            await bot.edit_message_text(frame, chat_id=chat_id, message_id=screen.message_id)
        except Exception:
            pass
        i += 1
        if i >= 60:  # предохранитель ~18с
            break
        await asyncio.sleep(0.3)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    forget_screen(message.chat.id)

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

    # Пытаемся показать экран-загрузку с анимированным custom-emoji из пака. Само
    # движение даёт эмодзи, поэтому текстовый прогресс-бар не нужен. Если пак/Premium
    # недоступны — TelegramBadRequest → мягкий фолбэк на текстовую анимацию ниже.
    screen = None
    loading = await _get_loading_emoji(bot)
    if loading:
        cid, alt = loading
        alt_len = len(alt.encode("utf-16-le")) // 2  # длина в UTF-16, как ждёт Bot API
        entity = MessageEntity(type="custom_emoji", offset=0, length=alt_len, custom_emoji_id=cid)
        try:
            screen = await render_screen(
                bot,
                message.chat.id,
                f"{alt} Ищу тебя в базе…",
                delete_trigger=message,
                entities=[entity],
            )
        except TelegramBadRequest as e:
            logger.warning("animated loading unavailable, fallback to text: %s", e)
            screen = None

    if screen is not None:
        # Анимация — сам эмодзи; просто ждём ответ поиска.
        result = await lookup_task
    else:
        # Фолбэк: текстовый экран + бегущий прогресс-бар параллельно поиску.
        screen = await render_screen(
            bot, message.chat.id, "📡 Ищу тебя в базе...", delete_trigger=message
        )
        await _run_lookup_animation(bot, message.chat.id, screen, lookup_task)
        result = await lookup_task

    if not result.found:
        # render_screen (а не edit) — шлём новое сообщение вниз, старый экран-загрузку
        # удаляем. Telegram скроллит к новому сообщению; edit-in-place скролл не давал.
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
    try:
        await sheets_client.update_row(result.row, {}, chat_id=chat_id)
    except SheetsError as e:
        logger.warning("update_row on recognize failed: %s", e)

    await upsert_creator(tg_id, username=None, fields=result.data or {}, sheet_row=result.row)

    text = (
        "✅ Отлично, узнал тебя! Ты в базе Packman.\n\n"
        + _profile_summary(result.data or {})
        + "\n\nОбновить данные можно в «🧾 Моя анкета»."
    )
    await render_screen(bot, chat_id, text)


@router.callback_query(F.data == "dedup:confirm")
async def dedup_confirm(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    row = data.get("lookup_row")
    profile = data.get("lookup_data") or {}
    result = LookupResult(found=True, row=row, data=profile)
    await state.clear()
    await call.answer("Отлично, ты в базе!")
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
        "🧾 Моя анкета — посмотреть/обновить свои данные\n"
        "🎯 Запросы брендов — актуальные проекты для отклика\n"
        "📨 Мои отклики — статус твоих откликов на бренды\n\n"
        "Если что-то не работает — напишите в чат команды.",
        delete_trigger=message,
    )
