"""
"Одно эволюционирующее сообщение" + чистка чата (критерий приёмки #1).

Идея: у бота в каждом чате есть ровно одно "рабочее" сообщение (экран).
Когда нужно показать следующий шаг — старое рабочее сообщение удаляется
(или, если Telegram не даёт удалить, редактируется), отправляется новое.
Входящие сообщения пользователя (текст анкеты, нажатия) тоже подчищаются,
чтобы в чате не копился мусор — остаётся только текущий экран.

Хранение last_message_id — в памяти процесса (per chat_id). Это не критичная
для данных информация: если бот перезапустится, просто начнётся новый экран
вместо удаления старого — не потеря данных, а чисто визуальный нюанс.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, MessageEntity, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

_LAST_SCREEN_MSG: dict[int, int] = {}
# Чаты, где уже поставлено нижнее reply-меню (сообщение-якорь). Меню-якорь НЕ
# входит в цепочку render_screen (не удаляется), поэтому кнопки не пропадают при
# смене экранов. Отправляется один раз на чат (в памяти процесса).
_MENU_SET: set[int] = set()


async def ensure_menu(bot: Bot, chat_id: int, reply_markup: ReplyKeyboardMarkup) -> None:
    """Поставить нижнее reply-меню один раз на чат отдельным сообщением-якорем.
    Это сообщение НЕ трогается render_screen, поэтому меню не исчезает при
    удалении экранов (фикс бага «при узнавании пропадают кнопки»)."""
    if chat_id in _MENU_SET:
        return
    try:
        await bot.send_message(chat_id, "📋 Меню — кнопки снизу 👇", reply_markup=reply_markup)
        _MENU_SET.add(chat_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("ensure_menu failed: %s", e)


def reset_menu(chat_id: int) -> None:
    """Забыть, что меню было поставлено (например, после временной reply-клавиатуры
    «Поделиться контактом», чтобы вернуть основное меню)."""
    _MENU_SET.discard(chat_id)


async def _safe_delete(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id, message_id)
    except TelegramBadRequest:
        # Сообщение старше 48ч или уже удалено — не критично, просто пропускаем.
        pass


async def render_screen(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
    *,
    delete_trigger: Message | None = None,
    entities: list[MessageEntity] | None = None,
) -> Message:
    """Показать очередной "экран" бота с НАДЁЖНЫМ автоскроллом.

    Ключевая идея: автоскролл «слетал» из-за удаления сообщения у нижнего края в один
    такт с показом нового экрана. Поэтому два разных пути:

    • delete_trigger is None (нажата инлайн-кнопка/продолжение без ввода юзера) — наш
      текущий экран уже у самого низа, ПОД ним ничего нет → РЕДАКТИРУЕМ его на месте.
      Лента вообще не двигается, скроллу неоткуда «слетать», чат чистый.
    • delete_trigger задан (юзер ввёл текст — его эхо появилось НИЖЕ нашего экрана) →
      ШЛЁМ новое сообщение (оно ниже эха, значит самое нижнее → клиент проматывает).
      Прошлый экран теперь ≥2 от низа (между ним и новым — эхо), удаляем его в фоне;
      эхо-ответ НЕ трогаем (адъяцентно новому — удаление ломало бы прокрутку).
    """
    prev_id = _LAST_SCREEN_MSG.get(chat_id)

    # --- Путь РЕДАКТИРОВАНИЯ (кнопка/продолжение): экран внизу, правим на месте ---
    # edit_message_text принимает только инлайн-клавиатуру, поэтому reply-меню
    # (ReplyKeyboardMarkup) исключаем — для него всегда send.
    if (
        delete_trigger is None
        and prev_id is not None
        and not isinstance(reply_markup, ReplyKeyboardMarkup)
    ):
        edit_kwargs: dict = {"reply_markup": reply_markup}
        if entities is not None:
            edit_kwargs["entities"] = entities
            edit_kwargs["parse_mode"] = None
        try:
            edited = await bot.edit_message_text(
                text, chat_id=chat_id, message_id=prev_id, **edit_kwargs
            )
            if isinstance(edited, Message):
                _LAST_SCREEN_MSG[chat_id] = edited.message_id
                return edited
        except TelegramBadRequest:
            # текст идентичен / сообщение удалено / не редактируется → падаем в send
            pass

    # --- Путь ОТПРАВКИ (ввод текста ниже экрана, либо экрана ещё нет) ---
    send_kwargs: dict = {"reply_markup": reply_markup}
    if entities is not None:
        send_kwargs["entities"] = entities
        send_kwargs["parse_mode"] = None
    sent = await bot.send_message(chat_id, text, **send_kwargs)
    _LAST_SCREEN_MSG[chat_id] = sent.message_id

    # Прошлый экран (он выше эха — ≥2 от низа) убираем в фоне: удаление далеко от низа
    # прокрутку к новому сообщению не трогает.
    if prev_id is not None and prev_id != sent.message_id:
        asyncio.create_task(_safe_delete(bot, chat_id, prev_id))
    return sent


# --- Анимированный индикатор загрузки (custom-emoji из пака), общий для всех экранов ---
LOADING_EMOJI_SET = "LoadingStatusByTimDesign"
_loading_emoji_cache: dict[str, str] = {}


async def _get_loading_emoji(bot: Bot) -> tuple[str, str] | None:
    """(custom_emoji_id, alt) анимированного loading-эмодзи из пака, с кэшем.
    None — если пак недоступен (нет Premium у владельца/ошибка) → текстовый ⏳."""
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
    except Exception as e:  # noqa: BLE001 — сеть/недоступность пака не должна ронять экран
        logger.warning("loading emoji fetch failed: %s", e)
        return None


async def render_loading(
    bot: Bot,
    chat_id: int,
    text: str = "Загружаю…",
    *,
    delete_trigger: Message | None = None,
) -> Message:
    """Экран загрузки с АНИМИРОВАННЫМ custom-emoji (как на /start). Дальше замените
    его следующим render_screen (для кнопки — правка на месте, для ввода — новое)."""
    loading = await _get_loading_emoji(bot)
    if loading:
        cid, alt = loading
        alt_len = len(alt.encode("utf-16-le")) // 2
        entity = MessageEntity(type="custom_emoji", offset=0, length=alt_len, custom_emoji_id=cid)
        try:
            return await render_screen(
                bot, chat_id, f"{alt} {text}", delete_trigger=delete_trigger, entities=[entity]
            )
        except TelegramBadRequest:
            pass
    return await render_screen(bot, chat_id, f"⏳ {text}", delete_trigger=delete_trigger)


@asynccontextmanager
async def loading_guard(
    bot: Bot,
    chat_id: int,
    *,
    delete_trigger: Message | None = None,
    text: str = "Загружаю…",
    delay: float = 0.4,
):
    """Показать анимированную загрузку, ЕСЛИ блок внутри длится дольше `delay` сек
    (по умолчанию 0.4с). Быстрые операции (из кэша) не мигают, медленные получают
    индикатор. Возвращает dict со `shown`: True, если загрузка была показана — тогда
    итоговый render_screen делайте с delete_trigger=None (правка индикатора на месте);
    иначе передайте исходный delete_trigger.

    Использование:
        async with loading_guard(bot, chat_id, delete_trigger=msg, text="…") as lg:
            data = await slow_fetch()
        await render_screen(bot, chat_id, out, reply_markup=kb,
                            delete_trigger=None if lg["shown"] else msg)
    """
    state = {"shown": False}

    async def _show() -> None:
        try:
            await asyncio.sleep(delay)
            await render_loading(bot, chat_id, text, delete_trigger=delete_trigger)
            state["shown"] = True
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_show())
    try:
        yield state
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def send_persistent_menu(bot: Bot, chat_id: int, text: str, reply_markup: ReplyKeyboardMarkup) -> None:
    """Отдельно от render_screen: используется один раз (при первом /start),
    чтобы поставить нижнее меню — его не трогаем при последующих render_screen,
    т.к. reply-keyboard не привязана к конкретному сообщению."""
    await bot.send_message(chat_id, text, reply_markup=reply_markup)


def forget_screen(chat_id: int) -> None:
    """Сбросить память об экране (например, при /start с нуля)."""
    _LAST_SCREEN_MSG.pop(chat_id, None)


def current_screen_id(chat_id: int) -> int | None:
    """id текущего «экрана» бота в чате. Нужно фоновому обновлению: обновлять
    экран только если пользователь всё ещё на нём (не ушёл в другое меню)."""
    return _LAST_SCREEN_MSG.get(chat_id)
