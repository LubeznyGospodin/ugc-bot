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
from bot.keyboards import single_accept_keyboard, single_submit_keyboard
from bot.models import AppState, CachedApplication, SinglePipeline

logger = logging.getLogger(__name__)

SINGLE_BRAND_ID = "br1"
_LINK_FORMATS = "https://docs.google.com/document/d/1oZcNVVlJsKL2g8sbTOQ70YT-epSWQKZsuFJMe_p3Rpg/edit?tab=t.0"
_LINK_CONDITIONS = "https://docs.google.com/document/d/1vlNOuAwyPzstk_F6RzeJagkpzw9DTsJq9_4JUhB4vDs/edit"

MSK = timedelta(hours=3)  # сервер в UTC; для «утра по Москве»


def is_single(brand_title: str | None) -> bool:
    """Относится ли отклик к проекту «Сингл» (единственный бренд со словом «сингл»)."""
    return "сингл" in (brand_title or "").lower()


# ── Тексты ────────────────────────────────────────────────────────────────────
OFFER_TEXT = (
    "🎉 <b>Тебя одобрили на проект «Сингл» (ИИ-треки от ЗВУК)!</b>\n\n"
    "Освежи в памяти перед стартом:\n"
    f'📄 <a href="{_LINK_FORMATS}">Форматы и идеи сценариев</a>\n'
    f'📋 <a href="{_LINK_CONDITIONS}">Условия участия</a>\n\n'
    "Готов(а) участвовать? Жми кнопку 👇"
)
ASK_DEADLINE_TEXT = (
    "Огонь! 🔥 К какой дате сделаешь ролик?\n\n"
    "Напиши дату в формате <b>ДД.ММ</b> (например, 20.07).\n\n"
    "📌 Важно: нам нужно сделать план по охватам до <b>15.08</b> — поэтому чем раньше "
    "скинешь ролик, тем лучше!"
)
BAD_DEADLINE_TEXT = "Не понял дату 🙈 Напиши в формате <b>ДД.ММ</b>, например 20.07."


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
        "3️⃣ Пришли СЮДА ссылки на посты — по кнопке ниже. Тогда цикл завершён."
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
PAYMENT_SAVED_TEXT = "Принято, спасибо! Передали реквизиты в оплату 🙌 Цикл завершён."
BAD_PAYMENT_TEXT = (
    "Не увидел номер телефона 🙈 Пришли, пожалуйста, номер телефона и банк одним сообщением, "
    "например: <code>89991234567 Сбербанк</code>."
)


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
    async with get_session() as s:
        apps = (await s.execute(select(CachedApplication))).scalars().all()
        pipe = (await s.execute(select(SinglePipeline))).scalars().all()
    single_apps = [a for a in apps if is_single(a.brand_title)]
    return {
        "otkliki": len(single_apps),
        "offers": sum(1 for a in single_apps if (a.status or "").strip().lower() == "оффер"),
        "accepted": sum(1 for p in pipe if p.stage in ("accepted", "producing", "submitted")),
        "producing": sum(1 for p in pipe if p.stage == "producing"),
        "submitted": sum(1 for p in pipe if p.stage == "submitted"),
    }


def funnel_text(f: dict[str, int]) -> str:
    return (
        "📊 <b>Воронка «Сингл»</b>\n\n"
        f"📨 Откликов: <b>{f['otkliki']}</b>\n"
        f"🎉 Офферов: <b>{f['offers']}</b>\n"
        f"🤝 Приняли оффер: <b>{f['accepted']}</b>\n"
        f"🎬 Делают ролик: <b>{f['producing']}</b>\n"
        f"✅ Сделали ролик: <b>{f['submitted']}</b>"
    )


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
                        reply_markup=single_submit_keyboard(),
                    )
                    p.reminded_before = True
                elif p.deadline <= today and not p.reminded_due:
                    await bot.send_message(
                        p.chat_id,
                        f"⏰ Сегодня ({p.deadline.strftime('%d.%m')}) срок по ролику «Сингл». Ждём ссылки на посты 🙏",
                        reply_markup=single_submit_keyboard(),
                    )
                    p.reminded_due = True
            except Exception as e:  # noqa: BLE001
                logger.info("single remind %s failed: %s", p.chat_id, e)
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
            await _daily_report(bot)
        except Exception as e:  # noqa: BLE001
            logger.warning("single loop error: %s", e)
        await asyncio.sleep(interval)
