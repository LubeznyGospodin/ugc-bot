"""
Клавиатуры бота.

MAIN_MENU — постоянное нижнее меню (reply-keyboard), которое не исчезает
после регистрации. Показывается один раз при /start и остаётся висеть.

Остальные клавиатуры — инлайн, привязаны к конкретному (единственному
"эволюционирующему") сообщению бота, см. bot/utils/chat_cleanup.py.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.sheets import Brand

BTN_PROFILE = "👤 Моя анкета"
BTN_BRANDS = "📢 Запросы брендов"
BTN_HELP = "❓ Помощь"
BTN_ADMIN = "⚙️ Админка"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_BRANDS)],
        [KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def confirm_dedup_keyboard() -> InlineKeyboardMarkup:
    """Показывается, когда lookup нашёл кандидата с confidence 70-92% —
    просим подтвердить, что это тот же человек, по отличающему полю (Instagram)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Это я", callback_data="dedup:confirm"),
                InlineKeyboardButton(text="❌ Не я, я новый", callback_data="dedup:reject"),
            ]
        ]
    )


def profile_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Обновить данные", callback_data="profile:edit")],
        ]
    )


def brands_list_keyboard(brands: list[Brand]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=b.title, callback_data=f"brand:{b.id}")]
        for b in brands
    ]
    # Если нет брендов, возвращаем пустую клавиатуру (без кнопок)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def brand_card_keyboard(brand_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🙋 Откликнуться", callback_data=f"brand_apply:{brand_id}")],
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="brands:list")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Аналитика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="📤 Экспорт (xlsx)", callback_data="admin:export")],
        ]
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить всем", callback_data="admin:broadcast_send"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin:broadcast_cancel"),
            ]
        ]
    )
