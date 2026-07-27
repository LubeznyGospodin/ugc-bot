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
    "сюмбель", "гульнур", "энже", "лилиан", "мадлен", "жанат", "айым",
}
# Имена, одинаково используемые у обоих полов → тег не ставим.
_UNISEX_FIRST = {"саша", "женя", "валя", "слава", "айнур"}


# Суффиксы фамилий — по ним слово опознаётся как фамилия, а не имя.
_SURNAME_SUFFIX = ("ова", "ева", "ёва", "ина", "ына", "ская", "цкая", "ов", "ев", "ёв",
                   "ин", "ын", "ский", "цкий", "ых", "их", "ко", "ук", "юк", "швили",
                   "дзе", "ян", "енко")


def _name_gender(w: str) -> str | None:
    """Пол по ОДНОМУ слову — только если это уверенно имя. Иначе None (не гадаем)."""
    if w in _UNISEX_FIRST:
        return None
    if w in _MALE_FIRST:
        return "male"
    if w in _FEMALE_FIRST:
        return "female"
    if w in _MALE_SURNAMES or w.endswith(_SURNAME_SUFFIX):
        return None  # это фамилия
    if w.endswith(("а", "я")):
        return "female"
    if w.endswith(("о", "у", "ы", "э", "ю", "и", "е", "ё", "ь")):
        return None  # нетипичное для имени окончание (Русу, Софико) → CV решит
    return "male"  # согласная/-й, не фамилия → мужское имя (Даниил, Сергей, Глеб)


def _is_given(w: str) -> bool:
    return _name_gender(w) is not None or w in _UNISEX_FIRST


def guess_gender(full_name: str | None) -> str | None:
    """'female' | 'male' | None (не уверены → тег не ставим / зовём CV).

    Оцениваем ОБА слова: имя может стоять и вторым («Русу Анастасия» — фамилия первой).
    Берём уверенную оценку по слову-имени; при конфликте/непонятности — None."""
    parts = [p.strip(".,") for p in (full_name or "").split() if p.strip(".,")]
    if not parts:
        return None
    words = [p.lower() for p in parts[:2]]
    if not all(all("а" <= c <= "я" or c in "ё-" for c in w) for w in words):
        return None  # латиница/эмодзи/мусор — CV

    guesses = [g for g in (_name_gender(w) for w in words) if g]
    if guesses:
        return guesses[0] if all(g == guesses[0] for g in guesses) else None
    # имя не распозналось — последний шанс по склоняемой фамилии
    for w in words:
        if w.endswith(("ова", "ева", "ёва", "ина", "ына", "ская", "цкая")):
            return "female"
        if w.endswith(("ов", "ев", "ёв", "ин", "ын", "ский", "цкий")):
            return "male"
    return None  # неоднозначно → компьютерное зрение (/watch, /video-eyes)


def gender_tag(full_name: str | None) -> str | None:
    g = guess_gender(full_name)
    return {"female": "девушка", "male": "мужчина"}.get(g or "")


def short_name(full_name: str | None) -> str:
    """«Ксения Волкова» → «Ксения В». Отчество/лишние слова отбрасываем."""
    parts = [p for p in (full_name or "").replace(" ", " ").split() if p]
    if not parts:
        return "—"
    if len(parts) == 1:
        return _cap(parts[0])
    p0, p1 = parts[0], parts[1]
    # если первое слово — не имя, а второе — имя, значит фамилия написана первой → меняем
    if not _is_given(p0.lower()) and _is_given(p1.lower()):
        p0, p1 = p1, p0
    return f"{_cap(p0)} {p1[0].upper()}"


def _cap(w: str) -> str:
    """Заглавная первая буква, остальное как есть: «леся»→«Леся»."""
    return w[:1].upper() + w[1:] if w else w


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
