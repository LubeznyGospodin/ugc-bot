"""
Админ-панель (критерий приёмки #3): аналитика (кол-во креаторов) + рассылка.
Доступ — только settings.admin_ids. Рассылка идёт по chat_id, известным
локальной SQLite (кто хоть раз нажимал /start у этого бота) — рассылать
по всей гугл-таблице бессмысленно, т.к. там половина строк — люди, которых
никогда не было в боте (chat_id пуст).
"""
from __future__ import annotations

import asyncio
import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import select

from bot.config import settings
from bot.database import get_session
from bot.keyboards import (
    BTN_ADMIN,
    admin_menu_keyboard,
    broadcast_confirm_keyboard,
    nudge_backlog_confirm_keyboard,
)
from bot.models import Creator
from bot.sheets import SheetsError, sheets_client
from bot.states import AddVideoFSM, BroadcastFSM
from bot.utils.chat_cleanup import render_screen
from bot.single import wave2_audience
from bot.utils.db_helpers import count_creators, funnel_stats
from bot.utils.export import export_all_xlsx

logger = logging.getLogger(__name__)
router = Router(name="admin")


def _admin_only(user_id: int) -> bool:
    return settings.is_admin(user_id)


def _message_html(message: Message) -> str:
    """Текст сообщения c разметкой (жирный/курсив/ссылки) как HTML — чтобы форматирование
    доехало до креаторов (рассылка идёт с parse_mode=HTML). Работает и для подписи к фото."""
    if message.text is not None:
        return message.html_text
    if message.caption is not None:
        from aiogram.utils.text_decorations import html_decoration

        return html_decoration.unparse(message.caption, message.caption_entities or [])
    return ""


@router.message(Command("admin"))
@router.message(F.text == BTN_ADMIN)
async def admin_menu(message: Message, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    await render_screen(bot, message.chat.id, "⚙️ Админ-панель", reply_markup=admin_menu_keyboard(), delete_trigger=message)


@router.callback_query(F.data == "admin:stats")
async def admin_stats(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()

    # Воронка CJM из БД — мгновенно, без похода в таблицу.
    s = await funnel_stats()
    text = (
        "📊 <b>Аналитика (воронка CJM)</b>\n\n"
        f"👋 Всего уникальных заходов: <b>{s['visits']}</b>\n"
        f"📝 Регистраций: <b>{s['registrations']}</b>\n"
        f"🙋 Уникальных откликнувшихся: <b>{s['unique_applicants']}</b>\n\n"
        f"📨 Всего откликов: <b>{s['apps']}</b>\n"
        f"• 🎉 Офферов: {s['offers']}\n"
        f"• ❌ Отказов: {s['rejects']}\n"
        f"• 🕐 На рассмотрении: {s['pending']}"
    )
    await render_screen(bot, call.message.chat.id, text, reply_markup=admin_menu_keyboard())


@router.callback_query(F.data == "admin:sources")
async def admin_sources(call: CallbackQuery, bot: Bot):
    """Разбивка заходов по deep-link меткам (?start=МЕТКА): переходы + конверсия в
    регистрацию. Считаем только с момента внедрения фичи (прошлые клики не логировались)."""
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()

    from bot.utils.db_helpers import source_stats

    rows = await source_stats()
    labeled = [r for r in rows if r["source"] is not None]

    # Метки вида ref<tg_id> — это реферальные ссылки креаторов. Расшифровываем в имена,
    # чтобы видеть, кто сколько привёл (влияет на приоритет на проектах).
    ref_ids = []
    for r in labeled:
        m = re.fullmatch(r"ref(\d+)", str(r["source"]))
        if m:
            ref_ids.append(int(m.group(1)))
    names: dict[int, str] = {}
    if ref_ids:
        async with get_session() as session:
            found = (await session.execute(select(Creator).where(Creator.tg_id.in_(ref_ids)))).scalars().all()
            names = {c.tg_id: (c.full_name or c.telegram_contact or str(c.tg_id)) for c in found}

    def _label(src: str) -> str:
        m = re.fullmatch(r"ref(\d+)", str(src))
        if m:
            who = names.get(int(m.group(1)), f"id {m.group(1)}")
            return f"👤 {who}"
        return f"<code>{src}</code>"

    lines = ["🔗 <b>Источники переходов</b> (<code>?start=метка</code>)\n"]
    if not labeled:
        lines.append("Пока ни одного захода с меткой.\n")
    else:
        lines.append("<b>Метка · заходы · регистрации (конверсия)</b>")
        for r in labeled:
            conv = f"{round(r['registered'] / r['visits'] * 100)}%" if r["visits"] else "—"
            lines.append(f"• {_label(r['source'])} — {r['visits']} → {r['registered']} ({conv})")
    no_label = next((r for r in rows if r["source"] is None), None)
    if no_label:
        lines.append(f"\n<i>Без метки: {no_label['visits']} заходов "
                     f"(рег.: {no_label['registered']})</i>")
    lines.append(
        "\n💡 Ссылка с меткой: <code>t.me/ugc_radarbot?start=МЕТКА</code>\n"
        "Учёт ведётся с момента запуска фичи (прошлые переходы Telegram не хранит)."
    )
    await render_screen(bot, call.message.chat.id, "\n".join(lines), reply_markup=admin_menu_keyboard())


@router.callback_query(F.data == "admin:broadcast")
async def admin_broadcast_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()
    await state.set_state(BroadcastFSM.waiting_text)
    await render_screen(bot, call.message.chat.id, "Пришлите текст рассылки одним сообщением.")


@router.message(BroadcastFSM.waiting_text)
async def admin_broadcast_text(message: Message, state: FSMContext, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    bc_html = _message_html(message)
    await state.update_data(broadcast_text=bc_html)
    count = await count_creators()
    await state.set_state(BroadcastFSM.waiting_confirm)
    await render_screen(
        bot,
        message.chat.id,
        f"Отправить это сообщение {count} креаторам?\n\n---\n{bc_html}\n---",
        reply_markup=broadcast_confirm_keyboard(),
        delete_trigger=message,
    )


@router.callback_query(BroadcastFSM.waiting_confirm, F.data == "admin:broadcast_cancel")
async def admin_broadcast_cancel(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer("Отменено")
    await state.clear()
    await render_screen(bot, call.message.chat.id, "⚙️ Админ-панель", reply_markup=admin_menu_keyboard())


@router.callback_query(BroadcastFSM.waiting_confirm, F.data == "admin:broadcast_send")
async def admin_broadcast_send(call: CallbackQuery, state: FSMContext, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    data = await state.get_data()
    text = data.get("broadcast_text", "")
    await state.clear()
    await call.answer("Начинаю рассылку...")
    await render_screen(bot, call.message.chat.id, "📣 Рассылаю...")

    async with get_session() as session:
        result = await session.execute(select(Creator.tg_id))
        chat_ids = [row[0] for row in result.all()]

    sent, failed = 0, 0
    for chat_id in chat_ids:
        try:
            await bot.send_message(chat_id, text)
            sent += 1
        except Exception as e:
            logger.warning("broadcast to %s failed: %s", chat_id, e)
            failed += 1
        await asyncio.sleep(0.05)  # не упереться в лимиты Telegram (30 msg/sec)

    await render_screen(
        bot,
        call.message.chat.id,
        f"✅ Рассылка завершена.\nДоставлено: {sent}\nНе удалось: {failed}",
        reply_markup=admin_menu_keyboard(),
    )


# ── Рассылка 2-й волны «Сингл» ────────────────────────────────────────────────
@router.callback_query(F.data == "admin:wave2")
@router.message(Command("wave2"))
async def wave2_preview(event, bot: Bot):
    """Превью рассылки 2-й волны: кому уйдёт + текст. Отправка — по кнопке."""
    if not _admin_only(event.from_user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Недоступно", show_alert=True)
        return
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot.single import wave2_text

    aud = await wave2_audience()
    if not aud:
        await message.answer("Некому: нет откликов «Сингл» со ссылкой на ролик (колонка L).")
        return
    names = ", ".join(n or str(cid) for cid, n in aud)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Отправить ({len(aud)})", callback_data="admin:wave2_send"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:wave2_cancel"),
    ]])
    await message.answer(
        f"🌊 <b>2-я волна «Сингл»</b> — {len(aud)} чел.\n\n{names}\n\n"
        f"Текст (пример подстановки имени):\n———\n{wave2_text(aud[0][1])}\n———",
        reply_markup=kb, disable_web_page_preview=True,
    )


# ── Донабор по базе: UGC-пак за ролик к 3.08 ──────────────────────────────────
@router.message(Command("pack_test"))
async def pack_test(message: Message, bot: Bot):
    """Прогон донабора на себе — тот же текст и кнопки, что уйдут базе."""
    if not _admin_only(message.from_user.id):
        return
    from bot.keyboards import pack_offer_keyboard
    from bot.single import pack_text

    await bot.send_message(message.chat.id, pack_text(message.from_user.first_name or ""),
                           reply_markup=pack_offer_keyboard(), disable_web_page_preview=True)


@router.callback_query(F.data == "admin:pack")
@router.message(Command("pack"))
async def pack_preview(event, bot: Bot):
    """Превью донабора: сколько человек + текст. Отправка — по кнопке."""
    if not _admin_only(event.from_user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Недоступно", show_alert=True)
        return
    message = event.message if isinstance(event, CallbackQuery) else event
    if isinstance(event, CallbackQuery):
        await event.answer()
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    from bot.single import pack_audience, pack_text

    aud = await pack_audience()
    if not aud:
        await message.answer("Некому: база пуста.")
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"✅ Отправить ({len(aud)})", callback_data="admin:pack_send"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:pack_cancel"),
    ]])
    await message.answer(
        f"🎁 <b>Донабор «UGC-пак»</b> — {len(aud)} чел. (вся база, кроме сдавших ролик).\n\n"
        f"Текст (пример подстановки имени):\n———\n{pack_text(aud[0][1])}\n———",
        reply_markup=kb, disable_web_page_preview=True,
    )


@router.callback_query(F.data == "admin:pack_cancel")
async def pack_cancel(call: CallbackQuery):
    await call.answer("Отменено")
    await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "admin:pack_send")
async def pack_send(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    from bot.keyboards import pack_offer_keyboard
    from bot.single import pack_audience, pack_text

    await call.answer("Рассылаю...")
    await call.message.edit_reply_markup(reply_markup=None)
    sent, failed = 0, 0
    for chat_id, name in await pack_audience():
        try:
            await bot.send_message(chat_id, pack_text(name), reply_markup=pack_offer_keyboard(),
                                   disable_web_page_preview=True)
            sent += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("pack to %s failed: %s", chat_id, e)
            failed += 1
        await asyncio.sleep(0.05)  # лимит Telegram 30 msg/sec
    await bot.send_message(call.message.chat.id, f"✅ Донабор разослан.\nДоставлено: {sent}\nНе удалось: {failed}")


@router.message(Command("wave2_test"))
async def wave2_test(message: Message, bot: Bot):
    """Прогон 2-й волны на себе: тот же оффер и те же кнопки, что уйдут креаторам.
    Строки отклика у админа обычно нет — тогда дата в столбец Q не пишется, это ок."""
    if not _admin_only(message.from_user.id):
        return
    from bot.keyboards import wave2_offer_keyboard
    from bot.single import ensure_pipeline, set_wave2, wave2_text

    name = message.from_user.first_name or ""
    await ensure_pipeline(message.from_user.id, name)
    await bot.send_message(message.chat.id, wave2_text(name), reply_markup=wave2_offer_keyboard())
    await set_wave2(message.from_user.id, "sent")


@router.message(Command("wave2_nudge_now"))
async def wave2_nudge_now(message: Message, bot: Bot):
    """Тест пинков: отматывает твой «Погнали» на 7 часов назад и гоняет луп пинков сразу."""
    if not _admin_only(message.from_user.id):
        return
    from datetime import datetime, timedelta

    from bot.models import SinglePipeline
    from bot.single import _nudge_wave2

    async with get_session() as s:
        p = (await s.execute(
            select(SinglePipeline).where(SinglePipeline.chat_id == message.from_user.id)
        )).scalar_one_or_none()
        if p is None or p.wave2_status != "go" or p.wave2_deadline is not None:
            await message.answer("Сначала /wave2_test → «Погнали» и НЕ пиши дату.")
            return
        p.wave2_go_at = datetime.utcnow() - timedelta(hours=7)
        await s.commit()
    await _nudge_wave2(bot)
    await message.answer("Прогнал луп пинков (сдвинул время на -7ч). Повтори команду для второго пинка.")


@router.callback_query(F.data == "admin:wave2_cancel")
async def wave2_cancel(call: CallbackQuery):
    await call.answer("Отменено")
    await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "admin:wave2_send")
async def wave2_send(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    from bot.keyboards import wave2_offer_keyboard
    from bot.single import ensure_pipeline, set_wave2, wave2_text

    await call.answer("Рассылаю...")
    await call.message.edit_reply_markup(reply_markup=None)
    sent, failed = 0, 0
    for chat_id, name in await wave2_audience():
        try:
            await ensure_pipeline(chat_id, name)
            await bot.send_message(chat_id, wave2_text(name), reply_markup=wave2_offer_keyboard())
            await set_wave2(chat_id, "sent")
            sent += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("wave2 to %s failed: %s", chat_id, e)
            failed += 1
        await asyncio.sleep(0.05)  # лимит Telegram 30 msg/sec
    await bot.send_message(call.message.chat.id, f"✅ 2-я волна разослана.\nДоставлено: {sent}\nНе удалось: {failed}")


_COMMANDS_TEXT = (
    "📋 <b>Команды и разделы</b>\n\n"
    "<b>«🛠 Админка» — только общее по боту:</b>\n"
    "📊 Аналитика — воронка CJM\n"
    "🔗 Источники — переходы по <code>?start=метка</code>\n"
    "📣 Рассылка (текст) — сообщение всем креаторам\n"
    "📤 Экспорт (xlsx) — один файл, листы: Креаторы · Заходы · Не зарегались\n\n"
    "<b>«📁 Мои проекты» → Сингл — всё по проекту:</b>\n"
    "🎬 Воронка «Сингл» — этапы пайплайна (отклик→ролик)\n"
    "🌊 2-я волна — тем, кто уже сдал ролик\n"
    "🎁 Донабор «UGC-пак» — по всей базе\n"
    "➕ Добавить ролик за креатора\n\n"
    "<b>Команды (набрать вручную):</b>\n"
    "/admin — открыть админ-меню\n"
    "/wave2 · /pack — те же рассылки командой\n"
    "/wave2_test · /pack_test — прогон рассылки на себе\n"
    "/nudge_backlog — пуш-напоминание тем, кто зашёл, но не зарегался\n"
    "/photos_to_group — отправить сохранённые фото креаторов в рабочую группу\n"
    "/chatid — показать id текущего чата (для настройки группы)\n"
    "/export — выгрузить креаторов файлом\n\n"
    "<b>Хостинг:</b>\n"
    "/hosting — статус оплаты и мониторинга\n"
    "/hosting_due 15.08.2026 — задать дату оплаты\n"
    "/paid — отметить, что оплатил (перенос на след. месяц)"
)


@router.callback_query(F.data == "admin:single_funnel")
async def admin_single_funnel(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()
    from bot.single import funnel, funnel_text

    await bot.send_message(call.message.chat.id, funnel_text(await funnel()))


@router.callback_query(F.data == "admin:commands")
async def admin_commands(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer()
    await render_screen(bot, call.message.chat.id, _COMMANDS_TEXT, reply_markup=admin_menu_keyboard())


@router.message(Command("addvideo"))
@router.callback_query(F.data == "admin:add_video")
async def add_video_start(event, bot: Bot, state: FSMContext):
    """Ручное добавление ролика в трекинг охватов (Сингл): имя → ссылка."""
    uid = event.from_user.id
    chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
    if not _admin_only(uid):
        if isinstance(event, CallbackQuery):
            await event.answer("Недоступно", show_alert=True)
        return
    if isinstance(event, CallbackQuery):
        await event.answer()
    await state.set_state(AddVideoFSM.waiting_name)
    await render_screen(bot, chat_id, "➕ Добавление ролика.\n\nИмя и фамилия креатора?")


@router.message(AddVideoFSM.waiting_name)
async def add_video_name(message: Message, state: FSMContext, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    await state.update_data(av_name=(message.text or "").strip())
    await state.set_state(AddVideoFSM.waiting_link)
    await render_screen(bot, message.chat.id, "Ссылка(и) на ролик (можно несколько, с новой строки)?", delete_trigger=message)


@router.message(AddVideoFSM.waiting_link)
async def add_video_link(message: Message, state: FSMContext, bot: Bot):
    if not _admin_only(message.from_user.id):
        return
    text = (message.text or "").strip()
    if "http" not in text.lower():
        await render_screen(bot, message.chat.id, "Не вижу ссылку 🙈 Пришли URL (http…).", delete_trigger=message)
        return
    data = await state.get_data()
    name = data.get("av_name", "")
    await state.clear()
    from bot.reach import add_reach_link, extract_urls

    urls = extract_urls(text)
    added = 0
    for u in urls:
        new, _ = await add_reach_link(u, name)
        added += 1 if new else 0
    if added:  # ссылки в таблицу сразу (без парсинга охватов — юниты не тратим)
        from bot.handlers.single import _push_links_to_sheet

        _push_links_to_sheet()
    from bot.handlers.single import _added_text

    await render_screen(
        bot, message.chat.id,
        f"{_added_text(added, len(urls))}\n\nКреатор: <b>{name}</b>",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(Command("reach"))
async def reach_cmd(message: Message, bot: Bot):
    """Ручной сбор охватов + запись клиентской таблицы (обычно раз в сутки авто)."""
    if not _admin_only(message.from_user.id):
        return
    await render_screen(bot, message.chat.id, "📊 Собираю охваты по всем роликам…", delete_trigger=message)
    from bot.reach import reach_run

    res = await reach_run(bot)
    if res.get("ok"):
        await render_screen(
            bot, message.chat.id,
            (f"✅ Готово.\nСсылок: {res['links']}\nСуммарный охват: {res['total']:,}\n"
             f"Заморожено (финал): {res.get('frozen', 0)}\nНе спарсилось: {res['failed']}").replace(",", " "),
            reply_markup=admin_menu_keyboard(),
        )
    else:
        await render_screen(bot, message.chat.id, f"❌ {res.get('error')}", reply_markup=admin_menu_keyboard())


@router.message(Command("hosting_due"))
async def hosting_due_cmd(message: Message, bot: Bot):
    """Задать дату оплаты хостинга: /hosting_due 15.08.2026 (или 15.08)."""
    if not _admin_only(message.from_user.id):
        return
    from bot.ops import set_hosting_due
    from bot.single import parse_deadline

    arg = (message.text or "").split(maxsplit=1)
    dl = parse_deadline(arg[1]) if len(arg) > 1 else None
    if dl is None:
        await message.reply("Формат: <code>/hosting_due 15.08.2026</code> (или 15.08)")
        return
    await set_hosting_due(dl)
    await message.reply(
        f"✅ Дата оплаты хостинга: <b>{dl.strftime('%d.%m.%Y')}</b>.\n"
        "Напомню за 3 дня и в день оплаты. Когда оплатишь — /paid."
    )


@router.message(Command("paid"))
async def hosting_paid_cmd(message: Message, bot: Bot):
    """Отметить оплату хостинга — переносит срок на следующий месяц."""
    if not _admin_only(message.from_user.id):
        return
    from bot.ops import mark_paid

    nxt = await mark_paid()
    if nxt is None:
        await message.reply("Дата оплаты не задана. Сначала: <code>/hosting_due 15.08.2026</code>")
        return
    await message.reply(f"👍 Отметил оплату. Следующая дата: <b>{nxt.strftime('%d.%m.%Y')}</b>.")


@router.message(Command("hosting"))
async def hosting_status_cmd(message: Message, bot: Bot):
    """Показать статус оплаты хостинга + мониторинга."""
    if not _admin_only(message.from_user.id):
        return
    from bot.ops import _today_msk, get_hosting_status

    st = await get_hosting_status()
    if not st["due"]:
        await message.reply("Дата оплаты хостинга не задана. Задай: <code>/hosting_due 15.08.2026</code>")
        return
    days = (st["due"] - _today_msk()).days
    when = f"через {days} дн." if days > 0 else ("сегодня" if days == 0 else f"просрочено на {-days} дн.")
    mon = "✅ подключён" if settings.heartbeat_url else "❌ не подключён (нужен внешний монитор)"
    await message.reply(
        f"🖥 <b>Хостинг</b>\n"
        f"Оплата: <b>{st['due'].strftime('%d.%m.%Y')}</b> ({when})\n"
        f"Мониторинг падений: {mon}"
    )


@router.message(Command("chatid"))
async def chat_id_cmd(message: Message, bot: Bot):
    """Показать id текущего чата — так узнаём id группы для PHOTOS_CHAT_ID.
    Работает и в группе: добавь бота в группу и отправь там /chatid."""
    if not _admin_only(message.from_user.id):
        return
    thread = message.message_thread_id
    txt = (
        f"🆔 id чата: <code>{message.chat.id}</code>\n"
        f"тип: {message.chat.type}\n"
        f"название: {message.chat.title or '—'}"
    )
    if thread:
        txt += f"\n🧵 id темы: <code>{thread}</code>"
    else:
        txt += "\n🧵 темы нет (или это не форум). Для id темы отправь /chatid ВНУТРИ нужной темы."
    await message.reply(txt)


@router.message(Command("photos_to_group"))
async def photos_to_group(message: Message, bot: Bot):
    """Отправить в рабочую группу все сохранённые фото креаторов, которые туда ещё не
    отправляли. Работает только для фото, присланных ПОСЛЕ появления сохранения file_id."""
    if not _admin_only(message.from_user.id):
        return
    if not settings.photos_chat_id:
        await render_screen(
            bot, message.chat.id,
            "⚠️ Группа не задана. Добавь бота в группу, отправь там /chatid "
            "и пришли мне id — пропишу в PHOTOS_CHAT_ID.",
            delete_trigger=message,
        )
        return
    from bot.utils.photo_sender import send_stored_photos

    await render_screen(bot, message.chat.id, "📤 Отправляю фото в группу…", delete_trigger=message)
    creators, photos, failed = await send_stored_photos(bot, settings.photos_chat_id)
    await render_screen(
        bot, message.chat.id,
        f"✅ Готово.\nКреаторов: {creators}\nФото отправлено: {photos}\nНе удалось: {failed}",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(Command("nudge_backlog"))
async def nudge_backlog_start(message: Message, bot: Bot):
    """Ручная разовая рассылка пуша-напоминания по бэклогу (зашли до запуска фичи,
    но не зарегистрировались). Показываем число и просим подтвердить."""
    if not _admin_only(message.from_user.id):
        return
    from bot.utils.db_helpers import backlog_unregistered

    ids = await backlog_unregistered()
    if not ids:
        await render_screen(bot, message.chat.id, "Бэклог пуст — слать некому 👍", delete_trigger=message)
        return
    from bot.nudge import NUDGE_TEXT

    await render_screen(
        bot,
        message.chat.id,
        f"📣 Отправить пуш-напоминание <b>{len(ids)}</b> незарегистрированным?\n\n"
        f"<i>Текст:</i>\n{NUDGE_TEXT}",
        reply_markup=nudge_backlog_confirm_keyboard(len(ids)),
        delete_trigger=message,
    )


@router.callback_query(F.data == "nudge_backlog:cancel")
async def nudge_backlog_cancel(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer("Отменено")
    await render_screen(bot, call.message.chat.id, "Ок, рассылку отменил.", reply_markup=admin_menu_keyboard())


@router.callback_query(F.data == "nudge_backlog:send")
async def nudge_backlog_send(call: CallbackQuery, bot: Bot):
    if not _admin_only(call.from_user.id):
        await call.answer("Недоступно", show_alert=True)
        return
    await call.answer("Рассылаю...")
    await render_screen(bot, call.message.chat.id, "📣 Рассылаю напоминания...")
    from bot.nudge import send_backlog

    sent, failed = await send_backlog(bot)
    await render_screen(
        bot,
        call.message.chat.id,
        f"✅ Готово.\nДоставлено: {sent}\nНе удалось: {failed}",
        reply_markup=admin_menu_keyboard(),
    )


@router.callback_query(F.data == "admin:export")
@router.message(Command("export"))
async def admin_export(event, bot: Bot):
    """Один файл на всё: листы «Креаторы», «Заходы», «Не зарегались»."""
    if not _admin_only(event.from_user.id):
        if isinstance(event, CallbackQuery):
            await event.answer("Недоступно", show_alert=True)
        return
    chat_id = event.message.chat.id if isinstance(event, CallbackQuery) else event.chat.id
    if isinstance(event, CallbackQuery):
        await event.answer("Формирую файл...")
    buf = await export_all_xlsx()
    await bot.send_document(
        chat_id,
        BufferedInputFile(buf.read(), filename="ugc_radar.xlsx"),
        caption="📤 Листы: Креаторы · Заходы · Не зарегались",
    )
