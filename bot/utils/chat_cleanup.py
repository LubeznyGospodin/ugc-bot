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

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message, ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

_LAST_SCREEN_MSG: dict[int, int] = {}


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
    """Показать очередной "экран": убрать предыдущий экран бота, отправить новый.

    delete_trigger — сообщение пользователя, которое вызвало этот экран
    (например, введённый текст анкеты); если передано, оно тоже удаляется,
    чтобы не засорять чат.
    """
    if delete_trigger is not None:
        await _safe_delete(bot, chat_id, delete_trigger.message_id)

    prev_id = _LAST_SCREEN_MSG.get(chat_id)
    if prev_id is not None:
        await _safe_delete(bot, chat_id, prev_id)

    sent = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    _LAST_SCREEN_MSG[chat_id] = sent.message_id

    # Автоскролл: отправляем пустое сообщение для скролла вниз
    # (Telegram автоматически скроллит к новым сообщениям)
    try:
        spacer = await bot.send_message(chat_id, "​‌")  # zero-width space + zero-width joiner
        # Сразу удаляем спейсер чтобы он был невидим
        await _safe_delete(bot, chat_id, spacer.message_id)
    except Exception as e:
        logger.debug("autoscroll spacer failed: %s", e)

    return sent


async def send_persistent_menu(bot: Bot, chat_id: int, text: str, reply_markup: ReplyKeyboardMarkup) -> None:
    """Отдельно от render_screen: используется один раз (при первом /start),
    чтобы поставить нижнее меню — его не трогаем при последующих render_screen,
    т.к. reply-keyboard не привязана к конкретному сообщению."""
    await bot.send_message(chat_id, text, reply_markup=reply_markup)


def forget_screen(chat_id: int) -> None:
    """Сбросить память об экране (например, при /start с нуля)."""
    _LAST_SCREEN_MSG.pop(chat_id, None)
