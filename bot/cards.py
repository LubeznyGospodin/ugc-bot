"""
Карточка креатора для канала @ugc_creatory.

Формат (по образцу заказчика):
    👤 Имя Ф            ← фамилия сокращается до первой буквы
    📍 Город

    🎬 Тематика: ...

    📸 Полное портфолио: тут
    📲 Узнать стоимость: @packman_production

    #хештеги

Параметры тела НЕ пишем (решение заказчика) — вместо них хештеги тематики,
которые креатор сам выбрал в анкете (bot/categories.py).
"""
from __future__ import annotations

CATALOG_URL = "https://packman-prod.ru/ugc_creators"
CONTACT = "@packman_production"

# Категория из анкеты → хештеги в карточке. Одна категория может дать несколько
# тегов (по ним бренды ищут в канале), поэтому список.
CATEGORY_TAGS: dict[str, list[str]] = {
    "Бьюти": ["бьюти"],
    "Фитнес/спорт": ["фитнес", "спорт"],
    "Мода": ["мода"],
    "Еда": ["еда"],
    "Тревел": ["тревел"],
    "Лайфстайл": ["лайфстайл"],
    "Гейминг": ["гейминг"],
    "Юмор/актёрка": ["юмор"],
    "Мамы/дети": ["мамы", "дети"],
    "Техно": ["техно"],
}


# Мужские имена на -а/-я — иначе правило «кончается на -а → женщина» их перепутает.
_MALE_FIRST = {
    "никита", "илья", "данила", "кузьма", "фома", "савва", "лука", "гаврила",
    "добрыня", "юра", "паша", "дима", "миша", "гоша", "лёша", "леша", "толя",
    "коля", "вова", "серёга", "сережа", "серёжа", "андрюха", "стёпа", "степа",
}
# Мужские фамилии, оканчивающиеся на -а (индеклинируемые/южнославянские).
_MALE_SURNAMES = {"глоба", "кучма", "сирота", "мазепа", "гамсахурдиа", "данелия", "берия"}
# Женские имена, кончающиеся не на -а/-я (тюркские и пр.) — иначе уйдут в «мужчина».
_FEMALE_FIRST = {
    "гузель", "айгуль", "асель", "лейсан", "алсу", "жанель", "нурай", "айсылу",
    "сюмбель", "гульнур", "энже", "лилиан", "мадлен", "жанат",
}
# Имена, одинаково используемые у обоих полов → тег не ставим.
_UNISEX_FIRST = {"саша", "женя", "валя", "слава", "айнур"}


def guess_gender(full_name: str | None) -> str | None:
    """'female' | 'male' | None (не уверены → тег в карточке не ставим).

    Правило: сперва фамилия (надёжнее всего), затем — имя, если фамилии нет.
    Публикуем в открытый канал, поэтому при любой неоднозначности лучше промолчать,
    чем подписать человека неверно."""
    parts = [p.strip(".,") for p in (full_name or "").split() if p.strip(".,")]
    if not parts:
        return None
    words = [p.lower() for p in parts]
    if not all(all("а" <= c <= "я" or c in "ё-" for c in w) for w in words[:2]):
        return None  # латиница/эмодзи/мусор — не гадаем

    # ИМЯ ВАЖНЕЕ ФАМИЛИИ. Несклоняемые фамилии (Черных, Бауэр, Кравчук, Шевченко)
    # выглядят одинаково у мужчин и женщин и пола не несут — «Дарья Черных» по фамилии
    # читалась бы как мужчина. Поэтому сперва имя, фамилия — только подстраховка.
    by_first = _by_first_name(words[0])
    if by_first:
        return by_first

    if len(words) >= 2:  # имя унисекс/незнакомое → пробуем склоняемую фамилию
        surn = words[1]
        if surn.endswith(("ова", "ева", "ёва", "ина", "ына", "ская", "цкая", "ая", "яя")):
            return "female"
        if surn.endswith(("ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ой", "ый")):
            return "male"
    return None  # фамилия несклоняемая и имя неоднозначное — молчим


def _by_first_name(first: str) -> str | None:
    if first in _UNISEX_FIRST:
        return None
    if first in _MALE_FIRST:
        return "male"
    if first in _FEMALE_FIRST:
        return "female"
    if first.endswith(("а", "я")):
        return "female"
    # На мягкий знак кончаются и женские (Гузель, Асель), и мужские (Игорь, Наиль)
    # имена — сигнал ненадёжный, отдаём решение фамилии.
    if first.endswith("ь"):
        return None
    return "male"


def gender_tag(full_name: str | None) -> str | None:
    g = guess_gender(full_name)
    return {"female": "девушка", "male": "мужчина"}.get(g or "")


def short_name(full_name: str | None) -> str:
    """«Ксения Волкова» → «Ксения В». Отчество/лишние слова отбрасываем."""
    parts = [p for p in (full_name or "").replace(" ", " ").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[1][0].upper()}"


def _split_categories(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


def card_tags(categories: str | None) -> list[str]:
    """Хештеги по категориям анкеты, без дублей, порядок сохраняем."""
    out: list[str] = []
    for cat in _split_categories(categories):
        for tag in CATEGORY_TAGS.get(cat, []):
            if tag not in out:
                out.append(tag)
    return out


def build_creator_card(
    full_name: str | None,
    city: str | None,
    categories: str | None,
    portfolio_url: str | None = None,
) -> str:
    """HTML-текст карточки. portfolio_url — персональная страница на сайте;
    если её ещё нет, ведём на общий каталог."""
    lines = [f"👤 <b>{short_name(full_name)}</b>"]
    if (city or "").strip():
        lines.append(f"📍 {city.strip()}")

    cats = _split_categories(categories)
    if cats:
        lines.append("")
        lines.append(f"🎬 Тематика: {', '.join(cats)}")

    lines.append("")
    lines.append(f'📸 Полное портфолио: <a href="{portfolio_url or CATALOG_URL}">тут</a>')
    lines.append(f"📲 Узнать стоимость: {CONTACT}")

    # Пол — первым тегом (по нему бренды фильтруют), если определился уверенно.
    tags = card_tags(categories)
    gt = gender_tag(full_name)
    if gt:
        tags = [gt] + tags
    if tags:
        lines.append("")
        lines.append(" ".join(f"#{t}" for t in tags))
    return "\n".join(lines)
