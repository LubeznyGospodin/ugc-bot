"""
Пайплайн дожима до ролика — ТОЛЬКО проект «Сингл» (br1).

Этапы: offered → accepted → producing → submitted.
- offered   ставит sync (админ поставил «оффер» в таблице) → бот шлёт условия + «Участвую».
- accepted  креатор нажал «Участвую» → бот просит срок.
- producing креатор задал срок → срок в таблицу, «не подведи», напоминания.
- submitted креатор прислал ссылки на посты (после утверждения ролика в @packman_hr) → цикл закрыт.

Здесь: конфиг/тексты, парсер даты, переходы в БД, фоновый луп (напоминания креатору +
утренний отчёт админам в 10:00 МСК), подсчёт воронки.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime, timedelta

from aiogram import Bot
from sqlalchemy import select

from bot.config import settings
from bot.database import get_session
from bot.keyboards import deadline_push_keyboard, wave2_date_keyboard
from bot.models import AppState, CachedApplication, ReachRow, SinglePipeline

logger = logging.getLogger(__name__)

SINGLE_BRAND_ID = "br1"
_LINK_FORMATS = "https://docs.google.com/document/d/1oZcNVVlJsKL2g8sbTOQ70YT-epSWQKZsuFJMe_p3Rpg/edit?tab=t.0"
_LINK_CONDITIONS = "https://docs.google.com/document/d/1vlNOuAwyPzstk_F6RzeJagkpzw9DTsJq9_4JUhB4vDs/edit"

MSK = timedelta(hours=3)  # сервер в UTC; для «утра по Москве»


def is_single(brand_title: str | None) -> bool:
    """Относится ли отклик к проекту «Сингл»/ЗВУК. Проект в таблице под двумя именами:
    «Сингл (…СберЗВУК)» и старое «ИИ-Композитор (…ЗВУК)» — считаем их одним проектом."""
    t = (brand_title or "").lower()
    return ("звук" in t) or ("сингл" in t) or ("композитор" in t)


# ── Тексты ────────────────────────────────────────────────────────────────────
OFFER_TEXT = (
    "🎉 <b>Тебя одобрили на проект «Сингл» (ИИ-треки от ЗВУК)!</b>\n\n"
    "Освежи в памяти перед стартом:\n"
    f'📄 <a href="{_LINK_FORMATS}">Форматы и идеи сценариев</a>\n'
    f'📋 <a href="{_LINK_CONDITIONS}">Условия участия</a>\n\n'
    "Готов(а) участвовать? Жми кнопку 👇"
)
# Экран подтверждения участия: план работы + @packman_hr + вопрос о сроке.
ACCEPT_TEXT = (
    "🎉 Отлично, ты в проекте «Сингл»!\n\n"
    "<b>План работы:</b>\n"
    "1️⃣ Сначала делаем 1 ролик\n"
    "2️⃣ Утверждаем с нами\n"
    "3️⃣ Постим во все соцсети (обрати внимание! в этом проекте ты постишь в свои соцсети, "
    "но без призывов и ссылок)\n"
    "4️⃣ Оцениваем охваты\n"
    "5️⃣ Если совокупный охват хороший — заказываем ещё ролики\n\n"
    "💰 Оплату сделаем в течение двух дней после постинга ролика — реквизиты запрошу отдельно.\n\n"
    '💬 Также напиши нам в чат <a href="https://t.me/packman_hr">@packman_hr</a> слово '
    "«сингл» — туда дальше можно задавать любые вопросы.\n\n"
    "📅 А теперь к делу: к какой дате сделаешь ролик? Напиши в формате <b>ДД.ММ</b> "
    "(например, 20.07).\n"
    "📌 Важно: план по охватам до <b>15.08</b> — чем раньше скинешь ролик, тем лучше!"
)
BAD_DEADLINE_TEXT = "Не понял дату 🙈 Напиши в формате <b>ДД.ММ</b>, например 20.07."
# Короткий запрос срока — для возврата к шагу из «Мои проекты» (без плана работы).
ASK_DEADLINE_RESUME = (
    "📅 К какой дате сделаешь ролик? Напиши в формате <b>ДД.ММ</b> (например, 20.07).\n"
    "📌 План по охватам до <b>15.08</b> — чем раньше, тем лучше."
)
ASK_PAYMENT_TEXT = (
    "💳 Пришли номер телефона для оплаты и желаемый банк для перевода по СБП одним "
    "сообщением (например: <code>89991234567 Сбербанк</code>)."
)


def producing_text(deadline: date) -> str:
    return (
        f"Принято — ждём ролик к <b>{deadline.strftime('%d.%m')}</b> 💪\n\n"
        "❗️Важно: не подведи. По твоей ответственности и срокам мы решаем, "
        "брать ли тебя в другие проекты.\n\n"
        "<b>Как сдавать:</b>\n"
        "1️⃣ Готовый ролик отправь на утверждение в "
        '<a href="https://t.me/packman_hr">@packman_hr</a> (не сюда и НЕ файлом).\n'
        "2️⃣ После утверждения выложи его во все свои соцсети (чем больше — тем лучше, "
        "без призывов и ссылок).\n"
        "3️⃣ Пришли СЮДА ссылки на посты — по кнопке ниже."
    )


ASK_LINKS_TEXT = (
    "Пришли ссылки на опубликованные ролики (можно несколько, каждую с новой строки).\n\n"
    "⚠️ Только <b>ссылки</b> на посты в соцсетях — не файл. Сам ролик на утверждение — в "
    '<a href="https://t.me/packman_hr">@packman_hr</a>.'
)
NOT_A_LINK_TEXT = (
    "Это не похоже на ссылку 🙈 Пришли ссылки на посты (начинаются с http…). "
    "Файлы сюда не нужно — ролик на утверждение отправляй в @packman_hr."
)
# После сдачи ссылок — поздравление + запрос реквизитов для оплаты по СБП.
DONE_TEXT = (
    "Супер! 🚀 Желаю тебе в скором времени под этим рилсом написать и закрепить "
    "комментарий в духе: «раз этот рилс улетел в космос, давайте расскажу о себе…». "
    "Точнее — иметь повод так сделать 😉\n\n"
    "💳 Пришли, пожалуйста, сюда номер телефона для оплаты и желаемый банк для перевода "
    "по СБП. В течение двух дней оплатим фиксированную часть, если она у тебя предусмотрена."
)
PAYMENT_SAVED_TEXT = (
    "Принято, спасибо! Передали реквизиты в оплату 🙌\n\n"
    "Оплата поступит на карту в течение двух дней."
)
BAD_PAYMENT_TEXT = (
    "Не увидел номер телефона 🙈 Пришли, пожалуйста, номер телефона и банк одним сообщением, "
    "например: <code>89991234567 Сбербанк</code>."
)


# ── Вторая волна: перезалив старого ролика + новый ─────────────────────────────
WAVE2_BOT = "@video_zhora_bot"


def _g(name: str | None, verb: str) -> str:
    """Глагол прошедшего времени по полу из имени: «сделал» → «сделал» / «сделала» /
    «сделал(а)», если пол не определился. Определялка общая с карточками (bot.cards)."""
    from bot.cards import guess_gender

    return verb + {"female": "а", "male": ""}.get(guess_gender(name) or "", "(а)")


def wave2_text(name: str | None) -> str:
    """Оффер 2-й волны. Имя — только первое слово из «Имя» в таблице."""
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    hello = (f"Привет, {first}!" if first else "Привет!") + " Я по проекту «Сингл» от ЗВУКа."
    return (
        f"{hello}\n\n"
        f"Ты {_g(name, 'сделал')} классный ролик, но он, к сожалению, собрал незаслуженно "
        "мало охватов.\n\n"
        "Я хочу попросить тебя опубликовать его ещё раз и сделать ещё один — более мощный "
        "и креативный ролик.\n\n"
        "💰 Мы заплатим <b>50 ₽ за каждые 1000 просмотров</b>, но не больше 500 тыс. "
        "То есть можно заработать <b>25 тыс. с ролика</b>.\n\n"
        f"Но это не главное. Главное — я дам тебе <b>бесплатный доступ</b> к этому боту: {WAVE2_BOT} — "
        "он технически уникализирует ролики, чтобы их можно было безопасно перевыкладывать и "
        "собирать охваты! Сможешь использовать и для своих целей 😉\n\n"
        "Идёт?"
    )


WAVE2_ASK_DATE = (
    "🔥 Отлично!\n\n"
    "Можешь, пожалуйста, написать дату, когда опубликуешь новые посты? "
    "Формат <b>ДД.ММ</b> (например, 05.08).\n\n"
    "И вот дублирую форматы и требования к ролику, чтобы тебе не искать:\n"
    f'📄 <a href="{_LINK_FORMATS}">Форматы и идеи сценариев</a>'
)
WAVE2_STILL_ON_TEXT = "Отлично, ждём 💪"


def wave2_no_text(name: str | None) -> str:
    return f"Понял, спасибо, что {_g(name, 'ответил')} 🙏 Если передумаешь — просто напиши сюда."


def wave2_drop_text(name: str | None) -> str:
    return f"Принято, снимаю с этой волны. Спасибо, что {_g(name, 'предупредил')} 🙏"


def wave2_accepted_text(dl: date) -> str:
    return (
        f"Записал: публикуешь <b>{dl.strftime('%d.%m')}</b> 🚀\n\n"
        f"Не забудь: доступ к {WAVE2_BOT} для уникализации — за тобой.\n"
        "Как выложишь — пришли сюда ссылки на посты, учтём охваты."
    )


async def wave2_audience() -> list[tuple[int, str]]:
    """Кому слать 2-ю волну: участники «Сингл», у кого в листе «Отклики» заполнена
    колонка «Ссылка на ролик» (кол. L) — т.е. они уже сделали ролик.
    → [(chat_id, имя)]."""
    from bot.models import Creator

    async with get_session() as s:
        apps = (await s.execute(select(CachedApplication))).scalars().all()
        creators = {c.tg_id: c for c in (await s.execute(select(Creator))).scalars().all()}
    seen: dict[int, str] = {}
    for a in apps:
        if not is_single(a.brand_title) or not (a.video or "").strip():
            continue
        c = creators.get(a.chat_id)
        # Имя: сперва из строки отклика (там оно есть даже у заведённых руками),
        # иначе — из анкеты в боте.
        name = (a.name or "").strip() or ((getattr(c, "full_name", None) or "") if c else "")
        seen.setdefault(a.chat_id, name)
    return list(seen.items())


async def ensure_pipeline(chat_id: int, full_name: str | None = None, telegram: str | None = None) -> SinglePipeline:
    """Строка пайплайна для креатора. Часть участников 1-й волны заводилась вручную в
    таблице — записи в БД у них нет, поэтому создаём при первом касании 2-й волны."""
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p is None:
            p = SinglePipeline(chat_id=chat_id, full_name=full_name, telegram=telegram,
                               stage="submitted", updated_at=datetime.utcnow())
            s.add(p)
            await s.commit()
            await s.refresh(p)
        return p


async def set_wave2(chat_id: int, status: str, dl: date | None = None) -> None:
    await ensure_pipeline(chat_id)
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p:
            p.wave2_status = status
            if status == "go" and dl is None:
                p.wave2_go_at = datetime.utcnow()  # точка отсчёта пинков «напиши дату»
                p.wave2_nudges = 0
            if dl is not None:
                p.wave2_deadline = dl
                p.wave2_reminded = False
            p.updated_at = datetime.utcnow()
            await s.commit()


async def decline_pack(chat_id: int) -> bool:
    """Отказ от ДОНАБОРА (UGC-пак). Активную 1-ю волну не трогаем!

    Раньше здесь звали mark_dropped, и человек с уже взятым сроком по «Синглу»
    вылетал из напоминаний, отказавшись всего лишь от бонусного пака
    (так потеряли дожим Василисы Некрасовой 31.07). → True, если сняли с пайплайна.
    """
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p and p.stage == "producing" and p.deadline and not p.links:
            return False  # у него живой дедлайн по основной волне — дожимаем дальше
        if p:
            p.stage = "dropped"
            p.updated_at = datetime.utcnow()
            await s.commit()
        return True


async def mark_dropped(chat_id: int) -> None:
    """1-я волна: «не буду участвовать» — снимаем с дожима (луп шлёт только producing)."""
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p:
            p.stage = "dropped"
            p.updated_at = datetime.utcnow()
            await s.commit()


# ── Парсер даты ───────────────────────────────────────────────────────────────
def parse_deadline(text: str) -> date | None:
    """ДД.ММ или ДД.ММ.ГГГГ (разделители . / -). Без года — текущий; если дата уже
    прошла — переносим на следующий год. Возвращает date или None."""
    m = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", (text or "").strip())
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    today = (datetime.utcnow() + MSK).date()
    y = m.group(3)
    if y:
        year = int(y)
        if year < 100:
            year += 2000
    else:
        year = today.year
    try:
        dl = date(year, mo, d)
    except ValueError:
        return None
    if not y and dl < today:  # без года и уже прошла — значит следующий год
        try:
            dl = date(year + 1, mo, d)
        except ValueError:
            return None
    return dl


# ── Переходы в БД ─────────────────────────────────────────────────────────────
async def get_pipeline(chat_id: int) -> SinglePipeline | None:
    async with get_session() as s:
        return (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()


async def start_offer(chat_id: int, full_name: str | None, telegram: str | None) -> bool:
    """Завести/обновить пайплайн на этап offered. True — если это НОВЫЙ оффер (надо слать)."""
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        now = datetime.utcnow()
        if p is None:
            s.add(SinglePipeline(chat_id=chat_id, full_name=full_name, telegram=telegram,
                                 stage="offered", offered_at=now, updated_at=now))
            await s.commit()
            return True
        # Уже в пайплайне — повторный «оффер» не сбрасывает продвинутые этапы.
        if p.stage == "offered":
            return False  # уже слали оффер
        return False


async def mark_accepted(chat_id: int) -> None:
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p and p.stage in ("offered",):
            p.stage = "accepted"
            p.accepted_at = datetime.utcnow()
            p.updated_at = datetime.utcnow()
            await s.commit()


async def set_deadline(chat_id: int, dl: date, raw: str) -> None:
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p:
            p.stage = "producing"
            p.deadline = dl
            p.deadline_raw = raw
            p.reminded_before = False
            p.reminded_due = False
            p.updated_at = datetime.utcnow()
            await s.commit()


async def submit_links(chat_id: int, links: str) -> None:
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p:
            p.stage = "submitted"
            p.links = links
            p.submitted_at = datetime.utcnow()
            p.updated_at = datetime.utcnow()
            await s.commit()


def parse_payment(text: str) -> tuple[str, str] | None:
    """Из «89991234567 Сбербанк» → (телефон, банк). Телефон обязателен (иначе None),
    банк — остаток строки (может быть пустым)."""
    t = (text or "").strip()
    m = re.search(r"(\+?\d[\d\s\-()]{8,}\d)", t)
    if not m:
        return None
    phone = re.sub(r"\D", "", m.group(1))
    if len(phone) < 10:
        return None
    bank = (t[: m.start()] + " " + t[m.end():]).strip(" ,;–-\n")
    return phone, bank


async def save_payment(chat_id: int, phone: str, bank: str) -> None:
    async with get_session() as s:
        p = (await s.execute(select(SinglePipeline).where(SinglePipeline.chat_id == chat_id))).scalar_one_or_none()
        if p:
            p.payment_phone = phone
            p.payment_bank = bank
            p.payment_at = datetime.utcnow()
            p.updated_at = datetime.utcnow()
            await s.commit()


# ── Воронка ───────────────────────────────────────────────────────────────────
async def funnel() -> dict[str, int]:
    """Воронка «Сингл» из ЕДИНОГО источника — листа «Отклики» (через кэш). Учитывает и
    бот-пайплайн (зеркалит Подтвердил/Ссылку), и перенесённые вручную данные. Агрегируем
    по chat_id (человек = один участник, даже если есть отклики под обоими названиями)."""
    async with get_session() as s:
        apps = (await s.execute(select(CachedApplication))).scalars().all()
    by_chat: dict[int, dict] = {}
    for a in apps:
        if not is_single(a.brand_title):
            continue
        d = by_chat.setdefault(a.chat_id, {"offer": False, "confirmed": False, "video": False})
        if (a.status or "").strip().lower() == "оффер":
            d["offer"] = True
        if (a.confirmed or "").strip().lower() == "да":
            d["confirmed"] = True
        if (a.video or "").strip():
            d["video"] = True
    v = list(by_chat.values())

    # Просрочки и охваты берём из пайплайна и трекинга охватов (единый проект «Сингл»).
    today_msk = (datetime.utcnow() + MSK).date()
    async with get_session() as s:
        pipelines = (await s.execute(select(SinglePipeline))).scalars().all()
        reach = (await s.execute(select(ReachRow).where(ReachRow.active.is_(True)))).scalars().all()
    # Просрочка: срок задан, прошёл, а ролик ещё не сдан (stage != submitted).
    overdue = sum(
        1 for p in pipelines
        if p.deadline and p.deadline < today_msk and p.stage != "submitted"
    )
    reach_vals = [r.views for r in reach if r.views is not None]
    return {
        "otkliki": len(v),
        "offers": sum(1 for d in v if d["offer"]),
        "accepted": sum(1 for d in v if d["confirmed"]),
        "producing": sum(1 for d in v if d["confirmed"] and not d["video"]),
        "submitted": sum(1 for d in v if d["video"]),
        "overdue": overdue,
        "reach_total": sum(reach_vals),
        "reach_rows": len(reach),           # всего роликов в трекинге
        "reach_measured": len(reach_vals),  # по скольким есть цифра охвата
        "reach_10k": sum(1 for x in reach_vals if x >= 10000),
    }


_ADD_LINK_BTN = ("➕ Добавить ещё ссылку", "single:add_link")


def _stage_card(p: SinglePipeline):
    """(текст, [кнопки]) текущего этапа «Сингл». Шаг восстанавливается из БД."""
    head = "🎵 <b>Проект «Сингл» (ИИ-треки от ЗВУК)</b>\n\n"
    if p.stage == "offered":
        return head + "✅ Тебя одобрили! Осталось подтвердить участие.", [("✅ Да, участвую", "single:accept")]
    if p.stage == "accepted":
        return head + "📅 Ты подтвердил участие. Осталось указать срок ролика.", [("📅 Указать срок", "single:resume_deadline")]
    if p.stage == "producing":
        dl = p.deadline.strftime("%d.%m") if p.deadline else "—"
        return (
            head + f"🎬 Ты делаешь ролик. Срок: <b>{dl}</b>.\nКак выложишь в соцсети — пришли ссылки на посты.",
            [("📹 Отправить ссылки на ролик", "single:submit"), _ADD_LINK_BTN],
        )
    if p.stage == "submitted":
        if p.payment_at is None:
            return head + "✅ Ролик сдан! Осталось прислать реквизиты для оплаты.\nВыложил ещё — жми «Добавить ссылку».", [("💳 Отправить реквизиты", "single:resume_payment"), _ADD_LINK_BTN]
        return head + "🎉 Ты в проекте, ролик сдан. Выложил ещё (в другую соцсеть/новый ролик)? Добавь ссылку — учтём охваты.", [_ADD_LINK_BTN]
    return head + "Ты в проекте «Сингл».", [_ADD_LINK_BTN]


async def _participates_single(chat_id: int) -> bool:
    """Участвует ли в Сингле по отклику (confirmed=да) — даже без записи пайплайна."""
    from sqlalchemy import select

    from bot.database import get_session
    from bot.models import CachedApplication

    async with get_session() as s:
        apps = (await s.execute(select(CachedApplication).where(CachedApplication.chat_id == chat_id))).scalars().all()
    return any(is_single(a.brand_title) and (a.confirmed or "").strip().lower() == "да" for a in apps)


async def my_projects_view(chat_id: int, is_admin: bool = False):
    """(текст, клавиатура) экрана «Мои проекты». Показываем «Сингл» всем участникам
    (пайплайн ИЛИ подтверждённый отклик), с кнопкой добавить ещё видео."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    p = await get_pipeline(chat_id)
    if p is not None:
        text, buttons = _stage_card(p)
    elif await _participates_single(chat_id):
        text = ("🎵 <b>Проект «Сингл» (ИИ-треки от ЗВУК)</b>\n\nТы в проекте. Выложил ролик "
                "(в любой соцсети)? Добавь ссылку — учтём охваты.")
        buttons = [_ADD_LINK_BTN]
    elif not is_admin:
        return ("📁 <b>Мои проекты</b>\n\nПока нет активных проектов. Загляни в «🎯 Запросы брендов» "
                "и откликнись — если будет оффер, проект появится здесь.", None)
    else:
        text, buttons = "📁 <b>Мои проекты</b>\n\nЛичных проектов нет.", []

    if is_admin:
        # Всё управление «Синглом» живёт здесь, в проекте — в админке только общее.
        text += ("\n\n———\n⚙️ <b>Управление проектом</b> (видно только админам)")
        buttons = list(buttons) + [
            ("🎬 Воронка «Сингл»", "admin:single_funnel"),
            ("🌊 2-я волна (сдавшим ролик)", "admin:wave2"),
            ("🎁 Донабор «UGC-пак»", "admin:pack"),
            ("➕ Добавить ролик за креатора", "admin:add_video"),
        ]
    rows = [[InlineKeyboardButton(text=t, callback_data=c)] for t, c in buttons]
    return text, (InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


def _pct(part: int, whole: int) -> str:
    """Конверсия part/whole в % (─ если делить не на что)."""
    return f"{round(part / whole * 100)}%" if whole else "—"


def _fmt_int(n: int) -> str:
    """1234567 → 1 234 567 (неразрывный пробел для читаемости в TG)."""
    return f"{n:,}".replace(",", " ")


def funnel_text(f: dict[str, int]) -> str:
    offers = f["offers"]
    accepted = f["accepted"]
    submitted = f["submitted"]
    lines = [
        "📊 <b>Воронка «Сингл»</b>\n",
        # После авто-оффера отклик == оффер, поэтому верхняя точка — «В проекте (оффер)».
        f"🎉 В проекте (оффер): <b>{offers}</b>",
        f"🤝 Приняли участие: <b>{accepted}</b> <i>({_pct(accepted, offers)} от офферов)</i>",
        f"🎬 Делают ролик: <b>{f['producing']}</b>"
        + (f" · ⏰ просрочка: <b>{f['overdue']}</b>" if f.get("overdue") else ""),
        f"✅ Сдали ролик: <b>{submitted}</b> <i>({_pct(submitted, accepted)} от принявших)</i>",
        f"\n📈 Дошли до ролика: <b>{_pct(submitted, offers)}</b> <i>(оффер → сдал)</i>",
    ]

    # Блок охватов (из трекинга роликов проекта).
    rows = f.get("reach_rows", 0)
    if rows:
        measured = f.get("reach_measured", 0)
        pending = rows - measured
        lines.append(
            f"\n👁 <b>Охваты роликов</b>\n"
            f"Суммарно: <b>{_fmt_int(f['reach_total'])}</b>\n"
            f"Роликов в трекинге: <b>{rows}</b>"
            + (f" <i>(ждём данные по {pending})</i>" if pending else "")
            + f"\n🔥 ≥10к: <b>{f['reach_10k']}</b>"
        )
    return "\n".join(lines)


# ── Фоновый луп: напоминания креатору + утренний отчёт админам ────────────────
async def _get_state(key: str) -> str:
    async with get_session() as s:
        r = (await s.execute(select(AppState).where(AppState.key == key))).scalar_one_or_none()
        return r.value if r else ""


async def _set_state(key: str, value: str) -> None:
    async with get_session() as s:
        r = (await s.execute(select(AppState).where(AppState.key == key))).scalar_one_or_none()
        if r is None:
            s.add(AppState(key=key, value=value))
        else:
            r.value = value
        await s.commit()


async def _remind_creators(bot: Bot) -> None:
    today = (datetime.utcnow() + MSK).date()
    async with get_session() as s:
        rows = (await s.execute(
            select(SinglePipeline).where(SinglePipeline.stage == "producing", SinglePipeline.deadline.is_not(None))
        )).scalars().all()
        for p in rows:
            try:
                if p.deadline == today + timedelta(days=1) and not p.reminded_before:
                    await bot.send_message(
                        p.chat_id,
                        f"🔔 Напоминание: завтра ({p.deadline.strftime('%d.%m')}) дедлайн по ролику для «Сингл». "
                        "Успеваешь? Как выложишь — жми «📹 Отправить ссылки на ролик».",
                        reply_markup=deadline_push_keyboard(1),
                    )
                    p.reminded_before = True
                elif p.deadline <= today and not p.reminded_due:
                    await bot.send_message(
                        p.chat_id,
                        f"⏰ Сегодня ({p.deadline.strftime('%d.%m')}) срок по ролику «Сингл». Ждём ссылки на посты 🙏",
                        reply_markup=deadline_push_keyboard(1),
                    )
                    p.reminded_due = True
            except Exception as e:  # noqa: BLE001
                logger.info("single remind %s failed: %s", p.chat_id, e)
        await s.commit()


# ── Донабор по всей базе: UGC-пак за ролик, сданный в срок ────────────────────
PACK_DEADLINE = date(2026, 8, 3)  # понедельник
PACK_COLUMN = "Донабор 3.08"      # колонка ответов в листе креаторов (создаётся в конце)
SINGLE_BRAND_TITLE = "Сингл (Сервис создания ИИ-треков от СберЗВУК) - охватная кампания"


def pack_text(name: str | None) -> str:
    first = (name or "").strip().split()[0] if (name or "").strip() else ""
    hello = f"Привет, {first}!" if first else "Привет!"
    return (
        f"{hello} Я по проекту «Сингл» от ЗВУКа.\n\n"
        "У нас немного не хватает до плана по охватам, и без тебя никак.\n\n"
        "🎁 Мы собрали <b>UGC-пак: 30+ материалов и сервисов</b> для съёмки — нейронки, монтаж, "
        "тренды, шаблоны. С <b>бесплатным доступом</b>. В открытом виде этого нет нигде.\n\n"
        "<b>Как получить:</b> сдать ролик по «Синглу» в срок. Всё.\n\n"
        "⏰ Дедлайн — <b>понедельник, 3 августа</b>. Ролик снимается за вечер, так что успеть "
        "реально — но тянуть некуда.\n\n"
        "💰 <b>По деньгам:</b> гарант <b>1 000 ₽</b> за ролик (если 800+ подписчиков и охваты "
        "от 4 000 на рилс) плюс бонусы за виральность — <b>до +10 000 ₽</b>. Профиль поменьше — "
        f'платим за охват, от 500 ₽.\n<a href="{_LINK_CONDITIONS}">Условия целиком →</a>\n\n'
        f"🤖 И сверху: <b>бесплатный доступ к {WAVE2_BOT}</b> — он технически уникализирует "
        "ролики, чтобы их можно было безопасно перевыкладывать и собирать охваты. Пригодится "
        "и под свои задачи.\n\n"
        "Погнали?"
    )


PACK_GO_TEXT = (
    "🚀 Отлично, ты в деле!\n\n"
    f'📄 <a href="{_LINK_FORMATS}">Форматы и идеи сценариев</a>\n'
    f'📋 <a href="{_LINK_CONDITIONS}">Условия участия</a>\n\n'
    f"⏰ Срок — <b>{PACK_DEADLINE.strftime('%d.%m')}</b> (понедельник). Напомню за день.\n\n"
    "<b>Как сдавать:</b>\n"
    '1️⃣ Готовый ролик — на утверждение в <a href="https://t.me/packman_hr">@packman_hr</a>\n'
    "2️⃣ После утверждения выложи в свои соцсети — без призывов и ссылок, с хештегом "
    "<code>#синглзвук</code>\n"
    "3️⃣ Пришли СЮДА ссылки на посты — по кнопке ниже\n\n"
    "🎁 Сдашь в срок — открою UGC-пак и доступ к боту-уникализатору."
)
PACK_NO_TEXT = "Понял, спасибо за ответ 🙏 Если передумаешь — загляни в «🎯 Запросы брендов»."


async def pack_audience() -> list[tuple[int, str]]:
    """Кому слать донабор: ВСЯ база бота, кроме тех, кто уже сдал ролик (они во 2-й волне).
    → [(chat_id, имя)]. Имя берём из строки отклика, иначе из анкеты."""
    from bot.models import Creator

    async with get_session() as s:
        creators = (await s.execute(select(Creator))).scalars().all()
        apps = (await s.execute(select(CachedApplication))).scalars().all()
    done = {a.chat_id for a in apps if is_single(a.brand_title) and (a.video or "").strip()}
    from_sheet = {a.chat_id: (a.name or "").strip() for a in apps if (a.name or "").strip()}
    return [
        (c.tg_id, from_sheet.get(c.tg_id) or (c.full_name or ""))
        for c in creators if c.tg_id not in done
    ]


# Пинки тем, кто нажал «Погнали», но дату так и не написал: через час, потом ещё через 6.
WAVE2_NUDGE_HOURS = (1, 6)
WAVE2_NUDGE_COUNT = len(WAVE2_NUDGE_HOURS)


def wave2_nudge_text(n: int, name: str | None) -> str:
    if n == 0:
        return (
            f"🙌 Ты {_g(name, 'сказал')} «погнали» по 2-й волне «Сингла» — не хватает только даты.\n\n"
            "Напиши, пожалуйста, когда опубликуешь новые посты, в формате <b>ДД.ММ</b> (например, 05.08)."
        )
    return (
        "📅 Всё ещё жду дату по 2-й волне «Сингла» — без неё не могу поставить тебя в план.\n\n"
        f"Напиши в формате <b>ДД.ММ</b> (например, 05.08). Если {_g(name, 'передумал')} — тоже скажи, это ок."
    )


async def _nudge_wave2(bot: Bot) -> None:
    """Нажал «Погнали», но дату не прислал → пинок через 1 час, затем ещё через 6 часов."""
    now = datetime.utcnow()
    async with get_session() as s:
        rows = (await s.execute(
            select(SinglePipeline).where(
                SinglePipeline.wave2_status == "go",
                SinglePipeline.wave2_deadline.is_(None),
                SinglePipeline.wave2_go_at.is_not(None),
            )
        )).scalars().all()
        for p in rows:
            n = p.wave2_nudges or 0
            if n >= WAVE2_NUDGE_COUNT:
                continue
            # Отсчёт от «Погнали» для первого пинка, от предыдущего пинка — для второго.
            due = p.wave2_go_at + timedelta(hours=sum(WAVE2_NUDGE_HOURS[: n + 1]))
            if now < due:
                continue
            try:
                await bot.send_message(p.chat_id, wave2_nudge_text(n, p.full_name),
                                       reply_markup=wave2_date_keyboard())
                p.wave2_nudges = n + 1
            except Exception as e:  # noqa: BLE001
                logger.info("wave2 nudge %s failed: %s", p.chat_id, e)
        await s.commit()


async def _remind_wave2(bot: Bot) -> None:
    """2-я волна: напоминание за день до заявленной даты публикации."""
    today = (datetime.utcnow() + MSK).date()
    async with get_session() as s:
        rows = (await s.execute(
            select(SinglePipeline).where(
                SinglePipeline.wave2_status == "go",
                SinglePipeline.wave2_deadline == today + timedelta(days=1),
                SinglePipeline.wave2_reminded.is_(False),
            )
        )).scalars().all()
        for p in rows:
            try:
                await bot.send_message(
                    p.chat_id,
                    f"🔔 Напоминание: завтра ({p.wave2_deadline.strftime('%d.%m')}) ты обещал(а) выложить "
                    "новые посты по «Синглу» — перезалив старого ролика и новый.\n\n"
                    f"Уникализировать ролики можно в {WAVE2_BOT}. Всё в силе?",
                    reply_markup=deadline_push_keyboard(2),
                )
                p.wave2_reminded = True
            except Exception as e:  # noqa: BLE001
                logger.info("wave2 remind %s failed: %s", p.chat_id, e)
        await s.commit()


async def _daily_report(bot: Bot) -> None:
    now_msk = datetime.utcnow() + MSK
    if now_msk.hour < 10:
        return
    today_str = now_msk.date().isoformat()
    if await _get_state("single_report_date") == today_str:
        return
    today = now_msk.date()
    async with get_session() as s:
        pipe = (await s.execute(select(SinglePipeline))).scalars().all()
    overdue = [p for p in pipe if p.stage == "producing" and p.deadline and p.deadline < today]
    due_today = [p for p in pipe if p.stage == "producing" and p.deadline == today]
    no_deadline = [p for p in pipe if p.stage == "accepted"]

    def who(p):
        return f"{p.full_name or '—'} ({p.telegram or '—'})"

    f = await funnel()
    parts = [f"☀️ <b>Отчёт по «Сингл»</b> · {today.strftime('%d.%m')}\n", funnel_text(f)]
    if overdue:
        parts.append("\n⏰ <b>Просрочки:</b>\n" + "\n".join(f"• {who(p)} — было {p.deadline.strftime('%d.%m')}" for p in overdue))
    if due_today:
        parts.append("\n📅 <b>Дедлайн сегодня:</b>\n" + "\n".join(f"• {who(p)}" for p in due_today))
    if no_deadline:
        parts.append("\n🔔 <b>Приняли оффер, но не задали срок:</b>\n" + "\n".join(f"• {who(p)}" for p in no_deadline))
    report = "\n".join(parts)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, report)
        except Exception as e:  # noqa: BLE001
            logger.warning("single report to %s failed: %s", admin_id, e)
    await _set_state("single_report_date", today_str)


async def run_single_loop(bot: Bot, interval: int = 600) -> None:
    await asyncio.sleep(90)
    while True:
        try:
            await _remind_creators(bot)
            await _nudge_wave2(bot)
            await _remind_wave2(bot)
            await _daily_report(bot)
        except Exception as e:  # noqa: BLE001
            logger.warning("single loop error: %s", e)
        await asyncio.sleep(interval)
