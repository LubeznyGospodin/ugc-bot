#!/usr/bin/env python3
"""Брендовый дашборд PapKids ПРЯМО в листе «Дашборд» Google-таблицы.
Логотип, крем/шалфей/золото, Nunito+Caveat, спарклайн-бары, живые формулы (RU-локаль: ';' и '\\').
"""
import base64
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
                return ln.split("=", 1)[1].strip()
    return d

WEBHOOK = _env("SHEETS_WEBHOOK_URL")
SECRET = _env("SHEETS_WEBHOOK_SECRET")
SID = _env("PAPKIDS_SHEET_ID")
SHEET = "Дашборд"
LOGO = "/Users/nastasyapopova/Downloads/PapKids_creators/logo_papkids_512.png"  # >1,05 млн пикселей Sheets не берёт
ART = "https://claude.ai/code/artifact/ec6a9c23-ffe9-43ef-a3ea-3598459b3c83"

CREAM, CARD, SAGE, SAGE_D = "#F0ECE7", "#FDFDFD", "#6C7B66", "#5A6955"
INK, SOFT, TAUPE, GOLD, GRAY = "#424023", "#797650", "#AB917A", "#D9A84E", "#D9D2C6"

def call(payload, timeout=180):
    payload = {"secret": SECRET, "sheet_id": SID, "sheet_name": SHEET, **payload}
    data = json.dumps(payload).encode()
    last = None
    for a in range(4):
        try:
            req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as e:
            last = e; time.sleep(3 * (a + 1))
    raise last

DF = 'IF($D$6="";DATE(2000;1;1);$D$6)'
DT = 'IF($F$6="";DATE(2100;1;1);$F$6)+1'
PER = f"'Посты'!$A$2:$A;\">=\"&{DF};'Посты'!$A$2:$A;\"<\"&{DT}"

def bar(val_or_expr, color, maxx):
    return f'=IFERROR(SPARKLINE({val_or_expr};{{"charttype"\\"bar";"max"\\{maxx};"color1"\\"{color}"}});"")'

def funnel_wait(width_filled, width_wait, dcell):
    return (f'=IF(N({dcell})=0;SPARKLINE({width_wait};{{"charttype"\\"bar";"max"\\100;"color1"\\"{GRAY}"}});'
            f'SPARKLINE({width_filled};{{"charttype"\\"bar";"max"\\100;"color1"\\"{SAGE}"}}))')

R = [[""] * 11 for _ in range(44)]
def put(r, c, v): R[r - 1][c - 1] = v

# — шапка —
put(2, 3, "ДАШБОРД КАМПАНИИ")
put(3, 3, "считаем каждый просмотр с заботой 🌿")
put(2, 7, "● обновляется каждые 6 часов")
put(3, 7, "5 креаторов · 20 аккаунтов · 4 платформы")
# — период —
put(6, 2, "Период:  с"); put(6, 5, "по")
# — воронка —
put(8, 2, "ВОРОНКА КАМПАНИИ · от поста до выручки")
stages = [
    (9,  "ПУБЛИКАЦИИ", f"=COUNTIFS({PER})", "", "", bar(100, SAGE, 100)),
    (10, "ОХВАТЫ", f"=SUMIFS('Посты'!$F$2:$F;{PER})", "охват на пост", "=IFERROR(ROUND(D10/D9);\"\")", bar(87, SAGE, 100)),
    (11, "ПЕРЕХОДЫ", "=SUM('WB · ROI'!D2:D)", "CTR", '=IFERROR(D11/D10;"")', funnel_wait(74, 38, "$D$11")),
    (12, "ЗАКАЗЫ", "=SUM('WB · ROI'!E2:E)", "из перехода", '=IFERROR(D12/D11;"")', funnel_wait(61, 38, "$D$12")),
    (13, "ВЫКУПЫ", "=SUM('WB · ROI'!F2:F)", "выкупаемость", '=IFERROR(D13/D12;"")', funnel_wait(48, 38, "$D$13")),
    (14, "ВЫРУЧКА", "=SUM('WB · ROI'!G2:G)", "средний чек", '=IFERROR(D14/D13;"")', funnel_wait(35, 38, "$D$14")),
]
for r, nm, val, cl, cv, sp in stages:
    put(r, 2, nm); put(r, 3, sp); put(r, 4, val); put(r, 5, cl); put(r, 6, cv)
WBP = "'WB отчёты'!$A$2:$A"    # периоды загруженных отчётов WB (пусто = отчётов ещё нет)
put(15, 2, f'=IF(COUNTA({WBP})=0;'
           f'"☁️ Переходы · Заказы · Выкупы · Выручка появятся из кабинета WB — связка через #WW уже в подписях";'
           f'"📊 Отчёт WB за "&TEXTJOIN(", ";1;UNIQUE(FILTER({WBP};{WBP}<>"""")))'
           f'&" · в корзину "&SUM(\'WB · ROI\'!H2:H)&" · выкупы и выручка — из отчёта о продажах")')
# — платформы —
put(17, 2, "ОХВАТ ПО ПЛАТФОРМАМ")
plats = [("Instagram", SAGE), ("YouTube", GOLD), ("TikTok", TAUPE), ("VK", "#8A9BB4")]
for i, (p, col) in enumerate(plats):
    r = 18 + i
    put(r, 2, p)
    put(r, 4, f"=SUMIFS('Посты'!$F$2:$F;'Посты'!$B$2:$B;\"{p}\";{PER})")
    put(r, 3, bar(f"$D${r}", col, "MAX($D$18:$D$21)+1"))
# — креаторы —
put(23, 2, "ОХВАТ ПО КРЕАТОРАМ")
cres = ["Татьяна Тищенко", "Ксения Кузнецова", "Анастасия Китаева", "Рождественская Лилия", "Дарья Трофимова"]
for i, c in enumerate(cres):
    r = 24 + i
    put(r, 2, c)
    put(r, 4, f"=SUMIFS('Посты'!$F$2:$F;'Посты'!$D$2:$D;\"{c}\";{PER})")
    put(r, 3, bar(f"$D${r}", GOLD if i == 0 else SAGE, "MAX($D$24:$D$28)+1"))
# — динамика —
put(30, 2, "ДИНАМИКА ПО ДНЯМ · с 21 июля")
put(31, 2, "просмотры за день →")
put(31, 3, '=SPARKLINE($K$2:$K$21;{"charttype"\\"column";"color"\\"' + SAGE + '"})')
# — товары —
put(33, 2, "ОХВАТ ПО ТОВАРАМ · #WW")
for i in range(4):
    r = 34 + i
    put(r, 4, f"=IFERROR(LARGE('WB · ROI'!$C$2:$C;{i+1});\"\")")
    put(r, 2, f"=IFERROR(INDEX('WB · ROI'!$B$2:$B;MATCH($D${r};'WB · ROI'!$C$2:$C;0));\"\")")
    put(r, 3, bar(f"$D${r}", GOLD if i == 0 else TAUPE, "MAX($D$34:$D$37)+1"))
# — звезда + ссылка —
put(39, 2, "🏆 РОЛИК-ЗВЕЗДА")
put(39, 4, "=MAX('Посты'!$F$2:$F)")
put(39, 5, "=INDEX('Посты'!$D$2:$D;MATCH($D$39;'Посты'!$F$2:$F;0))")
put(39, 3, "=HYPERLINK(INDEX('Посты'!$E$2:$E;MATCH($D$39;'Посты'!$F$2:$F;0));\"смотреть ролик →\")")
put(41, 2, f'=HYPERLINK("{ART}";"✨  ОТКРЫТЬ ПОЛНЫЙ ВЕБ-ДАШБОРД  ✨")')
# — helpers J,K (скрыты) —
for i in range(20):
    r = 2 + i
    put(r, 10, "=DATE(2026;7;21)" if i == 0 else f"=J{r-1}+1")
    put(r, 11, f"=SUMIFS('Посты'!$F$2:$F;'Посты'!$A$2:$A;\">=\"&J{r};'Посты'!$A$2:$A;\"<\"&J{r}+1)")

print("1. пишу значения/формулы…")
print(call({"action": "grid_write", "clear": True, "row": 1, "rows": R}))

print("2. числовые форматы…")
for rng, f in [("A1:K44", "General"), ("D6", "dd.MM.yyyy"), ("F6", "dd.MM.yyyy"),
               ("D9:D13", "#,##0"), ("D14", "#,##0 ₽"), ("F10", "#,##0"),
               ("F11", "0.00%"), ("F12:F13", "0.0%"), ("F14", "#,##0 ₽"),
               ("D18:D21", "#,##0"), ("D24:D28", "#,##0"), ("D34:D37", "#,##0"), ("D39", "#,##0")]:
    call({"action": "set_format", "range": rng, "format": f})

print("3. стили…")
ops = [
    {"op": "gridlines", "hide": True}, {"op": "tab", "color": SAGE},
    {"op": "bg", "range": "A1:H44", "color": CREAM},
    # колонки
    {"op": "colw", "col": 1, "w": 22}, {"op": "colw", "col": 2, "w": 185}, {"op": "colw", "col": 3, "w": 330},
    {"op": "colw", "col": 4, "w": 115}, {"op": "colw", "col": 5, "w": 130}, {"op": "colw", "col": 6, "w": 110},
    {"op": "colw", "col": 7, "w": 240}, {"op": "colw", "col": 8, "w": 22},
    {"op": "hidecol", "col": 9, "n": 3},
    # шапка
    {"op": "rowh", "row": 1, "h": 16}, {"op": "rowh", "row": 2, "h": 46}, {"op": "rowh", "row": 3, "h": 34},
    {"op": "merge", "range": "C2:F2"}, {"op": "merge", "range": "C3:F3"},
    {"op": "font", "range": "C2", "family": "Nunito", "size": 24, "bold": True, "color": INK},
    {"op": "font", "range": "C3", "family": "Caveat", "size": 16, "color": SAGE},
    {"op": "font", "range": "G2:G3", "family": "Nunito", "size": 9, "bold": True, "color": SOFT},
    {"op": "align", "range": "G2:G3", "h": "right"},
    # период
    {"op": "rowh", "row": 6, "h": 30},
    {"op": "font", "range": "B6", "family": "Nunito", "size": 11, "bold": True, "color": INK},
    {"op": "font", "range": "E6", "family": "Nunito", "size": 11, "bold": True, "color": INK},
    {"op": "align", "range": "B6", "h": "right"}, {"op": "align", "range": "E6", "h": "center"},
    {"op": "bg", "range": "D6", "color": CARD}, {"op": "bg", "range": "F6", "color": CARD},
    {"op": "align", "range": "D6", "h": "center"}, {"op": "align", "range": "F6", "h": "center"},
]
# секции-плашки
for r in (8, 17, 23, 30, 33):
    ops += [{"op": "merge", "range": f"B{r}:G{r}"}, {"op": "rowh", "row": r, "h": 30},
            {"op": "bg", "range": f"B{r}:G{r}", "color": SAGE},
            {"op": "font", "range": f"B{r}", "family": "Nunito", "size": 11, "bold": True, "color": "#FDFDFD"},
            {"op": "align", "range": f"B{r}:G{r}", "v": "middle"}]
# карточные зоны
for rng in ("B9:G14", "B18:G21", "B24:G28", "B31:G31", "B34:G37", "B39:G39"):
    ops.append({"op": "bg", "range": rng, "color": CARD})
# воронка
for r in range(9, 15):
    ops += [{"op": "rowh", "row": r, "h": 34},
            {"op": "font", "range": f"B{r}", "family": "Nunito", "size": 11, "bold": True, "color": INK},
            {"op": "font", "range": f"D{r}", "family": "Nunito", "size": 13, "bold": True, "color": INK},
            {"op": "font", "range": f"E{r}", "family": "Nunito", "size": 9, "color": SOFT},
            {"op": "font", "range": f"F{r}", "family": "Nunito", "size": 11, "bold": True, "color": INK},
            {"op": "align", "range": f"E{r}", "h": "right"}, {"op": "align", "range": f"B{r}:G{r}", "v": "middle"}]
ops += [{"op": "font", "range": "B15", "family": "Caveat", "size": 13, "color": TAUPE}, {"op": "rowh", "row": 15, "h": 26}]
# бары-строки
for rr in list(range(18, 22)) + list(range(24, 29)) + list(range(34, 38)):
    ops += [{"op": "rowh", "row": rr, "h": 30},
            {"op": "font", "range": f"B{rr}", "family": "Nunito", "size": 10.5, "bold": True, "color": INK},
            {"op": "font", "range": f"D{rr}", "family": "Nunito", "size": 12, "bold": True, "color": INK},
            {"op": "align", "range": f"B{rr}:G{rr}", "v": "middle"}]
# динамика
ops += [{"op": "merge", "range": "C31:G31"}, {"op": "rowh", "row": 31, "h": 72},
        {"op": "font", "range": "B31", "family": "Caveat", "size": 14, "color": SOFT},
        {"op": "align", "range": "B31", "v": "middle"}]
# звезда
ops += [{"op": "rowh", "row": 39, "h": 40},
        {"op": "bg", "range": "B39:G39", "color": SAGE_D},
        {"op": "font", "range": "B39", "family": "Nunito", "size": 12, "bold": True, "color": "#FDFDFD"},
        {"op": "font", "range": "C39", "family": "Nunito", "size": 10, "bold": True, "color": "#FDFDFD"},
        {"op": "font", "range": "D39", "family": "Nunito", "size": 16, "bold": True, "color": GOLD},
        {"op": "font", "range": "E39:F39", "family": "Caveat", "size": 14, "color": "#EDE9DC"},
        {"op": "merge", "range": "E39:F39"}, {"op": "align", "range": "B39:G39", "v": "middle"}]
# ссылка на веб-версию
ops += [{"op": "merge", "range": "B41:G41"}, {"op": "rowh", "row": 41, "h": 36},
        {"op": "bg", "range": "B41:G41", "color": GOLD},
        {"op": "font", "range": "B41", "family": "Nunito", "size": 12, "bold": True, "color": INK},
        {"op": "align", "range": "B41:G41", "h": "center", "v": "middle"},
        {"op": "first"}]
print(call({"action": "style_batch", "ops": ops}))

print("4. логотип…")
b64 = base64.b64encode(open(LOGO, "rb").read()).decode()
print(call({"action": "style_batch", "ops": [{"op": "img", "b64": b64, "col": 2, "row": 2, "w": 84, "h": 84, "dx": 40}]}))
print("ГОТОВО")
