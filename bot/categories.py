"""Категории креаторов — используются и в анкете, и в фильтрах брендов."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CATEGORIES = [
    "Бьюти",
    "Фитнес/спорт",
    "Мода",
    "Еда",
    "Тревел",
    "Лайфстайл",
    "Гейминг",
    "Юмор/актёрка",
    "Мамы/дети",
    "Техно",
]


def categories_keyboard(selected: set[str] | None = None) -> InlineKeyboardMarkup:
    selected = selected or set()
    rows = []
    row: list[InlineKeyboardButton] = []
    for i, cat in enumerate(CATEGORIES, start=1):
        mark = "✅ " if cat in selected else ""
        row.append(InlineKeyboardButton(text=f"{mark}{cat}", callback_data=f"cat:{cat}"))
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Готово ➡️", callback_data="cat:done")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
