#!/usr/bin/env python3
"""PapKids: наш трафик из WB по ПОИСКОВЫМ ЗАПРОСАМ → лист «WB запросы».

Подменный артикул = поисковый запрос: зритель видит «Артикул: #WW645476» в ролике и
вбивает его в поиск WB. Поэтому берём отчёт по поисковым запросам
(POST /api/v2/search-report/product/search-texts) и оставляем строки, где текст запроса —
один из наших кодов WW.

Важное отличие от отчёта кабинета по подменникам: один код приводит людей СРАЗУ В
НЕСКОЛЬКО карточек (ww649543 → четыре поильника разных цветов), и переходы по «соседним»
карточкам кабинет не показывает. Здесь суммируем по всем карточкам.

Чужие коды (ww405232 и подобные — подменники других продавцов, по которым наши карточки
тоже показываются) отбрасываем: список наших берём из листа «WB отчёты».

Запуск: python papkids_wb_queries.py [YYYY-MM-DD начало периода]
"""
import datetime
import json
import re
import sys
import time
import urllib.error
import urllib.request

import papkids_push as pp

KEY = pp._env("WB_API_KEY")
URL = "https://seller-analytics-api.wildberries.ru/api/v2/search-report/product/search-texts"
SHEET = "WB запросы"
START = "2026-07-16"
HEAD = ["Ключ", "Товар", "Показов в поиске", "Переходы", "В корзину", "Заказы",
        "Карточек по ключу", "Средняя позиция", "Период", "Обновлено"]


def ours():
    """{код WW: (nmID «своей» карточки, товар)} — из листа «WB отчёты» (импорт кабинета)."""
    rows = pp._webhook({"action": "grid_dump", "sheet_name": "WB отчёты"}).get("values", [])[1:]
    out = {}
    for r in rows:
        if len(r) > 8 and str(r[1]).strip().upper().startswith("WW") and str(r[8]).strip().isdigit():
            out[str(r[1]).strip().upper()] = (int(r[8]), str(r[2]))
    return out


def search_texts(nm_ids, start, end, limit=100):
    """Поисковые запросы по нашим карточкам. limit — на КАЖДУЮ карточку (максимум 100)."""
    body = {"currentPeriod": {"start": start, "end": end}, "nmIds": list(nm_ids),
            "topOrderBy": "openCard", "orderBy": {"field": "openCard", "mode": "desc"},
            "limit": limit}
    req = urllib.request.Request(URL, headers={"Authorization": KEY, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode())
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())["data"]["items"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(25)
                continue
            raise RuntimeError(f"WB API {e.code}: {e.read()[:200].decode(errors='replace')}") from e


def _cur(v):
    return (v or {}).get("current") or 0


def collect(start, end, mine=None):
    """Свод по нашим кодам за период: {код: {freq, open, cart, ord, cards, pos}}."""
    mine = mine or ours()
    items = search_texts({nm for nm, _t in mine.values()}, start, end)
    agg = {}
    for it in items:
        code = re.sub(r"\s+", "", str(it.get("text", ""))).upper()
        if code not in mine:                       # чужие ww-коды и обычные запросы — мимо
            continue
        a = agg.setdefault(code, {"freq": 0, "open": 0, "cart": 0, "ord": 0, "cards": 0, "pos": []})
        a["freq"] = max(a["freq"], _cur(it.get("frequency")))   # частота — свойство запроса, не сумма
        a["open"] += _cur(it.get("openCard"))
        a["cart"] += _cur(it.get("addToCart"))
        a["ord"] += _cur(it.get("orders"))
        a["cards"] += 1
        if _cur(it.get("avgPosition")):
            a["pos"].append(_cur(it.get("avgPosition")))
    return agg


def avg_price():
    """{код: средний чек карточки, ₽} из листа «WB карточки»."""
    out = {}
    try:
        for r in pp._webhook({"action": "grid_dump", "sheet_name": "WB карточки"})["values"][1:]:
            if len(r) > 10 and str(r[2]).strip():
                out[str(r[2]).strip().upper()] = r[10] or 0
    except Exception:
        pass
    return out


def totals(start, end):
    """Итоги за период (для «за вчера» в отчёте): переходы, корзина, заказы, выручка.
    Выручка — по ЗАКАЗАМ (заказ ≠ выкуп): заказы × средний чек карточки."""
    agg = collect(start, end)
    price = avg_price()
    return {"open": sum(a["open"] for a in agg.values()),
            "cart": sum(a["cart"] for a in agg.values()),
            "orders": sum(a["ord"] for a in agg.values()),
            "revenue": round(sum(a["ord"] * price.get(c, 0) for c, a in agg.items()))}


def run(start=START):
    if not KEY:
        raise SystemExit("нет WB_API_KEY в .env")
    mine = ours()
    if not mine:
        raise SystemExit("нет списка наших кодов — сначала импортируй отчёт кабинета (papkids_wb_report.py)")
    end = datetime.date.today().isoformat()
    period = f"{start} — {end}"
    agg = collect(start, end, mine)

    rows =[[code, mine[code][1], a["freq"], a["open"], a["cart"], a["ord"], a["cards"],
             round(sum(a["pos"]) / len(a["pos"]), 1) if a["pos"] else "", period, pp.NOW]
            for code, a in agg.items()]
    rows.sort(key=lambda r: -r[3])
    for code, (_nm, name) in mine.items():          # коды без единого перехода — тоже строкой
        if code not in agg:
            rows.append([code, name, 0, 0, 0, 0, 0, "", period, pp.NOW])
    pp._webhook({"action": "grid_write", "sheet_name": SHEET, "clear": True, "row": 1,
                 "rows": [HEAD] + rows})
    pp._webhook({"action": "set_format", "sheet_name": SHEET, "range": "J2:J200",
                 "format": "dd.MM.yyyy HH:mm"})
    t = [sum(r[i] for r in rows) for i in (3, 4, 5)]
    return (f"ключей {len(rows)} (с трафиком {len(agg)}) за {period}: "
            f"переходы {t[0]}, корзина {t[1]}, заказы {t[2]}")


if __name__ == "__main__":
    print(run(sys.argv[1] if len(sys.argv) > 1 else START))
