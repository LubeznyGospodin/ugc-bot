#!/usr/bin/env python3
"""Импорт отчёта WB по контент-заводу (подменные артикулы = наши #WW) в таблицу PapKids.

Что делает:
  1. «WB отчёты» — сырые строки отчёта с меткой периода (повторный импорт того же
     периода перезатирает свои строки, а не плодит дубли).
  2. «WB · ROI» — пересобирает список артикулов, цифры тянет формулами SUMIF из
     «WB отчётов» (следующий отчёт подхватится сам), ручные «Выкупы»/«Выручка» сохраняет.

Запуск:  python papkids_wb_report.py [путь_к_отчёту.xlsx]
Без аргумента берёт самый свежий «Отчет_по_контен_заводу*.xlsx» с рабочего стола.
"""
import datetime
import glob
import os
import re
import sys

import openpyxl

import papkids_push as pp
from papkids_push import TOVAR, _webhook

REPORTS = "WB отчёты"
ROI = "WB · ROI"
# колонки отчёта WB → наши поля (ищем по названию шапки, порядок у WB может меняться)
COLS = {"ww": "Подменный артикул", "name": "Наименование", "clicks": "Перешли в карточку",
        "cart": "Положили в корзину", "orders": "Заказали товаров", "fav": "Добавили в избранное",
        "nm": "Артикул WB"}     # nmID — по нему тянем воронку карточки из WB API
PERIOD_RE = re.compile(r"(\d{2})_(\d{2})_(\d{4})[—\-](\d{2})_(\d{2})_(\d{4})")


def period_of(path):
    """Период из имени файла → «16.07–30.07.2026» (иначе — дата импорта)."""
    m = PERIOD_RE.search(os.path.basename(path))
    if not m:
        return datetime.date.today().strftime("отчёт от %d.%m.%Y")
    d1, m1, y1, d2, m2, y2 = m.groups()
    return f"{d1}.{m1}–{d2}.{m2}.{y2}"


def read_report(path):
    """xlsx → [{ww, name, clicks, cart, orders, fav}] (строки без #WW пропускаем)."""
    ws = openpyxl.load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    head = [str(c or "").strip() for c in rows[0]]
    idx = {k: head.index(v) for k, v in COLS.items() if v in head}
    missing = set(COLS) - set(idx)
    if missing:
        raise SystemExit(f"в отчёте нет колонок: {[COLS[m] for m in missing]}")
    out = []
    for r in rows[1:]:
        ww = str(r[idx["ww"]] or "").strip().upper()
        if not ww.startswith("WW"):
            continue
        num = lambda k: r[idx[k]] if isinstance(r[idx[k]], (int, float)) else 0   # noqa: E731
        out.append({"ww": ww, "name": str(r[idx["name"]] or "").strip(),
                    "clicks": num("clicks"), "cart": num("cart"),
                    "orders": num("orders"), "fav": num("fav"), "nm": num("nm")})
    return out


def push_reports(rows, period):
    """Лист «WB отчёты»: свои строки за этот период заменяем, чужие периоды не трогаем."""
    head = ["Период", "Артикул", "Товар", "Переходы", "В корзину", "Заказы", "Избранное",
            "Загружено", "Артикул WB"]
    try:
        old = _webhook({"action": "grid_dump", "sheet_name": REPORTS}).get("values", [])[1:]
    except Exception:
        old = []
    keep = [list(r[:9]) + [""] * max(0, 9 - len(r)) for r in old if r and r[0] and str(r[0]) != period]
    for r in keep:                       # дата загрузки после дампа приходит ISO-строкой
        r[7] = pp._iso_to_serial(r[7])
    fresh = [[period, r["ww"], TOVAR.get(r["ww"]) or r["name"], r["clicks"], r["cart"],
              r["orders"], r["fav"], pp.NOW, r["nm"]] for r in rows]
    _webhook({"action": "grid_write", "sheet_name": REPORTS, "clear": True, "row": 1,
              "rows": [head] + keep + fresh})
    return len(keep), len(fresh)


def push_roi(rows):
    """«WB · ROI»: артикулы = наш каталог + все из отчётов; цифры — формулами из «WB отчётов».
    Ручные «Выкупы»/«Выручка» переносим из текущего листа."""
    try:
        cur = _webhook({"action": "grid_dump", "sheet_name": ROI}).get("values", [])[1:]
    except Exception:
        cur = []
    cur = [r for r in cur if r and str(r[0]).startswith("WW")]
    names = dict(TOVAR)
    for r in cur:                                  # артикулы, уже заведённые в листе
        names.setdefault(str(r[0]), str(r[1]) if len(r) > 1 else "")
    for r in rows:
        names.setdefault(r["ww"], r["name"])
    clicks = {r["ww"]: r["clicks"] for r in rows}   # без отчёта — порядок по тому, что уже в листе
    for r in cur:
        clicks.setdefault(str(r[0]), r[3] if len(r) > 3 and isinstance(r[3], (int, float)) else 0)
    order = sorted(names, key=lambda w: (-clicks.get(w, 0), w))
    head = ["Артикул", "Товар", "Σ охват", "Переходы", "В корзину", "Заказы",
            "Выручка по заказам, ₽ (оценка)", "Избранное"]
    # Считаем ЗАКАЗЫ (заказ ≠ выкуп: выкуп — когда товар забрали на ПВЗ).
    # Выручки по заказам WB в разрезе запроса не отдаёт → оцениваем: заказы × средний чек карточки.
    out = []
    for i, ww in enumerate(order):
        r = i + 2
        # переходы/корзина/заказы — из «WB запросов» (поисковый отчёт API: считает и переходы
        # в соседние карточки по тому же коду, кабинет их не показывает)
        q = lambda col: f"=IFERROR(VLOOKUP($A{r};'WB запросы'!$A:$F;{col};0);0)"      # noqa: E731
        out.append([ww, names[ww], f"=SUMIF('Посты'!G:G;$A{r};'Посты'!F:F)",
                    q(4), q(5), q(6),
                    f"=IFERROR(ROUND($F{r}*VLOOKUP($A{r};'WB карточки'!$C:$K;9;0));0)",
                    f"=SUMIF('{REPORTS}'!$B:$B;$A{r};'{REPORTS}'!G:G)"])
    _webhook({"action": "grid_write", "sheet_name": ROI, "clear": True, "row": 1, "rows": [head] + out})
    return len(out)


def run(path):
    period = period_of(path)
    rows = read_report(path)
    kept, fresh = push_reports(rows, period)
    n = push_roi(rows)
    tot = {k: sum(r[k] for r in rows) for k in ("clicks", "cart", "orders", "fav")}
    return (f"отчёт «{period}»: {fresh} артикулов (прошлых строк {kept}), в «WB · ROI» {n} позиций; "
            f"переходы {tot['clicks']}, корзина {tot['cart']}, заказы {tot['orders']}, избранное {tot['fav']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] != "--check":
        src = sys.argv[1]
    else:
        found = sorted(glob.glob(os.path.expanduser("~/Desktop/Отчет_по_контен_заводу*.xlsx")),
                       key=os.path.getmtime)
        if not found:
            raise SystemExit("не нашёл отчёт на рабочем столе — передай путь аргументом")
        src = found[-1]
    if "--check" in sys.argv:      # самопроверка разбора без записи в таблицу
        assert period_of("Отчет_по_контен_заводу_16_07_30_07_16_07_2026—30_07_2026.xlsx") == "16.07–30.07.2026"
        assert period_of("без_дат.xlsx").startswith("отчёт от")
        rs = read_report(src)
        assert rs and all(r["ww"].startswith("WW") for r in rs), "не разобрались артикулы"
        print(f"ok: {len(rs)} строк, Σ переходы {sum(r['clicks'] for r in rs)}")
        sys.exit(0)
    print(run(src))
