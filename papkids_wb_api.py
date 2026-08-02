#!/usr/bin/env python3
"""PapKids: воронка карточек из WB API → лист «WB карточки».

Зачем: отчёт по ПОДМЕННЫМ артикулам (наш трафик) выгружается только из кабинета —
в публичном WB API такой ручки нет (проверено перебором путей и по полному списку
методов). Зато API отдаёт воронку по карточке целиком, включая ВЫКУПЫ, ВЫРУЧКУ,
выкупаемость и средний чек — то, чего нет в отчёте по подменникам вообще.

Отсюда деление в таблице:
  «WB отчёты» / «WB · ROI» D:E:H  — НАШ трафик (по подменникам, из кабинета);
  «WB карточки»                   — вся карточка целиком (WB API);
  «WB · ROI» F:G                  — наши выкупы и выручка ОЦЕНКОЙ: наши заказы,
                                    умноженные на выкупаемость и средний чек карточки.

Ключ: WB_API_KEY в .env (категории «Аналитика» + «Статистика», только чтение).
Запуск: python papkids_wb_api.py [YYYY-MM-DD начало периода]
"""
import datetime
import json
import sys
import time
import urllib.error
import urllib.request

import papkids_push as pp

KEY = pp._env("WB_API_KEY")
URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"
SHEET = "WB карточки"
START = "2026-07-16"          # старт кампании PapKids
HEAD = ["Артикул WB", "Товар", "Подменник", "Переходы", "В корзину", "Заказы", "Заказы, ₽",
        "Выкупы", "Выкупы, ₽", "Выкупаемость", "Средний чек, ₽", "Период", "Обновлено"]


def nm_map():
    """{nmID: подменник WW} из листа «WB отчёты» (последний период побеждает)."""
    rows = pp._webhook({"action": "grid_dump", "sheet_name": "WB отчёты"}).get("values", [])[1:]
    return {int(r[8]): str(r[1]) for r in rows if len(r) > 8 and str(r[8]).strip().isdigit()}


def funnel(nm_ids, start, end):
    """POST воронки. WB режет по 3 запроса в минуту — на 429 ждём и повторяем."""
    body = {"selectedPeriod": {"start": start, "end": end}, "nmIds": list(nm_ids)}
    req = urllib.request.Request(URL, headers={"Authorization": KEY, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode())
    for attempt in range(6):
        try:
            return json.load(urllib.request.urlopen(req, timeout=90))["data"]["products"]
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 5:
                time.sleep(25)
                continue
            raise RuntimeError(f"WB API {e.code}: {e.read()[:200].decode(errors='replace')}") from e


def run(start=START):
    if not KEY:
        raise SystemExit("нет WB_API_KEY в .env")
    end = datetime.date.today().isoformat()
    ww = nm_map()
    if not ww:
        raise SystemExit("в «WB отчётах» нет колонки «Артикул WB» — сначала импортируй отчёт кабинета")
    period = f"{start} — {end}"
    rows = []
    for p in funnel(ww, start, end):
        pr, st = p["product"], p["statistic"]["selected"]
        cv = st.get("conversions") or {}
        rows.append([pr["nmId"], pr.get("title", ""), ww.get(pr["nmId"], ""),
                     st.get("openCount", 0), st.get("cartCount", 0), st.get("orderCount", 0),
                     st.get("orderSum", 0), st.get("buyoutCount", 0), st.get("buyoutSum", 0),
                     (cv.get("buyoutPercent") or 0) / 100, st.get("avgPrice", 0), period, pp.NOW])
    rows.sort(key=lambda r: -r[3])
    pp._webhook({"action": "grid_write", "sheet_name": SHEET, "clear": True, "row": 1,
                 "rows": [HEAD] + rows})
    pp._webhook({"action": "set_format", "sheet_name": SHEET, "range": "J2:J200", "format": "0%"})
    pp._webhook({"action": "set_format", "sheet_name": SHEET, "range": "M2:M200",
                 "format": "dd.MM.yyyy HH:mm"})
    tot = [sum(r[i] for r in rows) for i in (3, 5, 7, 8)]
    return (f"карточек {len(rows)} за {period}: переходы {tot[0]}, заказы {tot[1]}, "
            f"выкупы {tot[2]}, выкуплено на {tot[3]} ₽")


if __name__ == "__main__":
    print(run(sys.argv[1] if len(sys.argv) > 1 else START))
