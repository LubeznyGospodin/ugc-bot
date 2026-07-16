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
BTN_HELP = "💬 Задать вопрос"
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


def ask_question_keyboard() -> InlineKeyboardMarkup:
    """Кнопка-ссылка на HR — для «Задать вопрос»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Написать @packman_hr", url="https://t.me/packman_hr")],
        ]
    )


def nudge_keyboard() -> InlineKeyboardMarkup:
    """Кнопка под пуш-напоминанием — сразу запускает заполнение анкеты."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Заполнить анкету", callback_data="nudge:register")],
        ]
    )


def nudge_backlog_confirm_keyboard(n: int) -> InlineKeyboardMarkup:
    """Подтверждение ручной рассылки по бэклогу незарегистрированных."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"📣 Отправить {n}", callback_data="nudge_backlog:send")],
            [InlineKeyboardButton(text="Отмена", callback_data="nudge_backlog:cancel")],
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


def skip_photo_keyboard() -> InlineKeyboardMarkup:
    """Кнопка пропуска шага «фото» в регистрации — фото можно добавить позже."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ Добавить позже", callback_data="reg:photo_skip")],
        ]
    )


def photo_done_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура шага «фото», когда уже прислали фото: «Готово» + «Добавить позже»."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово, фото загрузил", callback_data="reg:photo_done")],
            [InlineKeyboardButton(text="⏭ Добавить позже", callback_data="reg:photo_skip")],
        ]
    )


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


def back_to_list_keyboard() -> InlineKeyboardMarkup:
    """Только «Назад к списку» — для экранов результата отклика (уже без «Откликнуться»)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="brands:list")],
        ]
    )


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Аналитика", callback_data="admin:stats")],
            [InlineKeyboardButton(text="📣 Рассылка", callback_data="admin:broadcast")],
            [InlineKeyboardButton(text="📤 Экспорт креаторов", callback_data="admin:export")],
            [InlineKeyboardButton(text="📥 Экспорт заходов (все)", callback_data="admin:export_visits")],
            [InlineKeyboardButton(text="🙈 Экспорт: заходили, но не зарегались", callback_data="admin:export_unreg")],
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
