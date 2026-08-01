"""
Прогрев аккаунтов (Сингл / PapKids) руками ассистента.

Кто что делает:
- Бот в 10:00 шлёт ассистенту список задач на сегодня — по каждой персоне свой день
  прогрева, по каждой соцсети свой набор действий. Она жмёт «Готово» по персоне.
- В 15:00 бот просит скрин ОДНОГО случайного аккаунта (выборочный аудит) — фото
  улетает админам. Проверка не отчёта, а состояния аккаунта.
- В 20:00 — напоминание ассистенту, если не всё отмечено, и отчёт админам
  (сделано/не сделано + дисциплина за 7 дней).

Состояние — один JSON в AppState (ключ warmup_state). Отдельная таблица не нужна:
7 персон × 30 дней истории — это килобайты.
# ponytail: один блоб под глобальной перезаписью, хватает на десятки персон;
# если дорастёт до сотен аккаунтов — отдельная таблица с per-row апдейтом.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.single import _get_state, _set_state

logger = logging.getLogger(__name__)
router = Router(name="warmup")

STATE_KEY = "warmup_state"
# Ассистент (Днепр). UTC+3 летом — совпадает с МСК, зимой разойдётся на час.
# ponytail: фиксированный сдвиг, без DST — сдвиг пуша на час зимой некритичен.
KYIV = timedelta(hours=3)
ASSISTANT_ID = int(settings.assistant_id or 0)

PLATFORMS = {"ig": "📸 Instagram", "tt": "🎵 TikTok", "yt": "▶️ YouTube"}

# Схемы прогрева: (последний день фазы, что делать). Первая подходящая — берётся.
PLAN: dict[str, list[tuple[int, list[str]]]] = {
    "ig": [
        (1, ["оформить профиль: аватар, имя, био, 3 хайлайта", "1 пост в ленту", "больше НИЧЕГО не делать"]),
        (3, ["скролл ленты 10–15 мин", "5–10 лайков", "5–8 подписок по теме", "посмотреть сторис"]),
        (7, ["скролл 15 мин", "10–15 лайков", "8–10 подписок", "2–3 живых комментария",
             "с 5-го дня — 1 Reels"]),
        (14, ["1 Reels", "15–20 лайков", "до 10 подписок", "3–5 сохранений чужих постов"]),
        (21, ["1–2 Reels", "≈20 лайков", "лента 10 мин"]),
        (999, ["рабочий режим: 1–2 Reels", "лента 10 мин для активности"]),
    ],
    "tt": [
        (1, ["оформить профиль: аватар, ник, био", "больше НИЧЕГО не делать"]),
        (3, ["скролл 20–30 мин, ролики ДОСМАТРИВАТЬ до конца", "5–10 лайков в своей нише",
             "3–5 подписок"]),
        (6, ["скролл 20 мин", "10 лайков", "3–5 подписок", "2–3 комментария", "1 репост в сторис",
             "поиск по 2–3 нишевым запросам"]),
        (10, ["1 ролик (слот 12:00–14:00 или 18:00–22:00)", "скролл 15 мин", "10 лайков",
              "3 подписки", "ссылку в био НЕ ставить"]),
        (21, ["1–2 ролика", "скролл 15 мин", "10 лайков"]),
        (999, ["рабочий режим: 2–3 ролика", "скролл 10 мин"]),
    ],
    "yt": [
        (1, ["оформить канал: аватар, шапка, описание", "ПОДТВЕРДИТЬ канал по телефону",
             "больше НИЧЕГО не делать"]),
        (3, ["смотреть Shorts по нише 15 мин", "3–5 подписок", "1 обычное (не Shorts) видео на канал"]),
        (7, ["1–2 Shorts", "смотреть по нише 10 мин", "лайки/комменты чужим"]),
        (14, ["2–3 Shorts", "10 мин просмотра"]),
        (999, ["рабочий режим: 3–5 Shorts"]),
    ],
}

RULES = (
    "⚠️ <b>Правила, без них смысла нет:</b>\n"
    "• каждый аккаунт — со своего устройства/сессии, не прыгать между ними в одном браузере\n"
    "• сессии в разное время дня, не все 7 персон подряд за один заход\n"
    "• не копировать один ролик на несколько аккаунтов без уникализации\n"
    "• пропустила день — не догоняй двойной нормой, просто продолжай"
)


# ── Хранилище ─────────────────────────────────────────────────────────────────
async def _load() -> dict:
    raw = await _get_state(STATE_KEY)
    if not raw:
        return {"personas": [], "done": {}, "audit": {}}
    try:
        st = json.loads(raw)
    except ValueError:
        logger.warning("warmup_state битый, стартуем с пустого")
        return {"personas": [], "done": {}, "audit": {}}
    st.setdefault("personas", [])
    st.setdefault("done", {})
    st.setdefault("audit", {})
    return st


async def _save(st: dict) -> None:
    # История нужна только для дисциплины за 7 дней — держим 30 последних дней.
    for bucket in ("done", "audit"):
        keys = sorted(st.get(bucket, {}))
        for k in keys[:-30]:
            st[bucket].pop(k, None)
    await _set_state(STATE_KEY, json.dumps(st, ensure_ascii=False))


def _today() -> date:
    return (datetime.utcnow() + KYIV).date()


def _slug(name: str) -> str:
    return "".join(c for c in name.lower() if c.isalnum())[:16] or "p"


def day_index(start: str, today: date | None = None) -> int:
    """1-based день прогрева. До даты старта — 0."""
    try:
        s = date.fromisoformat(start)
    except ValueError:
        return 1
    return ((today or _today()) - s).days + 1


def tasks_for(platform: str, day: int) -> list[str]:
    for last_day, tasks in PLAN.get(platform, []):
        if day <= last_day:
            return tasks
    return []


# ── Тексты ────────────────────────────────────────────────────────────────────
def _persona_block(p: dict, day: int) -> str:
    lines = [f"<b>{p['name']}</b> · {p.get('project', '—')} · день {day}"]
    for plat, title in PLATFORMS.items():
        handle = p.get("handles", {}).get(plat)
        if not handle:
            continue
        lines.append(f"{title} {handle}")
        lines += [f"   • {t}" for t in tasks_for(plat, day)]
    return "\n".join(lines)


def _task_text(st: dict, today: date) -> str:
    active = [p for p in st["personas"] if day_index(p["start"], today) >= 1]
    if not active:
        return ""
    head = f"☀️ <b>Прогрев · {today.strftime('%d.%m')}</b>\n"
    body = "\n\n".join(_persona_block(p, day_index(p["start"], today)) for p in active)
    return f"{head}\n{body}\n\n{RULES}\n\nОтмечай кнопками, когда персона сделана 👇"


def _kb(st: dict, today: date) -> InlineKeyboardMarkup:
    done = set(st["done"].get(today.isoformat(), []))
    rows = []
    for p in st["personas"]:
        if day_index(p["start"], today) < 1:
            continue
        mark = "✅" if p["key"] in done else "⬜️"
        rows.append([InlineKeyboardButton(text=f"{mark} {p['name']}", callback_data=f"wu:d:{p['key']}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Пуши ──────────────────────────────────────────────────────────────────────
async def _once_a_day(key: str, today: date) -> bool:
    """True, если сегодня это ещё не делали (и сразу помечает)."""
    if await _get_state(key) == today.isoformat():
        return False
    await _set_state(key, today.isoformat())
    return True


async def push_tasks(bot: Bot, force: bool = False) -> bool:
    today = _today()
    if not force and not await _once_a_day("warmup_push_date", today):
        return False
    st = await _load()
    text = _task_text(st, today)
    if not text:
        return False
    if not ASSISTANT_ID:
        logger.warning("ASSISTANT_ID не задан — задачи прогрева некому слать")
        return False
    await bot.send_message(ASSISTANT_ID, text, reply_markup=_kb(st, today))
    return True


async def _push_audit(bot: Bot) -> None:
    today = _today()
    now = datetime.utcnow() + KYIV
    if now.hour < 15 or not ASSISTANT_ID:
        return
    st = await _load()
    active = [p for p in st["personas"] if day_index(p["start"], today) >= 1]
    if not active or st["audit"].get(today.isoformat()):
        return
    # Детерминированный «случайный» выбор: одна и та же пара за день, без random-состояния.
    seed = sum(ord(c) for c in today.isoformat())
    p = active[seed % len(active)]
    plats = [k for k in PLATFORMS if p.get("handles", {}).get(k)]
    if not plats:
        return
    plat = plats[seed % len(plats)]
    st["audit"][today.isoformat()] = {"key": p["key"], "platform": plat, "sent": False}
    await _save(st)
    await bot.send_message(
        ASSISTANT_ID,
        f"🔍 <b>Выборочная проверка</b>\nПришли сюда скрин профиля "
        f"{PLATFORMS[plat]} {p['handles'][plat]} — так, чтобы было видно подписки и последние действия.",
    )


async def _evening(bot: Bot) -> None:
    today = _today()
    now = datetime.utcnow() + KYIV
    if now.hour < 20:
        return
    st = await _load()
    active = [p for p in st["personas"] if day_index(p["start"], today) >= 1]
    if not active:
        return
    done = set(st["done"].get(today.isoformat(), []))
    late = [p for p in active if p["key"] not in done]

    if late and ASSISTANT_ID and await _once_a_day("warmup_evening_date", today):
        names = ", ".join(p["name"] for p in late)
        await bot.send_message(
            ASSISTANT_ID,
            f"🌙 Не отмечено за сегодня: <b>{names}</b>.\nЕсли сделала — отметь кнопками в утреннем сообщении.",
        )

    if not await _once_a_day("warmup_report_date", today):
        return
    audit = st["audit"].get(today.isoformat()) or {}
    audit_line = "—"
    if audit:
        who = next((p["name"] for p in st["personas"] if p["key"] == audit["key"]), audit["key"])
        audit_line = f"{who} / {audit['platform']} — {'скрин прислан ✅' if audit.get('sent') else 'скрина нет ❌'}"

    parts = [
        f"📋 <b>Прогрев · отчёт {today.strftime('%d.%m')}</b>",
        f"Сделано: <b>{len(active) - len(late)}/{len(active)}</b>",
    ]
    for p in active:
        d = day_index(p["start"], today)
        mark = "✅" if p["key"] in done else "❌"
        parts.append(f"{mark} {p['name']} · день {d} · дисциплина 7д: {_streak(st, p['key'], today)}")
    parts.append(f"🔍 Аудит: {audit_line}")
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, "\n".join(parts))
        except Exception as e:  # noqa: BLE001 — отчёт не должен ронять луп
            logger.warning("warmup report to %s failed: %s", admin_id, e)


def _streak(st: dict, key: str, today: date) -> str:
    """Сколько из последних 7 дней персона была отмечена."""
    hit = sum(
        1
        for i in range(7)
        if key in st["done"].get((today - timedelta(days=i)).isoformat(), [])
    )
    return f"{hit}/7"


async def run_warmup_loop(bot: Bot, interval: int = 600) -> None:
    await asyncio.sleep(120)
    while True:
        try:
            now = datetime.utcnow() + KYIV
            if now.hour >= 10:
                await push_tasks(bot)
            await _push_audit(bot)
            await _evening(bot)
        except Exception as e:  # noqa: BLE001 — луп не должен падать
            logger.warning("warmup loop error: %s", e)
        await asyncio.sleep(interval)


# ── Хендлеры ──────────────────────────────────────────────────────────────────
def _allowed(user_id: int) -> bool:
    return settings.is_admin(user_id) or user_id == ASSISTANT_ID


@router.callback_query(F.data.startswith("wu:d:"))
async def wu_done(call: CallbackQuery):
    if not _allowed(call.from_user.id):
        await call.answer()
        return
    key = call.data.split(":", 2)[2]
    today = _today()
    st = await _load()
    day_done = st["done"].setdefault(today.isoformat(), [])
    if key in day_done:
        day_done.remove(key)  # повторное нажатие — снять отметку
    else:
        day_done.append(key)
    await _save(st)
    await call.answer("Отмечено" if key in day_done else "Снято")
    try:
        await call.message.edit_reply_markup(reply_markup=_kb(st, today))
    except Exception:  # noqa: BLE001 — сообщение могло не измениться
        pass


@router.message(F.photo, F.from_user.id == ASSISTANT_ID)
async def wu_audit_photo(msg: Message, bot: Bot):
    """Скрин от ассистента по запросу аудита → админам. Если аудита нет — не мешаем."""
    today = _today()
    st = await _load()
    audit = st["audit"].get(today.isoformat())
    if not audit or audit.get("sent"):
        return
    who = next((p["name"] for p in st["personas"] if p["key"] == audit["key"]), audit["key"])
    for admin_id in settings.admin_ids:
        try:
            await bot.send_photo(
                admin_id, msg.photo[-1].file_id,
                caption=f"🔍 Аудит прогрева: <b>{who}</b> · {PLATFORMS[audit['platform']]}",
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("audit photo to %s failed: %s", admin_id, e)
    audit["sent"] = True
    await _save(st)
    await msg.answer("Принято, передала админам 👌")


@router.message(F.text.startswith("/wu_add"))
async def wu_add(msg: Message):
    """/wu_add + строки вида: Проект | Имя | @ig | @tt | @yt | ДД.ММ(необязательно)"""
    if not settings.is_admin(msg.from_user.id):
        return
    lines = [ln.strip() for ln in msg.text.splitlines()[1:] if ln.strip()]
    if not lines:
        await msg.answer(
            "Формат (каждая персона с новой строки, первая строка — только команда):\n"
            "<code>/wu_add\nСингл | Егор | @egor_ig | @egor_tt | @egor_yt | 02.08</code>\n\n"
            "Дата — день старта прогрева, можно не указывать (тогда сегодня). "
            "Ненужную соцсеть — прочерк <code>-</code>."
        )
        return
    st = await _load()
    added = []
    for ln in lines:
        parts = [x.strip() for x in ln.split("|")]
        if len(parts) < 5:
            continue
        project, name, ig, tt, yt = parts[:5]
        start = _today()
        if len(parts) > 5 and parts[5]:
            try:
                d, m = parts[5].split(".")[:2]
                start = date(_today().year, int(m), int(d))
            except ValueError:
                pass
        handles = {k: v for k, v in (("ig", ig), ("tt", tt), ("yt", yt)) if v and v != "-"}
        key = _slug(name)
        st["personas"] = [p for p in st["personas"] if p["key"] != key]
        st["personas"].append(
            {"key": key, "name": name, "project": project, "start": start.isoformat(), "handles": handles}
        )
        added.append(f"{name} ({project}) с {start.strftime('%d.%m')}")
    await _save(st)
    await msg.answer("Добавлено:\n• " + "\n• ".join(added) if added else "Не разобрал ни одной строки.")


@router.message(F.text.startswith("/wu_del"))
async def wu_del(msg: Message):
    if not settings.is_admin(msg.from_user.id):
        return
    name = msg.text.replace("/wu_del", "", 1).strip()
    st = await _load()
    before = len(st["personas"])
    st["personas"] = [p for p in st["personas"] if p["key"] != _slug(name)]
    await _save(st)
    await msg.answer("Удалено" if len(st["personas"]) < before else f"Не нашёл персону «{name}»")


@router.message(F.text.startswith("/wu_push"))
async def wu_push(msg: Message, bot: Bot):
    if not settings.is_admin(msg.from_user.id):
        return
    ok = await push_tasks(bot, force=True)
    await msg.answer("Отправил ассистенту" if ok else "Некому/нечего слать — проверь /wu и ASSISTANT_ID")


@router.message(F.text.startswith("/wu"))
async def wu_status(msg: Message):
    """Статус прогрева. Ассистенту — задачи на сегодня, админу — сводка."""
    if not _allowed(msg.from_user.id):
        return
    today = _today()
    st = await _load()
    if not st["personas"]:
        await msg.answer("Персон нет. Добавь через /wu_add — пришлю формат.")
        return
    if not settings.is_admin(msg.from_user.id):
        await msg.answer(_task_text(st, today), reply_markup=_kb(st, today))
        return
    done = set(st["done"].get(today.isoformat(), []))
    lines = [f"📊 <b>Прогрев · {today.strftime('%d.%m')}</b>"]
    for p in st["personas"]:
        d = day_index(p["start"], today)
        mark = "✅" if p["key"] in done else ("⬜️" if d >= 1 else "🕓")
        hs = " ".join(f"{PLATFORMS[k][0]}{v}" for k, v in p.get("handles", {}).items())
        lines.append(f"{mark} <b>{p['name']}</b> · {p['project']} · день {d} · {_streak(st, p['key'], today)}\n   {hs}")
    lines.append("\n/wu_push — переслать задание сейчас, /wu_add — добавить, /wu_del — убрать")
    await msg.answer("\n".join(lines))


def demo() -> None:
    """Самопроверка фазовой логики — единственное место, где легко ошибиться."""
    assert day_index("2026-08-01", date(2026, 8, 1)) == 1
    assert day_index("2026-08-01", date(2026, 8, 10)) == 10
    assert day_index("2026-08-05", date(2026, 8, 1)) == -3  # старт в будущем → неактивна
    assert "больше НИЧЕГО не делать" in tasks_for("tt", 1)
    assert any("ролик" in t for t in tasks_for("tt", 8))
    assert tasks_for("yt", 500) == PLAN["yt"][-1][1]
    assert tasks_for("xx", 3) == []
    # границы фаз ig: 1 / 2-3 / 4-7 / 8-14 / 15-21 / 22+
    assert tasks_for("ig", 3) != tasks_for("ig", 4)
    assert tasks_for("ig", 21) != tasks_for("ig", 22)
    print("warmup demo ok")


if __name__ == "__main__":
    demo()
