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

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

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
) -> Message:
    """Показать очередной "экран" анкеты.

    Надёжный автоскролл (как @bro_hit_bot): СНАЧАЛА отправляем новое сообщение,
    ПОТОМ удаляем предыдущий экран. Новое сообщение приходит вниз чата — Telegram
    (в т.ч. Desktop) нативно прокручивает к нему, потому что пользователь только
    что был у низа. Удаление старого экрана ВЫШЕ не сбивает позицию низа, а
    автоскролл к новому уже сработал. (edit-in-place не давал скролла, т.к. новое
    сообщение не создавалось; старый delete→send скроллил ненадёжно.)

    delete_trigger — сообщение пользователя (введённый ответ анкеты); удаляем, чтобы
    в чате оставался только текущий экран бота.
    """
    prev_id = _LAST_SCREEN_MSG.get(chat_id)

    # 1) Отправляем новый экран — он оказывается внизу, чат скроллится к нему.
    sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    _LAST_SCREEN_MSG[chat_id] = sent.message_id

    # 1.5) Микро-пауза: даём клиенту (в т.ч. Desktop) закоммитить прокрутку к новому
    # сообщению ДО удаления старого. Без неё delete прилетает в том же апдейте и
    # рефлоу списка иногда отменяет автоскролл — отсюда «иногда не срабатывает».
    await asyncio.sleep(0.12)

    # 2) Теперь убираем предыдущий экран бота (он выше — удаление не трогает низ).
    if prev_id is not None and prev_id != sent.message_id:
        await _safe_delete(bot, chat_id, prev_id)

    # 3) И убираем эхо-ответ пользователя, чтобы чат был чистым.
    if delete_trigger is not None:
        await _safe_delete(bot, chat_id, delete_trigger.message_id)

    return sent


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
