#!/usr/bin/env python3
"""Перестроить листы учёта PapKids под новый порядок «Постов» + воронку-дашборд.
Пишет через вебхук бота (grid_write / grid_dump), формулы с ';' (RU-локаль).
Новые колонки «Посты»: A Дата, B Платформа, C Аккаунт, D Креатор, F Охваты, G Артикул, K ERR.
"""
import json
import os
import time
import urllib.request


def _env(k, d=""):
    v = os.getenv(k)
    if v:
        return v.strip()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            if ln.startswith(k + "="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return d


WEBHOOK = _env("SHEETS_WEBHOOK_URL")
SECRET = _env("SHEETS_WEBHOOK_SECRET")
SID = _env("PAPKIDS_SHEET_ID", "1qZHSUgAMkuGSJQWKNm4n0osBGYdrOMoGGuk-0jcuhL4")


def call(payload):
    payload = {"secret": SECRET, "sheet_id": SID, **payload}
    data = json.dumps(payload).encode()
    last = None
    for a in range(4):
        try:
            req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:
            last = e
            time.sleep(3 * (a + 1))
    raise last


def dump(name):
    r = call({"action": "grid_dump", "sheet_name": name})
    return r.get("values", [])


def write(name, rows, clear=True, row=1):
    return call({"action": "grid_write", "sheet_name": name, "clear": clear, "row": row, "rows": rows})


DF = 'IF($B$3="";DATE(2000;1;1);$B$3)'   # период с (пусто = вся кампания)
DT = 'IF($D$3="";DATE(2100;1;1);$D$3)'   # период по
DR = f'\'Посты\'!A2:A;">="&{DF};\'Посты\'!A2:A;"<="&{DT}'   # фильтр по дате публикации


def reach_in_period(extra=""):
    return f'=SUMIFS(\'Посты\'!F2:F;{extra}{DR})'


# ─────────── 1. ДАШБОРД — воронка с сегментацией по датам ───────────
def build_dashboard():
    R = []
    R.append(["PapKids — Воронка кампании", "", ""])
    R.append(["Сегмент по датам (пусто = вся кампания):", "", ""])
    R.append(["Период с", "", "Период по"])            # B3 = дата с, D3 = дата по
    R.append(["", "", ""])
    R.append(["Этап воронки", "Значение", "Конверсия"])
    R.append([f'=COUNTIFS({DR})', "", "Публикаций"])   # B6 значение → перенесём: пусть A=этап,B=значение,C=подпись
    # перестроим корректно: A этап, B значение, C конверсия
    R = R[:5]
    R.append(["Публикации", f'=COUNTIFS({DR})', ""])
    R.append(["Охваты", reach_in_period(), '=IFERROR(B7/B6;0)'])          # ср. охват/пост
    R.append(["Переходы на WB", "=SUM('WB · ROI'!D2:D)", '=IFERROR(B8/B7;0)'])   # CTR
    R.append(["Заказы", "=SUM('WB · ROI'!E2:E)", '=IFERROR(B9/B8;0)'])           # заказ/переход
    R.append(["Выкупы", "=SUM('WB · ROI'!F2:F)", '=IFERROR(B10/B9;0)'])          # выкуп/заказ
    R.append(["Выручка, ₽", "=SUM('WB · ROI'!G2:G)", '=IFERROR(B11/B10;0)'])     # средний чек
    R.append(["", "", ""])
    R.append(["Охват по платформам (в периоде)", "", ""])
    for p in ["Instagram", "YouTube", "TikTok", "VK"]:
        R.append([p, reach_in_period(f'\'Посты\'!B2:B;"{p}";'), ""])
    R.append(["", "", ""])
    R.append(["Охват по креаторам (в периоде)", "", ""])
    for c in ["Анастасия Китаева", "Ксения Кузнецова", "Дарья Трофимова", "Рождественская Лилия", "Татьяна Тищенко"]:
        R.append([c, reach_in_period(f'\'Посты\'!D2:D;"{c}";'), ""])
    write("Дашборд", R)


# ─────────── 2. WB · ROI — воронка продаж по SKU ───────────
def build_wb():
    """Структуру листа держит papkids_wb_report (там же импорт отчётов WB).
    Ручные «Выкупы»/«Выручка» и загруженные отчёты не теряются."""
    from papkids_wb_report import push_roi
    print("WB · ROI:", push_roi([]), "позиций")


# ─────────── 3. КРЕАТОРЫ — свод (без «Оригиналов») ───────────
CREATORS = ["Ксения Кузнецова", "Дарья Трофимова", "Рождественская Лилия", "Татьяна Тищенко", "Анастасия Китаева"]


def build_creators():
    R = [["Креатор", "Аккаунтов", "Постов", "Σ охват", "Средний ERR", "Дней без поста", "Оплата"]]
    for i, c in enumerate(CREATORS):
        r = i + 2
        R.append([c,
                  f'=COUNTIF(\'Аккаунты\'!A:A;A{r})',
                  f'=COUNTIF(\'Посты\'!D:D;A{r})',
                  f'=SUMIF(\'Посты\'!D:D;A{r};\'Посты\'!F:F)',
                  f'=IFERROR(AVERAGEIFS(\'Посты\'!K:K;\'Посты\'!D:D;A{r});"")',
                  f'=IFERROR(MAX(0;TODAY()-INT(MAXIFS(\'Посты\'!A:A;\'Посты\'!D:D;A{r})));"")',
                  ""])
    write("Креаторы", R)


# ─────────── 4. КОНТРОЛЬ — факты по аккаунтам (план убран) ───────────
# Аккаунт в колонке A обязан ПОБУКВЕННО совпадать с «Посты»!C, иначе COUNTIF даёт 0
# (так и было: TikTok писался с «@», VK — ссылкой → факты по ним показывали нули).
def accounts():
    """[(аккаунт, креатор, платформа, ссылка на канал)] — источник правды papkids_push."""
    import papkids_push as pp
    out = []
    for prof, creator in pp.PROFILES.items():
        for plat in pp.PLATS:
            h = pp.HANDLE[f"{prof}|{plat}"]
            url = {"instagram": f"https://www.instagram.com/{h}/",
                   "tiktok": f"https://www.tiktok.com/@{h}",
                   "youtube": f"https://www.youtube.com/{h}"}[plat]
            out.append((h, creator, pp.PLAT_LABEL[plat], url))
    for sn, creator in pp.VK_GROUPS.items():
        out.append((sn, creator, "VK", f"https://vk.ru/{sn}"))
    return out


def build_control():
    old = dump("Контроль")
    manual = {(str(r[1]).strip(), str(r[2]).strip()): list(r[7:11]) + [""] * 4   # H:K — ручные
              for r in old[1:] if r and len(r) > 2}
    R = [["Аккаунт", "Креатор", "Платформа", "Всего роликов", "Роликов за 7 дней",
          "Роликов за вчера", "Последний пост", "Источник", "Правила OK", "Проблема / бан",
          "Коммент Арины"]]
    P, A, B = "'Посты'!$A:$A", "'Посты'!$C:$C", "'Посты'!$B:$B"
    for i, (acc, creator, plat, _url) in enumerate(accounts()):
        r = i + 2
        who = f'{A};$A{r};{B};$C{r}'      # аккаунт + платформа: в IG и TikTok хэндлы совпадают
        R.append([acc, creator, plat,
                  f'=COUNTIFS({who})',
                  f'=COUNTIFS({who};{P};">="&TODAY()-7)',
                  f'=COUNTIFS({who};{P};">="&TODAY()-1;{P};"<"&TODAY())',
                  f'=IFERROR(IF(MAXIFS({P};{who})=0;"";MAXIFS({P};{who}));"")',
                  *(manual.get((creator, plat), ["", "", "", ""])[:4])])
    write("Контроль", R, clear=False)
    call({"action": "set_links", "sheet_name": "Контроль", "row": 2, "col": 1,
          "links": [[a, u] for a, _c, _p, u in accounts()]})
    fmt("Контроль", "G2:G30", "dd.MM.yyyy HH:mm")
    return len(R) - 1


def fmt(name, rng, f):
    call({"action": "set_format", "sheet_name": name, "range": rng, "format": f})


def validation(name, rng, values):
    call({"action": "set_validation", "sheet_name": name, "range": rng, "values": values})


def dropdowns():
    # «Посты»: старые выпадашки сняты (колонки переехали) — ставим на новые места
    validation("Посты", "A1:O1000", [])                                  # сначала очистка
    validation("Посты", "B2:B1000", ["Instagram", "YouTube", "VK", "TikTok"])
    validation("Посты", "N2:N1000", ["Ручной", "Авто"])
    # «Контроль»: H Источник, I Правила OK
    validation("Контроль", "H2:H100", ["Ручной", "Авто"])
    validation("Контроль", "I2:I100", ["OK", "Нет"])


def formats():
    fmt("Креаторы", "E2:E6", "0.0%")                    # Средний ERR
    fmt("Дашборд", "B3", "dd.MM.yyyy")                  # период с
    fmt("Дашборд", "D3", "dd.MM.yyyy")                  # период по
    fmt("Дашборд", "C8:C11", "0.00%")                   # конверсии воронки
    fmt("Дашборд", "B11", "#,##0 ₽")                    # выручка
    fmt("WB · ROI", "G2:G19", "#,##0 ₽")                # выручка по SKU


if __name__ == "__main__":
    build_dashboard(); print("Дашборд ✓")
    build_wb(); print("WB · ROI ✓")
    build_creators(); print("Креаторы ✓")
    print("Контроль ✓", build_control(), "аккаунтов")
    formats(); print("форматы ✓")
    dropdowns(); print("выпадашки ✓")
