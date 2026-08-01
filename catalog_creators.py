"""
Генерация полей карточки-товара каталога Тильды (раздел «UGC-креаторы») из данных
креатора в БД бота.

    title  = «Элира А»                    (короткое имя)
    descr  = красноречивое описание-копирайт (ниша + польза бренду)
    text   = структурный блок: ГЕО, Возраст, «Отлично подойдёт для»

Дальше поля уходят в CSV-импорт / store-API Тильды (+ Photo = публичные URL с Selectel).
Здесь только чистая генерация текста, без сети и БД — чтобы её можно было тестить.
"""
from __future__ import annotations

import random

from bot.cards import guess_gender, short_name


# ---------- склонение города в родительный падеж («из <city>») ----------
# ponytail: эвристика, не морфоанализатор. Покрывает русские города из нашей базы
# (оканчивающиеся на согласную/-а/-ь). Апгрейд при промахах — pymorphy2.
_HUSH = "жшчщ"  # после шипящих и к/г/х пишем -и, а не -ы (правило «жи-ши»)


def city_genitive(city: str | None) -> str:
    """Город в род.падеже для «из <city>». Несклоняемые/иностранные — как есть."""
    c = (city or "").strip().replace(" - ", "-")
    if not c:
        return ""
    if "-на-" in c.lower():                       # Ростов-на-Дону → Ростова-на-Дону
        head, tail = c.split("-", 1)
        return f"{city_genitive(head)}-{tail}"
    if "-" in c:                                  # Санкт-Петербург → Санкт-Петербурга
        head, tail = c.rsplit("-", 1)
        return f"{head}-{city_genitive(tail)}"

    low = c.lower()
    last = low[-1]
    stem_last = low[-2] if len(low) >= 2 else ""
    if last in "оеиуыэюё":                         # Сочи, Иваново, Аликанте, Баку
        return c
    if last == "а":                               # Москва→Москвы, Калуга→Калуги
        return c[:-1] + ("и" if stem_last in _HUSH + "кгх" else "ы")
    if last == "я":
        return c[:-1] + "и"
    if last == "ь":                               # Тюмень→Тюмени, Пермь→Перми
        return c[:-1] + "и"
    if last == "й":
        return c[:-1] + "я"
    return c + "а"                                # согласная: Краснодар→Краснодара


# ---------- ниша и «подойдёт для» из категорий анкеты ----------
_NICHE: dict[str, str] = {
    "Бьюти": "бьюти и уход",
    "Мода": "моду и стиль",
    "Фитнес/спорт": "спорт и ЗОЖ",
    "Еда": "еду и рецепты",
    "Тревел": "путешествия",
    "Лайфстайл": "лайфстайл",
    "Гейминг": "гейминг",
    "Юмор/актёрка": "юмор",
    "Мамы/дети": "семью и детей",
    "Техно": "технику и гаджеты",
}
# Категория → покупательские «отлично подойдёт для» (по образцу боевой карточки).
_FIT_FOR: dict[str, list[str]] = {
    "Мода": ["Женской и мужской одежды", "Обуви", "Аксессуаров"],
    "Бьюти": ["Косметики и ухода", "Украшений"],
    "Фитнес/спорт": ["Спорттоваров и одежды", "Спортпита"],
    "Еда": ["Продуктов и доставки еды", "Посуды и товаров для кухни"],
    "Тревел": ["Тревел-товаров", "Отелей и туров"],
    "Лайфстайл": ["Бытовой техники и товаров для дома", "Товаров для животных"],
    "Гейминг": ["Гаджетов и игр"],
    "Юмор/актёрка": ["Развлекательных и брендовых интеграций"],
    "Мамы/дети": ["Детских товаров и игрушек", "Товаров для мам"],
    "Техно": ["Электроники и гаджетов", "Бытовой техники и товаров для дома"],
}


def _split_categories(raw: str | None) -> list[str]:
    return [p.strip() for p in (raw or "").replace(";", ",").split(",") if p.strip()]


def _dedup(seq: list[str]) -> list[str]:
    out: list[str] = []
    for x in seq:
        if x not in out:
            out.append(x)
    return out


def niche_phrase(categories: str | None, limit: int = 2) -> str:
    """«моду и стиль, бьюти и уход» — склейка запятой (не « и », т.к. в темах уже есть «и»)."""
    parts = _dedup([_NICHE[c] for c in _split_categories(categories) if c in _NICHE])[:limit]
    return ", ".join(parts)


def fit_for(categories: str | None, limit: int = 5) -> list[str]:
    items: list[str] = []
    for cat in _split_categories(categories):
        items += _FIT_FOR.get(cat, [])
    return _dedup(items)[:limit]


# ---------- красноречивое описание ----------
_VIBE = ["обаятельный", "харизматичный", "тёплый", "искренний", "самобытный",
         "яркий", "лёгкий", "живой", "стильный", "вдумчивый", "заряжающий"]
_CONTENT_PL = ["живые", "цепляющие", "тёплые", "атмосферные", "честные", "стильные"]
_CONTENT_SG = ["живой", "цепляющий", "тёплый", "атмосферный", "честный", "стильный", "искренний"]
_TAIL = [
    "товар в кадре выглядит так, что хочется купить",
    "контент, которому веришь с первой секунды",
    "ролики, которые хочется досмотреть до конца",
    "искренние эмоции и картинка без наигранности",
    "рассказывает про продукт, будто советует подруге",
    "естественная подача и живой монтаж",
    "бренд получает контент, который цепляет ленту",
]
_ROLE = ["UGC-креатор", "креатор", "автор контента"]


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _first_name(full_name: str | None) -> str:
    parts = [p for p in (full_name or "").split() if p]
    return parts[0] if parts else "Креатор"


# Короткое описание в стиле боевых карточек: «{Прилагательное} {существительное} из {Города}. {Возраст}».
_ADJ_F = ["Обаятельная", "Милая", "Знойная", "Чувственная", "Харизматичная", "Лучезарная",
          "Стильная", "Яркая", "Нежная", "Эффектная", "Тёплая", "Улыбчивая", "Утончённая"]
_ADJ_M = ["Обаятельный", "Харизматичный", "Стильный", "Яркий", "Эффектный", "Тёплый",
          "Уверенный", "Брутальный", "Статный"]
_NOUN_F = ["девушка", "красотка", "модель"]
_NOUN_M = ["парень", "модель"]


def build_descr(full_name: str | None, city: str | None, age: str | None,
                rnd: random.Random) -> str:
    """«Знойная красотка из Краснодара. 25 лет» — коротко, как в боевых карточках."""
    male = guess_gender(full_name) == "male"
    adj = rnd.choice(_ADJ_M if male else _ADJ_F)
    noun = rnd.choice(_NOUN_M if male else _NOUN_F)
    loc = f" из {city_genitive(city)}" if (city or "").strip() else ""
    ageph = _age_phrase(age)
    return f"{adj} {noun}{loc}." + (f" {ageph}." if ageph else "")


def _age_phrase(age: str | None) -> str:
    try:
        a = int(str(age).strip())
    except (TypeError, ValueError):
        return ""
    if a <= 0 or a > 99:
        return ""
    d = a % 100
    if 11 <= d <= 14:
        w = "лет"
    else:
        u = a % 10
        w = "год" if u == 1 else "года" if u in (2, 3, 4) else "лет"
    return f"{a} {w}"


def catalog_fields(full_name: str | None, city: str | None, age: str | None,
                   categories: str | None = None, *, seed: str | None = None) -> dict[str, str]:
    """{'title','descr','text'} для карточки. seed фиксирует выбор слов —
    повторный прогон одного креатора даёт тот же текст."""
    rnd = random.Random(seed or full_name)
    descr = build_descr(full_name, city, age, rnd)

    # ТЕКСТ — HTML (как в боевых карточках Тильды: <br />, <strong>, <ul><li data-list="bullet">).
    city_clean = (city or "").strip().replace(" - ", "-")
    head = []
    if city_clean:
        head.append(f"ГЕО: {city_clean}")
    ageph = _age_phrase(age)
    if ageph:
        head.append(f"Возраст: {ageph}")
    text_html = "<br />".join(head)
    items = fit_for(categories)
    if items:
        lis = "".join(f'<li data-list="bullet">{x}</li>' for x in items)
        if text_html:
            text_html += "<br /><br />"
        text_html += f"<strong>Отлично подойдёт для:</strong><br /><br /><ul>{lis}</ul>"

    title = short_name(full_name)
    gen = city_genitive(city_clean)
    loc = f" из {gen}" if gen else ""
    niche = niche_phrase(categories, limit=3)

    seo_title = f"{title} — UGC-креатор для брендов | Packman Production"
    seo_descr = f"{title} — UGC-креатор{loc}."
    if niche:
        seo_descr += f" Снимает {niche} для брендов и маркетплейсов."
    seo_descr += " Живой контент под ключ — Packman Production."

    kw = ["UGC-креатор", "UGC контент", "съёмка контента для брендов",
          "реклама на маркетплейсах"]
    if city_clean:
        kw.insert(2, city_clean)
    if niche:
        kw.append(niche)
    kw.append("Packman Production")
    seo_keywords = ", ".join(dict.fromkeys(kw))  # без дублей, порядок сохраняем

    return {"title": title, "descr": descr, "text": text_html,
            "seo_title": seo_title, "seo_descr": seo_descr,
            "seo_keywords": seo_keywords, "fb_title": seo_title, "fb_descr": seo_descr}


def _demo() -> None:
    cases = {
        "Краснодар": "Краснодара", "Саратов": "Саратова", "Оренбург": "Оренбурга",
        "Тюмень": "Тюмени", "Пермь": "Перми", "Москва": "Москвы", "Уфа": "Уфы",
        "Калуга": "Калуги", "Санкт-Петербург": "Санкт-Петербурга",
        "Ростов-на-Дону": "Ростова-на-Дону", "Сочи": "Сочи", "Аликанте": "Аликанте",
        "Челябинск": "Челябинска",
    }
    for src, exp in cases.items():
        assert city_genitive(src) == exp, f"{src}: {city_genitive(src)!r} != {exp!r}"

    assert _age_phrase("21") == "21 год" and _age_phrase("22") == "22 года"
    assert _age_phrase("25") == "25 лет" and _age_phrase("шапка") == ""

    f = catalog_fields("Элира Астахова", "Краснодар", "25",
                       "Мамы/дети, Фитнес/спорт, Мода, Бьюти", seed="x")
    assert f["title"] == "Элира А"
    assert "Краснодар" in f["descr"] and "Средние охваты" not in f["text"]
    assert f["text"].startswith("ГЕО: Краснодар<br />Возраст: 25 лет")
    assert '<ul><li data-list="bullet">' in f["text"]
    assert f["seo_title"].endswith("Packman Production")
    print("descr:", f["descr"])
    print("text:", f["text"])
    print("seo:", f["seo_title"])


if __name__ == "__main__":
    _demo()
