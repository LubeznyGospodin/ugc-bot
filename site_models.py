"""
База моделей для сайта packman-prod.ru: снимок каталога Tilda → свои данные и своё медиа.

На вход — дамп от `scripts/scrape-tilda-store.mjs` (карточки + страницы товаров Tilda).
На выход — `data/models.json` для сайта и фото в бакете Selectel (`models/<slug>/<n>.webp`).
Тащить надо обязательно: после отключения Tilda её CDN умрёт вместе с картинками.

Грабля из хендоффа (§5б.3): качать только с optim.tildacdn.com с параметрами resize —
собранный руками путь к static.tildacdn.com отдаёт 204 и пустое тело.

Запуск:  .venv/bin/python site_models.py <dump.json>
         .venv/bin/python site_models.py <dump.json> --no-upload   # только json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import boto3
import botocore
from dotenv import load_dotenv

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")

SITE = "/Users/nastasyapopova/Desktop/packman_site"
BUCKET = "packman"
PUBLIC_BASE = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru"
MAX_PHOTOS = 8          # больше в карточке не показываем, а тащить — лишний трафик

# Разделы каталога Tilda → пол/группа модели. UGC-креаторы не берём: они из своей БД.
PARTS = {
    "669800955062": "female",
    "708449535712": "male",
    "135172437192": "kids",
    "958888253272": "plus",
}
SKIP_PART = "380691270782"

# Надбавка к артикулу по категории — со страницы каталога (боевые цены Tilda).
TIER_EXTRA = {"Base": 0, "Top": 1500, "Exclusive": 2500, "Special": 5000}

_s3 = boto3.client(
    "s3", endpoint_url="https://s3.ru-7.storage.selcloud.ru",
    aws_access_key_id=os.getenv("SELECTEL_S3_KEY"),
    aws_secret_access_key=os.getenv("SELECTEL_S3_SECRET"),
    region_name="ru-7", config=botocore.config.Config(signature_version="s3v4"),
)

_SEO = {"ж": "zh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ю": "yu", "я": "ya",
        "ё": "yo", "й": "y", "ы": "y", "ь": "", "ъ": "", "э": "e", "х": "h"}
_TRANSLIT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "abvgdeejziyklmnoprstufhccss'y'eua")


def slugify(s: str) -> str:
    out = "".join(_SEO.get(ch, ch) for ch in (s or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", out.translate(_TRANSLIT)).strip("-")


# ---------- характеристики ----------
KEYS = ["Рост", "Объем груди", "Талия", "Бедра", "Размер обуви", "Размер одежды",
        "Внешность", "Категория", "Вес", "Часто снимаем", "Съемка в белье",
        "Цвет волос", "Возраст"]
_SPLIT = re.compile("(" + "|".join(map(re.escape, KEYS)) + r")\s*:\s*")

FIELD = {"Рост": "height", "Объем груди": "bust", "Талия": "waist", "Бедра": "hips",
         "Размер обуви": "shoe", "Размер одежды": "clothing", "Внешность": "look",
         "Категория": "tier", "Вес": "weight", "Часто снимаем": "often",
         "Съемка в белье": "lingerie", "Цвет волос": "hair", "Возраст": "age"}


LOOKS = ("Европейская", "Азиатская", "Темнокожая")


def clean_look(v: str | None) -> str | None:
    """В части карточек внешность слиплась с остальными мерками («Европейская Грудь
    100 Талия 78…») — оставляем только сам типаж."""
    for l in LOOKS:
        if l.lower() in (v or "").lower():
            return l
    return None


def parse_specs(s: str) -> dict[str, str]:
    """«Рост: 175 смРазмер одежды: 42-44…» → {'height': '175', 'clothing': '42-44', …}.
    Ключи в дампе слипаются без разделителя, поэтому режем по самим названиям."""
    parts = _SPLIT.split(s or "")
    out: dict[str, str] = {}
    for k, v in zip(parts[1::2], parts[2::2]):
        val = v.strip(" /·,;").replace("см", "").strip()
        if val and FIELD[k] not in out:
            out[FIELD[k]] = val
    return out


def optim_url(static_url: str, width: int = 900) -> str:
    """static.tildacdn.com/<path>/<file>.jpg → optim…/-/resize/900x/-/format/webp/…"""
    m = re.match(r"https://static\.tildacdn\.com/(.+?)/([^/]+)$", static_url)
    if not m:
        return static_url
    path, name = m.groups()
    return f"https://optim.tildacdn.com/{path}/-/resize/{width}x/-/format/webp/{name}.webp"


def _exists(key: str) -> bool:
    try:
        _s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def push_photo(src: str, key: str) -> str | None:
    url = f"{PUBLIC_BASE}/{key}"
    if _exists(key):
        return url
    req = urllib.request.Request(optim_url(src), headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://packman-prod.ru/"})
    try:
        body = urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:
        print(f"    ! {str(e)[:50]} {src[-30:]}")
        return None
    if len(body) < 1024:                     # 204/пустышка — см. граблю про static CDN
        print(f"    ! пустой ответ {src[-30:]}")
        return None
    _s3.put_object(Bucket=BUCKET, Key=key, Body=body, ContentType="image/webp")
    return url


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--parts", help="дамп probe-tilda-parts.mjs: uid → раздел каталога")
    ap.add_argument("-o", "--out", default=f"{SITE}/data/models.json")
    args = ap.parse_args()

    items = json.load(open(args.dump))["items"]
    # первый прогон скрейпера не писал раздел — доклеиваем его из отдельного дампа
    parts_map: dict[str, str] = {}
    if args.parts:
        parts_map = {c["uid"]: c.get("part") for c in json.load(open(args.parts))["cards"]
                     if c.get("part")}
    taken: set[str] = set()
    out = []
    for it in items:
        part = it.get("part") or parts_map.get(it["uid"]) or ""
        it["part"] = part.split(",")[0]     # «женский,+size» — берём первый раздел
        if it["part"] == SKIP_PART or not it.get("title"):
            continue
        specs = parse_specs(it.get("text") or "")            # точные значения модели
        ranges = parse_specs(it.get("charcs") or "")         # диапазоны для фильтров
        merged = {**ranges, **specs}

        slug = slugify(it["title"]) or f"model-{it['uid']}"
        if slug in taken:
            slug = f"{slug}-{it['uid'][-4:]}"
        taken.add(slug)

        photos_src = [u for u in dict.fromkeys(it.get("imgs") or [])
                      if "static.tildacdn.com" in u][:MAX_PHOTOS]
        if args.no_upload:
            photos = [optim_url(u) for u in photos_src]
        else:
            # Качаем и льём восемью потоками: 2600 картинок по одной — это час.
            with ThreadPoolExecutor(max_workers=8) as pool:
                photos = [u for u in pool.map(
                    lambda t: push_photo(t[1], f"models/{slug}/{t[0]}.webp"),
                    enumerate(photos_src, 1)) if u]
        if not photos:
            print(f"  ! {it['title']} — без фото, пропуск")
            continue

        # в Tilda значение поля бывает с ценой: «Top: +1500р/арт» — нужен сам разряд
        raw_tier = merged.get("tier") or "Base"
        tier = next((t for t in TIER_EXTRA if t.lower() in raw_tier.lower()), "Base")
        # Страница товара у Tilda дорисовывается скриптом, и <title> иногда остаётся
        # от предыдущего товара. Берём её SEO-строку только если она про этого человека.
        seo_ok = it.get("seoTitle", "").strip().lower().startswith(it["title"].strip().lower())
        h = merged.get("height")
        seo_title = it["seoTitle"] if seo_ok else (
            f"{it['title']} — модель для съёмок{', рост ' + h + ' см' if h else ''}, {tier}"
            " | Packman Production")
        seo_descr = it["seoDescr"] if seo_ok else (
            f"{it['title']} — профессиональная модель для фото- и видеосъёмки."
            f"{' Рост ' + h + ' см.' if h else ''} Съёмка для маркетплейсов и брендов"
            " в Packman Production.")

        out.append({
            "uid": it["uid"],
            "slug": slug,
            # адрес карточки на Tilda — с него ставим 301, он в индексе Google
            "oldPath": (it.get("url") or "").replace("https://packman-prod.ru", ""),
            "name": it["title"],
            "group": PARTS.get(it.get("part") or "", "female"),
            "tier": tier,
            "tierExtra": TIER_EXTRA.get(tier, 0),
            "height": merged.get("height"),
            "bust": merged.get("bust"),
            "waist": merged.get("waist"),
            "hips": merged.get("hips"),
            "shoe": merged.get("shoe"),
            "clothing": merged.get("clothing"),
            "look": clean_look(merged.get("look")),
            "hair": merged.get("hair"),
            "lingerie": (merged.get("lingerie") or "").lower().startswith("да"),
            "often": (merged.get("often") or "").lower().startswith("да"),
            "photos": photos,
            "seoTitle": seo_title,
            "seoDescr": seo_descr,
        })
        print(f"  ✓ {it['title']:<20} /{slug:<24} {tier:<9} {len(photos)} фото")

    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n{args.out}: {len(out)} моделей, {sum(len(m['photos']) for m in out)} фото")


def _demo() -> None:
    s = parse_specs("Рост: 175 см Размер одежды: 42-44 Объем груди: 84 см Талия: 64 см "
                    "Бедра: 93 см Размер обуви: 38 Внешность: Европейская Категория: Base")
    assert s["height"] == "175" and s["clothing"] == "42-44" and s["tier"] == "Base", s
    r = parse_specs("Рост: 173-175Объем груди: 74-78Талия: 61-64Категория: TopВнешность: Азиатская")
    assert r["height"] == "173-175" and r["tier"] == "Top" and r["look"] == "Азиатская", r
    assert slugify("Екатерина К") == "ekaterina-k"
    assert clean_look("Европейская Грудь 100 Талия 78") == "Европейская"
    assert clean_look("") is None
    t = parse_specs("Категория: Top: +1500р/арт")["tier"]
    assert next((x for x in TIER_EXTRA if x.lower() in t.lower()), "Base") == "Top", t
    u = optim_url("https://static.tildacdn.com/stor3531-3635/e62aca.jpg")
    assert u == "https://optim.tildacdn.com/stor3531-3635/-/resize/900x/-/format/webp/e62aca.jpg.webp", u
    print("ok", s)


if __name__ == "__main__":
    import sys
    if "--demo" in sys.argv:
        _demo()
    else:
        main()
