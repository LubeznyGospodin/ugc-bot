#!/usr/bin/env python3
"""Синхронизировать лист «Ролики (бот)» с базой бота: сколько роликов креатор реально
загрузил в проект PapKids и когда был последний.

Правит ТОЛЬКО колонки «Имя»/«Роликов»/«Обновлено» (+ дописывает пустой «ID», если нашли
креатора по имени). Ручные колонки (кого постит, аккаунт, оплата, реквизиты…) переносятся
как есть. Колонки ищем по названиям в шапке — можно двигать их в таблице.

Строки, оставшиеся от старой схемы бота (ID лежал в «Роликов»), удаляются: цифры всё равно
берём из базы.

Запуск: python papkids_rolls_sync.py [--dry]
"""
import asyncio
import datetime
import os
import sys

import asyncpg

import papkids_push as pp

SHEET = "Ролики (бот)"
COL = {"name": "Имя", "count": "Роликов", "ts": "Обновлено", "id": "ID", "num": "№"}


def db_url():
    u = pp._env("DATABASE_URL")
    if not u:
        raise SystemExit("нет DATABASE_URL в .env (нужен публичный URL Postgres из Railway)")
    return u


async def db_counts():
    """→ ({tg_id: (роликов, последний)}, {имя: tg_id} по участникам проекта)."""
    c = await asyncpg.connect(db_url())
    try:
        works = await c.fetch("""select w.tg_id, count(*) n, max(w.created_at) last
                                 from creator_works w where w.project='papkids' group by w.tg_id""")
        members = await c.fetch("""select m.tg_id, c.full_name from project_members m
                                   left join creators c on c.tg_id=m.tg_id where m.project='papkids'""")
    finally:
        await c.close()
    return ({str(r["tg_id"]): (r["n"], r["last"]) for r in works},
            {(r["full_name"] or "").strip().lower(): str(r["tg_id"]) for r in members if r["full_name"]})


def serial(dt):
    """datetime (UTC из базы) → сериал Google Sheets в MSK."""
    return (dt.replace(tzinfo=datetime.timezone.utc).timestamp() + 3 * 3600) / 86400.0 + 25569


def sync(dry=False):
    counts, by_name = asyncio.run(db_counts())
    grid = pp._webhook({"action": "grid_dump", "sheet_name": SHEET})["values"]
    head = next((i for i, r in enumerate(grid) if COL["name"] in [str(x).strip() for x in r]), None)
    if head is None:
        raise SystemExit(f"в листе «{SHEET}» не нашёл шапку с колонкой «{COL['name']}»")
    hdr = [str(x).strip() for x in grid[head]]
    ix = {k: hdr.index(v) for k, v in COL.items() if v in hdr}
    for k in ("name", "count", "ts", "id"):
        if k not in ix:
            raise SystemExit(f"в шапке нет колонки «{COL[k]}»")
    width = len(hdr)

    rows, dropped, touched, filled = [], 0, [], []
    for r in grid[head + 1:]:
        r = [x for x in r[:width]] + [""] * max(0, width - len(r))
        if not any(str(x).strip() for x in r):
            continue
        rid = str(r[ix["id"]]).strip()
        # хвост старой схемы бота: ID оказался в «Роликов», своих данных в строке нет
        if not rid and str(r[ix["count"]]).strip().isdigit() and int(r[ix["count"]]) > 10 ** 6:
            dropped += 1
            continue
        nm = str(r[ix["name"]]).strip()
        if not rid and nm:
            rid = by_name.get(nm.lower(), "")
            if rid:
                r[ix["id"]] = rid
                filled.append(nm)
        if rid in counts:
            n, last = counts[rid]
            if r[ix["count"]] != n:
                touched.append(f"{nm}: {r[ix['count']] or 0}→{n}")
            r[ix["count"]], r[ix["ts"]] = n, serial(last)
        else:
            r[ix["ts"]] = pp._iso_to_serial(r[ix["ts"]])      # чужие даты не портим
        rows.append(r)

    for i, r in enumerate(rows):                              # № по порядку
        if "num" in ix:
            r[ix["num"]] = i + 1
    pad = [[""] * width] * dropped                            # затираем освободившийся хвост
    known = {str(r[ix["id"]]).strip() for r in rows}
    missing = [(t, c[0]) for t, c in counts.items() if t not in known]
    if dry:
        return f"(dry) строк {len(rows)}, удалить {dropped}, обновить {touched}, ID по имени {filled}, нет в листе {missing}"
    pp._webhook({"action": "grid_write", "sheet_name": SHEET, "clear": False,
                 "row": head + 2, "rows": rows + pad})
    return (f"строк {len(rows)}, снято дублей {dropped}, обновлено {touched or '—'}, "
            f"ID проставлен {filled or '—'}, есть в базе но нет в листе: {missing or '—'}")


if __name__ == "__main__":
    print(sync(dry="--dry" in sys.argv))
