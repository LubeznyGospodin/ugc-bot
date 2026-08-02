#!/usr/bin/env python3
"""Пересобрать брендовый HTML-дашборд PapKids из живых данных таблицы и залить
в публичный Selectel (бакет packman). Гоняется после каждого сбора.

Публичный URL: https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru/papkids/dashboard.html
"""
import base64
import datetime
import json
import math
import os
import time
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

def _env(k, d=""):
    v = os.getenv(k)
    if v:
        return v.strip()
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):                      # на Railway .env нет — всё из окружения
        for ln in open(p, encoding="utf-8"):
            if ln.startswith(k + "="):
                return ln.split("=", 1)[1].strip()
    return d

WEBHOOK = _env("SHEETS_WEBHOOK_URL")
SECRET = _env("SHEETS_WEBHOOK_SECRET")
SID = _env("PAPKIDS_SHEET_ID")
PUB = "https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru/papkids/dashboard.html"
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def dump(name):
    p = {"secret": SECRET, "sheet_id": SID, "sheet_name": name, "action": "grid_dump"}
    data = json.dumps(p).encode()
    last = None
    for a in range(4):
        try:
            req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120)).get("values", [])
        except Exception as e:
            last = e
            time.sleep(3 * (a + 1))
    raise last


def build_data():
    rows = [r for r in dump("Посты")[1:] if r and r[0]]
    plat, cre, sku, daily = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    total, top = 0, []
    for x in rows:
        ov = x[5] if isinstance(x[5], (int, float)) else 0
        total += ov
        plat[x[1]] += ov
        cre[x[3]] += ov
        if x[6]:
            sku[f"{x[6]}|{x[7]}"] += ov
        daily[str(x[0])[:10]] += ov
        top.append((ov, x[3], x[1], x[4], x[7]))
    top.sort(reverse=True)
    wbv = dump("WB · ROI")[1:]
    def col(i):
        return sum(r[i] for r in wbv if len(r) > i and isinstance(r[i], (int, float)))
    try:                                   # период данных WB — подпись под воронкой
        per = sorted({str(r[8]) for r in dump("WB запросы")[1:] if len(r) > 8 and r[8]})
    except Exception:
        per = []
    return {"posts": len(rows), "total": total, "plat": dict(plat), "cre": dict(cre),
            "sku": dict(sorted(sku.items(), key=lambda kv: -kv[1])), "daily": dict(sorted(daily.items())),
            "top": [{"v": t[0], "c": t[1], "p": t[2], "u": t[3], "t": t[4]} for t in top[:5]],
            "wb": {"per": col(3), "cart": col(4), "ord": col(5), "rev": col(6),
                   "period": ", ".join(per)}}


def render(data):
    tpl = open(os.path.join(HERE, "papkids_dash", "template.html"), encoding="utf-8").read()
    fonts = open(os.path.join(HERE, "papkids_dash", "fonts.css"), encoding="utf-8").read()
    logo = base64.b64encode(open(os.path.join(HERE, "papkids_dash", "logo.png"), "rb").read()).decode()
    rays = []
    for i in range(14):
        a = math.radians(200 + i * 10)
        rays.append(f'<line x1="{50+30*math.cos(a):.1f}" y1="{50+30*math.sin(a):.1f}" '
                    f'x2="{50+46*math.cos(a):.1f}" y2="{50+46*math.sin(a):.1f}" '
                    f'stroke="#FDFDFD" stroke-width="5" stroke-linecap="round"/>')
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    stamp = f"обновлено: {now.day} {MONTHS[now.month-1]} {now:%H:%M} мск"
    out = (tpl.replace("/*__FONTS__*/", fonts, 1)
              .replace("__LOGO__", logo, 1)
              .replace("__DATA__", json.dumps(data, ensure_ascii=False), 1)
              .replace("__RAYS__", "".join(rays), 1)
              .replace("__STAMP__", stamp, 1))
    return '<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n' + out


def upload(html, key="papkids/dashboard.html"):
    import boto3
    s3 = boto3.client("s3", endpoint_url="https://s3.ru-7.storage.selcloud.ru",
                      aws_access_key_id=_env("SELECTEL_S3_KEY"),
                      aws_secret_access_key=_env("SELECTEL_S3_SECRET"),
                      region_name="ru-7")
    s3.put_object(Bucket="packman", Key=key, Body=html.encode("utf-8"),
                  ContentType="text/html; charset=utf-8", CacheControl="no-cache, max-age=300")


if __name__ == "__main__":
    d = build_data()
    html = render(d)
    upload(html)
    print(f"опубликовано: {d['posts']} постов, Σ {d['total']} → {PUB}")
