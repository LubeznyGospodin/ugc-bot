"""
Креатор-часть пайплайна «Сингл»: подтверждение участия → срок ролика → ссылки на посты.
Оффер-сообщение с кнопкой «Участвую» отправляет sync (при статусе «оффер» в таблице).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.keyboards import single_submit_keyboard
from bot.sheets import SheetsError, sheets_client
from bot.single import (
    ACCEPT_TEXT,
    ASK_DEADLINE_RESUME,
    ASK_LINKS_TEXT,
    ASK_PAYMENT_TEXT,
    BAD_DEADLINE_TEXT,
    BAD_PAYMENT_TEXT,
    DONE_TEXT,
    NOT_A_LINK_TEXT,
    PAYMENT_SAVED_TEXT,
    WAVE2_ASK_DATE,
    WAVE2_STILL_ON_TEXT,
    _participates_single,
    get_pipeline,
    mark_accepted,
    mark_dropped,
    parse_deadline,
    parse_payment,
    producing_text,
    save_payment,
    set_deadline,
    set_wave2,
    submit_links,
    wave2_accepted_text,
    wave2_drop_text,
    wave2_no_text,
)
from bot.states import SingleFSM

logger = logging.getLogger(__name__)
router = Router(name="single")

# В пайплайне «Сингл» НЕ редактируем сообщения «на месте» и не удаляем ответы креатора —
# вся переписка сохраняется (просьба заказчика). Поэтому шлём обычные новые сообщения.


def _mirror(chat_id: int, fields: dict) -> None:
    """Зеркалим поле пайплайна в лист «Отклики» в фоне (вебхук медленный)."""
    async def _run():
        try:
            await sheets_client.single_update(chat_id, fields)
        except SheetsError as e:  # noqa: BLE001
            logger.warning("single_update mirror failed for %s: %s", chat_id, e)

    asyncio.create_task(_run())


@router.callback_query(F.data == "single:accept")
async def single_accept(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await mark_accepted(call.from_user.id)
    _mirror(call.from_user.id, {"confirm": "да"})
    await state.set_state(SingleFSM.waiting_deadline)
    await bot.send_message(call.message.chat.id, ACCEPT_TEXT)


@router.message(SingleFSM.waiting_deadline, F.text)
async def single_deadline(message: Message, state: FSMContext, bot: Bot):
    dl = parse_deadline(message.text)
    if dl is None:
        await message.answer(BAD_DEADLINE_TEXT)
        return
    await set_deadline(message.from_user.id, dl, message.text.strip())
    _mirror(message.from_user.id, {"deadline": dl.strftime("%d.%m.%Y")})
    await state.clear()
    await message.answer(producing_text(dl), reply_markup=single_submit_keyboard())


@router.callback_query(F.data == "single:resume_deadline")
async def single_resume_deadline(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Вернуться к вводу срока из «Мои проекты» (шаг восстановлен из БД)."""
    await call.answer()
    await state.set_state(SingleFSM.waiting_deadline)
    await bot.send_message(call.message.chat.id, ASK_DEADLINE_RESUME)


@router.callback_query(F.data == "single:submit")
async def single_submit_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.set_state(SingleFSM.waiting_links)
    await bot.send_message(call.message.chat.id, ASK_LINKS_TEXT)


@router.callback_query(F.data == "single:resume_payment")
async def single_resume_payment(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Вернуться к вводу реквизитов из «Мои проекты»."""
    await call.answer()
    await state.set_state(SingleFSM.waiting_payment)
    await bot.send_message(call.message.chat.id, ASK_PAYMENT_TEXT)


async def _track_links(tg_id: int, text: str) -> tuple[int, int]:
    """Ссылки из сообщения → в трекинг охватов. Имя креатора берём из БД.
    → (сколько новых, сколько всего ссылок в сообщении)."""
    from bot.reach import add_reach_link, extract_urls
    from bot.utils.db_helpers import get_creator_by_tg_id

    urls = extract_urls(text)
    if not urls:
        return 0, 0
    creator = await get_creator_by_tg_id(tg_id)
    name = (getattr(creator, "full_name", None) if creator else None) or ""
    tg = (getattr(creator, "telegram_contact", None) if creator else None) or ""
    added = 0
    for u in urls:
        new, _ = await add_reach_link(u, name, tg)
        added += 1 if new else 0
    if added:
        _push_links_to_sheet()
    return added, len(urls)


def _n_roliki(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} ролик"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} ролика"
    return f"{n} роликов"


def _added_text(added: int, total: int) -> str:
    """Ответ АДМИНУ на догрузку роликов (можно упоминать таблицу)."""
    if added == 0:
        return ("👌 Эти ролики уже в трекинге — повторно добавлять не нужно.\n"
                "Всё на месте, охваты обновляются автоматически.")
    tail = f" (ещё {total - added} уже были в списке)" if total > added else ""
    return (f"✅ Готово! Загрузил {_n_roliki(added)}{tail}.\n\n"
            f"Ссылки уже в таблице проекта — охваты подтянутся автоматически "
            f"ближайшим обновлением 📊")


def _added_text_creator(added: int, total: int, ref_link: str) -> str:
    """Ответ КРЕАТОРУ: без внутренней кухни (таблиц/прогонов) + призыв снимать ещё
    и звать друзей по реферальной ссылке (в метке зашит его tg_id)."""
    if added == 0:
        return ("👌 Эти ролики уже у меня — повторно присылать не нужно.\n"
                "Слежу за охватами 📊")
    tail = f" (ещё {total - added} уже были)" if total > added else ""
    return (
        f"✅ Готово! Загрузил {_n_roliki(added)}{tail} — буду следить за охватами 📊\n\n"
        f"Снимай ещё, у тебя классно получается 🔥\n\n"
        f"И зови друзей по своей ссылке:\n{ref_link}\n\n"
        f"Чем больше креаторов приходит от тебя, тем выше твой приоритет на проектах 🚀"
    )


async def _ref_link(bot: Bot, tg_id: int) -> str:
    """Персональная реферальная ссылка креатора. Метка ref<id> попадает в трекинг
    источников (см. record_visit/source_stats) — видно, кто кого привёл."""
    me = await bot.me()
    return f"https://t.me/{me.username}?start=ref{tg_id}"


def _push_links_to_sheet() -> None:
    """Ссылки в клиентскую таблицу СРАЗУ (в фоне) — это только Google Sheets, без
    обращений к платным API. Охват подтянется ближайшим прогоном."""
    async def _run():
        try:
            from bot.reach import write_reach_sheet

            res = await write_reach_sheet()
            if not res.get("ok"):
                logger.warning("push links to sheet failed: %s", res.get("error"))
        except Exception as e:  # noqa: BLE001
            logger.warning("push links to sheet failed: %s", e)

    asyncio.create_task(_run())


@router.callback_query(F.data == "single:add_link")
async def single_add_link_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Креатор: «добавить ещё ссылку» — только ссылку, имя берём из БД."""
    await call.answer()
    await state.set_state(SingleFSM.waiting_extra_links)
    await bot.send_message(
        call.message.chat.id,
        "🔗 Пришли ссылку(и) на новые ролики (можно несколько, каждую с новой строки) — "
        "учтём их охваты в статистике проекта.",
    )


@router.message(SingleFSM.waiting_extra_links)
async def single_add_link(message: Message, state: FSMContext, bot: Bot):
    text = (message.text or message.caption or "").strip()
    if "http" not in text.lower():
        await message.answer("Это не похоже на ссылку 🙈 Пришли ссылку на пост (начинается с http…).")
        return
    added, total = await _track_links(message.from_user.id, text)
    await state.clear()
    await message.answer(
        _added_text_creator(added, total, await _ref_link(bot, message.from_user.id)),
        disable_web_page_preview=True,
    )


# СТРАХОВКА: состояние FSM живёт в памяти и стирается при рестарте бота. Без этого
# ссылка, присланная после перезапуска, не попадала НИ В ОДИН обработчик — молча
# терялась. Ловим ссылку от участника «Сингл», когда никакого диалога не идёт.
@router.message(StateFilter(None), F.text.contains("http"))
async def single_loose_link(message: Message, bot: Bot):
    from aiogram.dispatcher.event.bases import SkipHandler

    from bot.reach import detect_platform, extract_urls

    urls = extract_urls(message.text or "")
    # только распознаваемые площадки — случайную ссылку в переписке не трогаем
    if not urls or all(detect_platform(u) == "other" for u in urls):
        raise SkipHandler  # пропускаем дальше по цепочке роутеров
    if not await _participates_single(message.from_user.id):
        raise SkipHandler
    added, total = await _track_links(message.from_user.id, message.text or "")
    await message.answer(
        _added_text_creator(added, total, await _ref_link(bot, message.from_user.id)),
        disable_web_page_preview=True,
    )


@router.message(SingleFSM.waiting_links)
async def single_links(message: Message, state: FSMContext, bot: Bot):
    text = (message.text or message.caption or "").strip()
    if "http" not in text.lower():
        # файл/видео/просто текст без ссылки — не принимаем, объясняем куда что.
        await message.answer(NOT_A_LINK_TEXT)
        return
    await submit_links(message.from_user.id, text)
    _mirror(message.from_user.id, {"links": text})
    # Ссылки → в трекинг охватов (дата добавления = сейчас).
    await _track_links(message.from_user.id, text)
    # После ссылок → просим реквизиты для оплаты по СБП.
    await state.set_state(SingleFSM.waiting_payment)
    await message.answer(DONE_TEXT)

    # Уведомляем админов о сдаче ролика.
    p = await get_pipeline(message.from_user.id)
    name = (p.full_name if p else None) or message.from_user.full_name
    tg = (p.telegram if p else None) or (f"@{message.from_user.username}" if message.from_user.username else "—")
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, f"✅ <b>{name}</b> ({tg}) сдал(а) ролик по «Сингл»:\n{text}")
        except Exception:  # noqa: BLE001
            logger.warning("notify admin %s about single done failed", admin_id)


# ── Вторая волна: перезалив + новый ролик ─────────────────────────────────────
async def _notify_admins(bot: Bot, text: str) -> None:
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:  # noqa: BLE001
            logger.warning("notify admin %s failed", admin_id)


async def _who(chat_id: int, fallback) -> str:
    p = await get_pipeline(chat_id)
    name = (p.full_name if p else None) or fallback.full_name
    tg = (p.telegram if p else None) or (f"@{fallback.username}" if fallback.username else "—")
    return f"<b>{name}</b> ({tg})"


async def _name(chat_id: int, fallback) -> str:
    """Имя для согласования по полу («сделал/сделала»): из пайплайна (там имя из
    таблицы), иначе — из профиля Telegram."""
    p = await get_pipeline(chat_id)
    return (p.full_name if p else None) or fallback.full_name or ""


@router.callback_query(F.data == "w2:go")
async def wave2_go(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await set_wave2(call.from_user.id, "go")
    await state.set_state(SingleFSM.waiting_wave2_date)
    await bot.send_message(call.message.chat.id, WAVE2_ASK_DATE, disable_web_page_preview=True)


@router.callback_query(F.data == "w2:date")
async def wave2_date_resume(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Вернуться к вводу даты по кнопке из пинка (FSM мог сброситься другим диалогом)."""
    await call.answer()
    await state.set_state(SingleFSM.waiting_wave2_date)
    await bot.send_message(call.message.chat.id, WAVE2_ASK_DATE, disable_web_page_preview=True)


@router.callback_query(F.data == "w2:no")
async def wave2_no(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await set_wave2(call.from_user.id, "no")
    _mirror(call.from_user.id, {"wave2": "не интересно"})
    await state.clear()
    await bot.send_message(call.message.chat.id, wave2_no_text(await _name(call.from_user.id, call.from_user)))
    await _notify_admins(bot, f"🚫 {await _who(call.from_user.id, call.from_user)} — 2-я волна: «не интересно».")


@router.message(SingleFSM.waiting_wave2_date, F.text)
async def wave2_date(message: Message, state: FSMContext, bot: Bot):
    dl = parse_deadline(message.text)
    if dl is None:
        await message.answer(BAD_DEADLINE_TEXT)
        return
    await set_wave2(message.from_user.id, "go", dl)
    _mirror(message.from_user.id, {"wave2": dl.strftime("%d.%m.%Y")})
    await state.clear()
    await message.answer(wave2_accepted_text(dl))
    await _notify_admins(
        bot, f"🚀 {await _who(message.from_user.id, message.from_user)} — 2-я волна: публикует {dl.strftime('%d.%m')}."
    )


@router.callback_query(F.data.in_({"w2:still_on", "single:still_on"}))
async def wave_still_on(call: CallbackQuery, bot: Bot):
    """«Всё в силе» — просто подтверждение, этап не меняем."""
    await call.answer("Принято 👍")
    await bot.send_message(call.message.chat.id, WAVE2_STILL_ON_TEXT)


@router.callback_query(F.data.in_({"w2:drop", "single:drop"}))
async def wave_drop(call: CallbackQuery, state: FSMContext, bot: Bot):
    """«Не буду участвовать» — снимаем с напоминаний соответствующей волны."""
    await call.answer()
    wave = 2 if call.data.startswith("w2:") else 1
    if wave == 2:
        await set_wave2(call.from_user.id, "dropped")
        _mirror(call.from_user.id, {"wave2": "отказ"})
    else:
        await mark_dropped(call.from_user.id)
    await state.clear()
    await bot.send_message(call.message.chat.id, wave2_drop_text(await _name(call.from_user.id, call.from_user)))
    await _notify_admins(bot, f"❌ {await _who(call.from_user.id, call.from_user)} — отказ по волне {wave}.")


@router.message(SingleFSM.waiting_payment)
async def single_payment(message: Message, state: FSMContext, bot: Bot):
    parsed = parse_payment(message.text or message.caption or "")
    if parsed is None:
        await message.answer(BAD_PAYMENT_TEXT)
        return
    phone, bank = parsed
    await save_payment(message.from_user.id, phone, bank)
    _mirror(message.from_user.id, {"phone": phone, "bank": bank})
    await state.clear()
    await message.answer(PAYMENT_SAVED_TEXT)

    p = await get_pipeline(message.from_user.id)
    name = (p.full_name if p else None) or message.from_user.full_name
    tg = (p.telegram if p else None) or (f"@{message.from_user.username}" if message.from_user.username else "—")
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"💳 <b>{name}</b> ({tg}) прислал(а) реквизиты по «Сингл»:\n"
                f"Телефон: <code>{phone}</code>\nБанк: {bank or '—'}",
            )
        except Exception:  # noqa: BLE001
            logger.warning("notify admin %s about single payment failed", admin_id)
