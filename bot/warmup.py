"""
Прогрев аккаунтов руками ассистента.

Список аккаунтов НЕ ведётся в боте — источник правды лист «Аккаунты» рабочей таблицы
(колонки Проект / Соцсеть / Логин / … / Дата создания). Бот читает его через
grid_dump и кэширует; фаза прогрева считается от даты создания.

Кто что делает:
- Бот в 10:00 шлёт ассистенту задачи на сегодня — по каждому аккаунту свой набор
  действий в зависимости от возраста аккаунта. Она жмёт «Готово» по каждому.
- Раз в 3 дня просит выгрузку данных одного аккаунта; присланный архив бот сам
  разбирает и шлёт админам таблицу «что реально делалось» + вердикт Claude.
- В 20:00 — напоминание ассистенту, если не всё отмечено, и отчёт админам.

Состояние — один JSON в AppState (ключ warmup_state): кэш аккаунтов + отметки.
# ponytail: один блоб под глобальной перезаписью, хватает на десятки аккаунтов;
# если дорастёт до сотен — отдельная таблица с per-row апдейтом.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import zipfile
from datetime import date, datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.sheets import sheets_client
from bot.single import _get_state, _set_state

logger = logging.getLogger(__name__)
router = Router(name="warmup")

STATE_KEY = "warmup_state"
ACCOUNTS_TAB = "Аккаунты"
# Соцсеть из таблицы → ключ в PLAN. Чего тут нет (VK) — в прогрев не берём.
SOCIAL_MAP = {"instagram": "ig", "tiktok": "tt", "youtube": "yt"}
# Ассистент (Днепр). UTC+3 летом — совпадает с МСК, зимой разойдётся на час.
# ponytail: фиксированный сдвиг, без DST — сдвиг пуша на час зимой некритичен.
KYIV = timedelta(hours=3)
ASSISTANT_ID = int(settings.assistant_id or 0)

PLATFORMS = {"ig": "📸 Instagram", "tt": "🎵 TikTok", "yt": "▶️ YouTube"}

# Схемы прогрева: (последний день фазы, что делать). Первая подходящая — берётся.
# Ролики публикует upload-post — задачи ассистента ТОЛЬКО про человеческую активность.
PLAN: dict[str, list[tuple[int, list[str]]]] = {
    "ig": [
        (1, ["оформить профиль: аватар, имя, био, 3 хайлайта", "больше НИЧЕГО не делать"]),
        # Подписок мало и в прогреве: у бренд-аккаунта раздутый список «подписки» палевен.
        (3, ["лента 10–15 мин", "5–10 лайков", "3–5 подписок по теме", "посмотреть сторис"]),
        (7, ["лента 15 мин", "10–15 лайков", "2–3 подписки", "2–3 живых комментария"]),
        (14, ["лента 15 мин", "15–20 лайков", "1–2 подписки", "3–5 сохранений",
              "1 сторис с телефона"]),
        (999, ["лента 10–15 мин", "15–20 лайков", "1–2 подписки", "2–3 комментария",
               "3–5 сохранений", "ответить на ВСЕ комментарии под нашими роликами",
               "1–2 сторис в неделю с телефона"]),
    ],
    "tt": [
        (1, ["оформить профиль: аватар, ник, био", "больше НИЧЕГО не делать"]),
        (3, ["скролл 20–30 мин, ролики ДОСМАТРИВАТЬ до конца", "5–10 лайков в своей нише",
             "3–5 подписок"]),
        (6, ["скролл 20 мин", "10 лайков", "3–5 подписок", "2–3 комментария",
             "поиск по 2–3 нишевым запросам"]),
        (999, ["скролл 15–20 мин с досмотром", "10–15 лайков", "3–5 подписок", "1–2 комментария",
               "ответить на ВСЕ комментарии под нашими роликами"]),
    ],
    "yt": [
        (1, ["оформить канал: аватар, шапка, описание", "ПОДТВЕРДИТЬ канал по телефону",
             "больше НИЧЕГО не делать"]),
        (3, ["смотреть Shorts по нише 15 мин", "5–10 лайков", "3–5 подписок"]),
        (999, ["смотреть Shorts по нише 10–15 мин", "5–10 лайков", "2–3 подписки",
               "1–2 комментария", "ответить на ВСЕ комментарии под нашими роликами"]),
    ],
}

# Раз в 3 дня просим ВЫГРУЗКУ данных аккаунта (без медиа) — там вся история действий
# с таймстемпами. Один скрин ничего не доказывает, выгрузка доказывает всё.
EXPORTS: dict[str, tuple[str, str]] = {
    "ig": ("Instagram",
           "Профиль → ☰ → <b>Центр аккаунтов</b> → Ваша информация и разрешения → "
           "<b>Скачать вашу информацию</b> → Запросить загрузку → <b>Выбрать информацию вручную</b> → "
           "отметить только <b>«Ваша активность в Instagram»</b> → формат <b>JSON</b>, "
           "диапазон «Последние 3 месяца», качество медиа — <b>низкое</b> (медиа нам не нужны)."),
    "tt": ("TikTok",
           "Профиль → ☰ → Настройки → Аккаунт → <b>Скачать мои данные</b> → вкладка "
           "«Запросить данные» → формат <b>JSON</b> (не TXT) → Запросить. Когда будет готово — "
           "вкладка «Скачать данные»."),
    "yt": ("YouTube",
           "С этого аккаунта открой <b>takeout.google.com</b> → «Отменить выбор» → отметить только "
           "<b>YouTube и YouTube Music</b> → в «Все данные YouTube» оставить <b>история</b> и "
           "<b>подписки</b> → формат <b>JSON</b> → Экспортировать."),
}
EXPORT_EVERY_DAYS = 3

RULES = (
    "⚠️ <b>Правила, без них смысла нет:</b>\n"
    "• ролики публикует бот — тебе постить НЕ надо, только живая активность\n"
    "• каждый аккаунт — со своего устройства/сессии, не прыгать между ними в одном браузере\n"
    "• сессии в разное время дня, не все персоны подряд за один заход\n"
    "• пропустила день — не догоняй двойной нормой, просто продолжай"
)


# ── Хранилище ─────────────────────────────────────────────────────────────────
async def _load() -> dict:
    empty = {"accounts": [], "done": {}, "audit": {}, "synced": ""}
    raw = await _get_state(STATE_KEY)
    if not raw:
        return empty
    try:
        st = json.loads(raw)
    except ValueError:
        logger.warning("warmup_state битый, стартуем с пустого")
        return empty
    for k, v in empty.items():
        st.setdefault(k, v)
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


def _acc_key(platform: str, login: str) -> str:
    """Ключ аккаунта для отметок. Нормализуем логин: @ и . в таблице пишут вразнобой."""
    return platform + ":" + "".join(c for c in login.lower() if c.isalnum())[:20]


def parse_created(raw) -> str:
    """«Дата создания» из таблицы → ISO-дата. Sheets отдаёт полночь по Киеву как UTC-21:00."""
    s = str(raw).strip()
    if not s:
        return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})", s)
    if m:
        d = date(int(m[1]), int(m[2]), int(m[3]))
        return (d + timedelta(days=1) if int(m[4]) >= 21 else d).isoformat()
    m = re.search(r"(\d{2})[.](\d{2})[.](\d{4})", s)  # 14.07.2026
    if m:
        return date(int(m[3]), int(m[2]), int(m[1])).isoformat()
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else ""


def day_index(start: str, today: date | None = None) -> int:
    """1-based день прогрева. Дата не распознана — считаем аккаунт зрелым."""
    try:
        s = date.fromisoformat(start)
    except ValueError:
        return 999
    return ((today or _today()) - s).days + 1


async def sync_accounts(force: bool = False) -> tuple[int, str]:
    """Лист «Аккаунты» → кэш в состоянии. → (сколько аккаунтов, ошибка или '')."""
    st = await _load()
    today = _today().isoformat()
    if not force and st.get("synced") == today and st["accounts"]:
        return len(st["accounts"]), ""
    if not settings.accounts_sheet_id:
        return len(st["accounts"]), "ACCOUNTS_SHEET_ID не задан"
    try:
        rows = await sheets_client.grid_dump(settings.accounts_sheet_id, ACCOUNTS_TAB)
    except Exception as e:  # noqa: BLE001 — таблица недоступна: работаем на старом кэше
        logger.warning("warmup sync failed: %s", e)
        return len(st["accounts"]), str(e)[:120]

    head = [str(c).strip().lower() for c in (rows[0] if rows else [])]
    col = {name: head.index(name) for name in head}
    need = ("проект", "соцсеть", "логин", "дата создания")
    if any(n not in col for n in need):
        return len(st["accounts"]), f"в листе «{ACCOUNTS_TAB}» нет колонок: {need}"

    projects = {p.strip().lower() for p in settings.warmup_projects.split(",") if p.strip()}
    accounts = []
    for r in rows[1:]:
        get = lambda n: str(r[col[n]]).strip() if col[n] < len(r) else ""  # noqa: E731
        plat = SOCIAL_MAP.get(get("соцсеть").lower())
        login, project = get("логин"), get("проект")
        if not plat or not login or project.lower() not in projects:
            continue
        accounts.append({
            "key": _acc_key(plat, login),
            "project": project,
            "platform": plat,
            "login": login if login.startswith("@") else "@" + login,
            "start": parse_created(get("дата создания")),
        })
    st["accounts"], st["synced"] = accounts, today
    await _save(st)
    return len(accounts), ""


def tasks_for(platform: str, day: int) -> list[str]:
    for last_day, tasks in PLAN.get(platform, []):
        if day <= last_day:
            return tasks
    return []


# ── Тексты ────────────────────────────────────────────────────────────────────
def _active(st: dict, today: date, platform: str | None = None) -> list[dict]:
    """Аккаунты, которым сегодня есть что делать (дата создания уже наступила)."""
    return [
        a for a in st["accounts"]
        if day_index(a["start"], today) >= 1 and (platform is None or a["platform"] == platform)
    ]


def _account_block(a: dict, day: int) -> str:
    lines = [f"{PLATFORMS[a['platform']]} <b>{a['login']}</b> · день {day}"]
    lines += [f"   • {t}" for t in tasks_for(a["platform"], day)]
    return "\n".join(lines)


def _task_text(st: dict, today: date) -> str:
    active = _active(st, today)
    if not active:
        return ""
    head = f"☀️ <b>Прогрев · {today.strftime('%d.%m')}</b>\n"
    body = "\n\n".join(_account_block(a, day_index(a["start"], today)) for a in active)
    return f"{head}\n{body}\n\n{RULES}\n\nОтмечай кнопками, когда аккаунт сделан 👇"


def _kb(st: dict, today: date) -> InlineKeyboardMarkup:
    done = set(st["done"].get(today.isoformat(), []))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{'✅' if a['key'] in done else '⬜️'} {PLATFORMS[a['platform']][0]} {a['login']}",
            callback_data=f"wu:d:{a['key']}",
        )]
        for a in _active(st, today)
    ])


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
    await sync_accounts()  # список аккаунтов берём из таблицы, не из бота
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
    """Раз в EXPORT_EVERY_DAYS просим выгрузку данных одного аккаунта (ротация платформ)."""
    today = _today()
    now = datetime.utcnow() + KYIV
    if now.hour < 15 or not ASSISTANT_ID:
        return
    if today.toordinal() % EXPORT_EVERY_DAYS:
        return
    plat = list(EXPORTS)[(today.toordinal() // EXPORT_EVERY_DAYS) % len(EXPORTS)]
    st = await _load()
    active = _active(st, today, plat)
    if not active or st["audit"].get(today.isoformat()):
        return
    # Детерминированный «случайный» выбор аккаунта: один и тот же за день, без random-состояния.
    a = active[sum(ord(c) for c in today.isoformat()) % len(active)]
    st["audit"][today.isoformat()] = {"key": a["key"], "platform": plat, "sent": False}
    await _save(st)
    title, how = EXPORTS[plat]
    await bot.send_message(
        ASSISTANT_ID,
        f"📦 <b>Выгрузка данных</b> — {title}, аккаунт <b>{a['login']}</b>\n\n{how}\n\n"
        "Файл готовится от часа до суток. Когда придёт — <b>пришли архив сюда файлом</b>, "
        "бот сам всё разберёт. Медиа не нужны, только JSON.",
    )


async def _evening(bot: Bot) -> None:
    today = _today()
    now = datetime.utcnow() + KYIV
    if now.hour < 20:
        return
    st = await _load()
    active = _active(st, today)
    if not active:
        return
    done = set(st["done"].get(today.isoformat(), []))
    late = [a for a in active if a["key"] not in done]

    if late and ASSISTANT_ID and await _once_a_day("warmup_evening_date", today):
        names = ", ".join(a["login"] for a in late)
        await bot.send_message(
            ASSISTANT_ID,
            f"🌙 Не отмечено за сегодня: <b>{names}</b>.\nЕсли сделала — отметь кнопками в утреннем сообщении.",
        )

    if not await _once_a_day("warmup_report_date", today):
        return
    audit = st["audit"].get(today.isoformat()) or {}
    audit_line = "—"
    if audit:
        who = next((a["login"] for a in st["accounts"] if a["key"] == audit["key"]), audit["key"])
        state = "выгрузка пришла ✅" if audit.get("sent") else "выгрузки ещё нет ⏳"
        audit_line = f"{who} / {audit['platform']} — {state}"

    parts = [
        f"📋 <b>Прогрев · отчёт {today.strftime('%d.%m')}</b>",
        f"Сделано: <b>{len(active) - len(late)}/{len(active)}</b>",
    ]
    for a in active:
        mark = "✅" if a["key"] in done else "❌"
        parts.append(
            f"{mark} {PLATFORMS[a['platform']][0]} {a['login']} · день "
            f"{day_index(a['start'], today)} · 7д: {_streak(st, a['key'], today)}"
        )
    parts.append(f"📦 Выгрузка: {audit_line}")
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, "\n".join(parts))
        except Exception as e:  # noqa: BLE001 — отчёт не должен ронять луп
            logger.warning("warmup report to %s failed: %s", admin_id, e)


def _streak(st: dict, key: str, today: date) -> str:
    """Сколько из последних 7 дней аккаунт был отмечен."""
    hit = sum(
        1
        for i in range(7)
        if key in st["done"].get((today - timedelta(days=i)).isoformat(), [])
    )
    return f"{hit}/7"


# ── Разбор выгрузок (Instagram / TikTok / Google Takeout) ─────────────────────
# Схемы у всех трёх разные и регулярно меняются. Вместо маппинга полей обходим весь
# JSON и собираем всё, что похоже на дату, группируя по имени файла — имя файла и есть
# название события (liked_posts, Video Browsing History, watch-history…).
# ponytail: эвристика по длине числа, не парсер схем; если начнёт врать — мапить поля.
_TS_MIN, _TS_MAX = 1_500_000_000, 2_200_000_000  # 2017-07 … 2039-09, в секундах
_SCALE = {10: 1, 13: 1000, 16: 1_000_000}  # сек / мс / мкс; id обычно 17–19 цифр — мимо
_ISO = re.compile(r"20\d{2}-\d{2}-\d{2}")


def _harvest(node, out: list[str]) -> None:
    if isinstance(node, dict):
        for v in node.values():
            _harvest(v, out)
    elif isinstance(node, list):
        for v in node:
            _harvest(v, out)
    elif isinstance(node, bool):
        return  # bool — подкласс int, но датой не бывает
    elif isinstance(node, int):
        scale = _SCALE.get(len(str(abs(node))))
        if scale and _TS_MIN <= abs(node) / scale <= _TS_MAX:
            out.append(date.fromtimestamp(abs(node) / scale).isoformat())
    elif isinstance(node, str):
        m = _ISO.match(node)
        if m:
            out.append(m.group(0))


def parse_export(raw: bytes) -> dict[str, dict[str, int]]:
    """Архив или голый JSON → {метка события: {дата: сколько раз}}."""
    files: list[tuple[str, bytes]] = []
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in z.namelist():
                if n.lower().endswith(".json") and z.getinfo(n).file_size < 30_000_000:
                    files.append((n, z.read(n)))
    else:
        files.append(("export", raw))

    counts: dict[str, dict[str, int]] = {}
    for name, blob in files:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        dates: list[str] = []
        _harvest(data, dates)
        if not dates:
            continue
        label = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        bucket = counts.setdefault(label, {})
        for d in dates:
            bucket[d] = bucket.get(d, 0) + 1
    return counts


def _export_table(counts: dict[str, dict[str, int]], today: date, days: int = 7) -> str:
    span = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    rows = [(lbl, by) for lbl, by in counts.items() if any(by.get(d) for d in span)]
    if not rows:
        return "За последние 7 дней активности в выгрузке нет."
    rows.sort(key=lambda kv: -sum(kv[1].get(d, 0) for d in span))
    lines = ["событие".ljust(16) + "".join(f"{int(d[8:]):>4}" for d in span)]
    for label, by_date in rows[:10]:
        lines.append(label[:16].ljust(16) + "".join(f"{min(by_date.get(d, 0), 9999):>4}" for d in span))
    lines.append(f"(числа сверху — дни месяца, {span[0][:7]} → {span[-1][:7]})")
    return "\n".join(lines)


async def _analyze(table: str, who: str) -> str:
    """Короткий вердикт от Claude по таблице. Без ключа — просто цифры."""
    if not settings.anthropic_api_key:
        return "🔑 ANTHROPIC_API_KEY не задан — вердикта нет, смотри цифры."
    try:
        from anthropic import AsyncAnthropic

        r = await AsyncAnthropic(api_key=settings.anthropic_api_key).messages.create(
            model="claude-opus-5",
            max_tokens=2000,
            system=(
                "Ты контролируешь прогрев аккаунтов в соцсетях. На входе — таблица событий "
                "из официальной выгрузки данных аккаунта, по дням за неделю. "
                "Ответь по-русски, максимум 6 строк, без вступлений: (1) велась ли работа реально "
                "или есть мёртвые дни, (2) ровная активность или рывками, (3) хватает ли объёма "
                "для прогрева (ориентир: 10–20 лайков, 3–10 подписок, 10–20 минут просмотра в день), "
                "(4) что сказать ассистенту. Если данных мало — так и скажи, не выдумывай."
            ),
            messages=[{"role": "user", "content": f"Аккаунт: {who}\n\n{table}"}],
        )
        return next((b.text for b in r.content if b.type == "text"), "")
    except Exception as e:  # noqa: BLE001 — разбор не должен ронять хендлер
        logger.warning("claude analyze failed: %s", e)
        return "⚠️ Вердикт Claude не получился, смотри цифры выше."


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


@router.message(F.document, F.from_user.id == ASSISTANT_ID)
async def wu_export_file(msg: Message, bot: Bot):
    """Архив выгрузки от ассистента → счётчики действий по дням → отчёт админам."""
    name = (msg.document.file_name or "").lower()
    if not name.endswith((".zip", ".json")):
        raise SkipHandler  # не выгрузка — пусть идёт дальше по роутерам
    if msg.document.file_size > 19 * 1024 * 1024:
        await msg.answer(
            "Файл больше 20 МБ — Telegram не даёт боту его скачать 🙈\n"
            "Перезапроси выгрузку без медиа (только «Ваша активность» / только история и подписки)."
        )
        return
    await msg.answer("Взял файл, разбираю… это займёт минуту ⏳")
    try:
        buf = await bot.download(msg.document)
        counts = parse_export(buf.read())
    except Exception as e:  # noqa: BLE001 — битый архив не должен ронять хендлер
        logger.warning("export parse failed: %s", e)
        await msg.answer("Не смог разобрать архив 🙈 Проверь, что выгрузка в формате JSON, и пришли ещё раз.")
        return
    if not counts:
        await msg.answer("В архиве не нашёл истории действий. Похоже, выгрузка без раздела активности.")
        return

    today = _today()
    st = await _load()
    audit = st["audit"].get(today.isoformat()) or {}
    who = next((a["login"] for a in st["accounts"] if a["key"] == audit.get("key")), "—")
    if audit:
        audit["sent"] = True
        await _save(st)

    table = _export_table(counts, today)
    verdict = await _analyze(table, who)
    report = (
        f"📦 <b>Выгрузка: {who}</b> · файл <code>{msg.document.file_name}</code>\n\n"
        f"<pre>{table}</pre>\n{verdict}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, report)
        except Exception as e:  # noqa: BLE001
            logger.warning("export report to %s failed: %s", admin_id, e)
    await msg.answer("Разобрал, отчёт ушёл Игорю 👌")


@router.message(F.text.startswith("/wu_sync"))
async def wu_sync(msg: Message):
    """Перечитать лист «Аккаунты» прямо сейчас (обычно синк идёт сам перед утренним пушем)."""
    if not settings.is_admin(msg.from_user.id):
        return
    n, err = await sync_accounts(force=True)
    await msg.answer(f"⚠️ {err}" if err else f"Синк ок: {n} аккаунтов из листа «{ACCOUNTS_TAB}».")


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
    await sync_accounts()
    st = await _load()
    if not st["accounts"]:
        await msg.answer(
            f"Аккаунтов нет. Проверь лист «{ACCOUNTS_TAB}»: колонки Проект / Соцсеть / Логин / "
            f"Дата создания, проект — {settings.warmup_projects}. Потом /wu_sync."
        )
        return
    if not settings.is_admin(msg.from_user.id):
        await msg.answer(_task_text(st, today), reply_markup=_kb(st, today))
        return
    done = set(st["done"].get(today.isoformat(), []))
    lines = [f"📊 <b>Прогрев · {today.strftime('%d.%m')}</b> · синк {st.get('synced') or '—'}"]
    for a in st["accounts"]:
        d = day_index(a["start"], today)
        mark = "✅" if a["key"] in done else ("⬜️" if d >= 1 else "🕓")
        lines.append(
            f"{mark} {PLATFORMS[a['platform']][0]} <b>{a['login']}</b> · {a['project']} · "
            f"день {d} · {_streak(st, a['key'], today)}"
        )
    lines.append("\n/wu_push — переслать задание сейчас, /wu_sync — перечитать таблицу")
    await msg.answer("\n".join(lines))


def demo() -> None:
    """Самопроверка фаз и парсера выгрузок — два места, где легко ошибиться."""
    assert day_index("2026-08-01", date(2026, 8, 1)) == 1
    assert day_index("2026-08-01", date(2026, 8, 10)) == 10
    assert day_index("2026-08-05", date(2026, 8, 1)) == -3  # создан в будущем → пока не трогаем
    assert day_index("", date(2026, 8, 1)) == 999           # даты нет → считаем зрелым

    # «Дата создания» из таблицы: Sheets отдаёт полночь по Киеву как 21:00 UTC накануне.
    assert parse_created("2026-07-13T21:00:00.000Z") == "2026-07-14"
    assert parse_created("2026-07-14T09:30:00.000Z") == "2026-07-14"
    assert parse_created("14.07.2026") == "2026-07-14"
    assert parse_created("2026-07-14") == "2026-07-14"
    assert parse_created("") == "" and parse_created("скоро") == ""
    assert _acc_key("tt", "@Skazhi.Pesney") == _acc_key("tt", "skazhipesney")
    assert "больше НИЧЕГО не делать" in tasks_for("tt", 1)
    assert tasks_for("yt", 500) == PLAN["yt"][-1][1]
    assert tasks_for("xx", 3) == []
    assert tasks_for("ig", 3) != tasks_for("ig", 4)  # границы фаз ig: 1 / 2-3 / 4-7 / 8-14 / 15+
    assert tasks_for("ig", 14) != tasks_for("ig", 15)
    # публикацию из задач убрали — постит upload-post; Shorts остаются только «смотреть»
    assert not any("Reels" in t for ph in PLAN["ig"] for t in ph[1])
    assert all("Shorts" not in t or "смотреть" in t for ph in PLAN["yt"] for t in ph[1])

    # Парсер: три реальные формы даты (IG unix-сек, TikTok строка, Takeout ISO) + мусор.
    ig = {"likes_media_likes": [{"string_list_data": [{"timestamp": 1785000000}]}]}
    tt = {"Activity": {"Like List": {"ItemFavoriteList": [{"date": "2026-08-01 12:00:00"}]}}}
    yt = [{"time": "2026-08-01T12:00:00.000Z", "titleUrl": "https://x"}]
    noise = {"id": 17851234567890123, "ok": True, "ratio": 1.5, "name": "2026 год"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a/liked_posts.json", json.dumps(ig))
        z.writestr("b/Like List.json", json.dumps(tt))
        z.writestr("c/watch-history.json", json.dumps(yt))
        z.writestr("d/noise.json", json.dumps(noise))
    counts = parse_export(buf.getvalue())
    assert counts["liked_posts"] == {date.fromtimestamp(1785000000).isoformat(): 1}, counts
    assert counts["Like List"] == {"2026-08-01": 1}, counts
    assert counts["watch-history"] == {"2026-08-01": 1}, counts
    assert "noise" not in counts, counts  # id/bool/float/текст датами не считаем
    assert parse_export(json.dumps(yt).encode())["export"] == {"2026-08-01": 1}
    table = _export_table(counts, date(2026, 8, 3))
    assert "watch-history" in table and "событие" in table
    assert "нет" in _export_table({}, date(2026, 8, 3))
    print("warmup demo ok")


if __name__ == "__main__":
    demo()
