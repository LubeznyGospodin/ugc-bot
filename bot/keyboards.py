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

# Поля анкеты, которые можно править точечно (ключ данных -> подпись кнопки).
EDITABLE_FIELDS: list[tuple[str, str]] = [
    ("full_name", "Имя"),
    ("instagram", "Instagram"),
    ("other_socials", "Другие соцсети"),
    ("rate", "Оплата"),
    ("portfolio", "Портфолио"),
    ("photo", "Фото"),
    ("age", "Возраст"),
    ("city", "Город"),
    ("phone", "Телефон"),
    ("category", "Категории"),
]

BTN_SHARE_CONTACT = "📱 Поделиться контактом"

BTN_PROFILE = "🧾 Моя анкета"
BTN_BRANDS = "🎯 Запросы брендов"
BTN_MY_APPS = "📨 Мои отклики"
BTN_HELP = "💬 Помощь"
BTN_ADMIN = "🛠 Админка"


def main_menu(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_PROFILE), KeyboardButton(text=BTN_BRANDS)],
        [KeyboardButton(text=BTN_MY_APPS), KeyboardButton(text=BTN_HELP)],
    ]
    if is_admin:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, is_persistent=True)


def confirm_dedup_keyboard() -> InlineKeyboardMarkup:
    """Показывается на /start, когда lookup нашёл креатора в базе — сверяем данные
    и просим подтвердить «это я?». «Да» → отмечаем «Есть в боте»=да + Chat ID."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, это я!", callback_data="dedup:confirm"),
                InlineKeyboardButton(text="❌ Это не я", callback_data="dedup:reject"),
            ]
        ]
    )


def profile_edit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Обновить данные", callback_data="profile:edit")],
        ]
    )


def edit_fields_keyboard() -> InlineKeyboardMarkup:
    """Меню «что скорректировать?» — по 2 поля в ряд + кнопка Готово."""
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, (field, label) in enumerate(EDITABLE_FIELDS, start=1):
        row.append(InlineKeyboardButton(text=label, callback_data=f"editf:{field}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✅ Готово", callback_data="editf:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def contact_request_keyboard() -> ReplyKeyboardMarkup:
    """Клавиатура «Поделиться контактом» — Telegram отдаёт номер телефона."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SHARE_CONTACT, request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def brands_list_keyboard(
    brands: list[Brand], hot_ids: set[str] | None = None
) -> InlineKeyboardMarkup:
    hot_ids = hot_ids or set()
    rows = [
        [
            InlineKeyboardButton(
                text=(f"🔥 {b.title}" if b.id in hot_ids else b.title),
                callback_data=f"brand:{b.id}",
            )
        ]
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
