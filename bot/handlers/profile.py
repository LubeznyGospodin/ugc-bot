"""
"👤 Моя анкета" — показывает сохранённые данные. Кнопка "Обновить данные"
открывает МЕНЮ полей ("Что скорректировать?") — правим точечно одно поле,
а не переспрашиваем всю анкету. Правка пишется в строку таблицы (doUpdateRow_)
по sheet_row и в локальную БД.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import (
    BTN_ADMIN,
    BTN_BRANDS,
    BTN_HELP,
    BTN_MY_APPS,
    BTN_PROFILE,
    EDITABLE_FIELDS,
    edit_fields_keyboard,
    profile_edit_keyboard,
)
from bot.sheets import SheetsError, sheets_client
from bot.states import EditField
from bot.utils.chat_cleanup import current_screen_id, loading_guard, render_screen
from bot.utils.db_helpers import get_creator_by_tg_id, upsert_creator

logger = logging.getLogger(__name__)
router = Router(name="profile")

_LABELS = dict(EDITABLE_FIELDS)
# ключ данных (для update_row в таблице) -> поле локальной модели Creator
_DB_FIELD = {
    "full_name": "full_name",
    "instagram": "instagram",
    "other_socials": "other_socials",
    "rate": "rate",
    "portfolio": "portfolio",
    "photo": "photo",
    "age": "age",
    "city": "city",
    "phone": "phone",
    "category": "categories",
}
_MENU_BTNS = [BTN_PROFILE, BTN_BRANDS, BTN_MY_APPS, BTN_HELP, BTN_ADMIN]


async def _track_record_line(tg_id: int) -> str:
    """Строка достижений: откликов/офферов/уровень. Читаем из БД-кэша откликов
    (мгновенно, без лишнего запроса к таблице). При ошибке/пустоте — ничего."""
    try:
        from bot.sync import get_cached_applications

        apps = await get_cached_applications(tg_id)
    except Exception:  # noqa: BLE001
        return ""
    if not apps:
        return ""
    total = len(apps)
    offers = sum(1 for a in apps if a.status == "оффер")
    if offers >= 3:
        level = "🏆 Топ"
    elif offers >= 1 or total >= 3:
        level = "⭐ Про"
    else:
        level = "🌱 Новичок"
    return f"\n\n📊 Откликов: {total} · Офферов: {offers} · Уровень: {level}"


def _clean_links(value: str) -> str:
    """Обрезает трекинговые хвосты у ссылок: https://…/makeeva.daa?igsh=…&utm_source=qr
    → https://…/makeeva.daa. Работает и когда в поле несколько ссылок через пробел."""
    if not value:
        return value
    parts = str(value).split()
    cleaned = [(p.split("?", 1)[0] if p.startswith("http") else p) for p in parts]
    return " ".join(cleaned) if cleaned else str(value)


def _format_profile(
    full_name, telegram, instagram, other_socials, photo, portfolio, rate, age, city, phone, category
) -> str:
    """Единое красивое оформление анкеты: эмодзи-метки, разделение на блоки,
    чистые ссылки (без ?igsh/utm-хвостов)."""
    def v(x) -> str:
        return str(x).strip() if x not in (None, "") else "—"

    def link(x) -> str:
        return _clean_links(v(x))

    return (
        f"👤 <b>{v(full_name)}</b>\n"
        f"\n"
        f"✈️ <b>Telegram:</b> {v(telegram)}\n"
        f"📸 <b>Instagram:</b> {link(instagram)}\n"
        f"🔗 <b>Другие соцсети:</b> {link(other_socials)}\n"
        f"🖼 <b>Фото:</b> {link(photo)}\n"
        f"🎬 <b>Портфолио:</b> {link(portfolio)}\n"
        f"\n"
        f"💰 <b>Оплата:</b> {v(rate)}\n"
        f"🎂 <b>Возраст:</b> {v(age)}\n"
        f"📍 <b>Город:</b> {v(city)}\n"
        f"📱 <b>Телефон:</b> {v(phone)}\n"
        f"🏷 <b>Категории:</b> {v(category)}"
    )


def _profile_text_from_sheet(d: dict) -> str:
    """Профиль из живой строки таблицы (ключи по заголовкам из doProfile_)."""
    return _format_profile(
        d.get("full_name"), d.get("telegram"), d.get("instagram"), d.get("other_socials"),
        d.get("photo"), d.get("portfolio"), d.get("rate"), d.get("age"), d.get("city"),
        d.get("phone"), d.get("category"),
    )


def _profile_text(creator) -> str:
    return _format_profile(
        creator.full_name, creator.telegram_contact, creator.instagram, creator.other_socials,
        creator.photo, creator.portfolio, creator.rate, creator.age, creator.city,
        creator.phone, creator.categories,
    )


# Ключи из doProfile_ (таблица) -> поля модели Creator (для кэша).
_SHEET_TO_CREATOR = {
    "full_name": "full_name",
    "telegram": "telegram_contact",
    "instagram": "instagram",
    "other_socials": "other_socials",
    "rate": "rate",
    "portfolio": "portfolio",
    "photo": "photo",
    "age": "age",
    "city": "city",
    "phone": "phone",
    "category": "categories",
}


async def _refresh_profile_hybrid(
    bot: Bot, chat_id: int, uid: int, username: str | None, shown_text: str, screen_id: int
) -> None:
    """Фон: тянет свежую анкету из таблицы → пишет в БД (обновление БД из таблицы).
    Если данные изменились И пользователь всё ещё на экране анкеты — сам
    перерисовывает экран (гибрид: быстро + всегда актуально)."""
    try:
        data = await sheets_client.profile(uid)
    except Exception as e:  # noqa: BLE001
        logger.warning("profile refresh failed: %s", e)
        return
    if not data:
        # Креатора НЕТ в таблице (строку удалили) → не показываем устаревшие данные из
        # кэша: если юзер всё ещё на экране анкеты, зовём заполнить заново.
        if current_screen_id(chat_id) == screen_id:
            await render_screen(
                bot, chat_id, "У тебя пока нет анкеты в базе. Нажми /start, чтобы добавиться."
            )
        return
    fields = {_SHEET_TO_CREATOR[k]: v for k, v in data.items() if k in _SHEET_TO_CREATOR}
    await upsert_creator(uid, username=username, fields=fields)  # БД ⇐ таблица
    fresh_text = _profile_text_from_sheet(data) + await _track_record_line(uid)
    # Обновляем экран, только если он не изменился (юзер всё ещё на анкете).
    if fresh_text != shown_text and current_screen_id(chat_id) == screen_id:
        await render_screen(bot, chat_id, fresh_text, reply_markup=profile_edit_keyboard())


@router.message(F.text == BTN_PROFILE)
async def show_profile(message: Message, bot: Bot, state: FSMContext):
    # Кнопка меню должна работать ВСЕГДА: если юзер застрял в состоянии
    # (недозаполненная анкета/правка) — выходим из него, а не молчим.
    await state.clear()
    uid = message.from_user.id
    used_cache = False
    kb = None
    # Индикатор загрузки, если сбор данных займёт > 0.4с. Из тёплого кэша — мгновенно.
    async with loading_guard(
        bot, message.chat.id, delete_trigger=message, text="Загружаю анкету…"
    ) as lg:
        creator = await get_creator_by_tg_id(uid)
        if creator is not None:
            used_cache = True
            text = _profile_text(creator) + await _track_record_line(uid)
            kb = profile_edit_keyboard()
        else:
            sheet_data = None
            try:
                sheet_data = await sheets_client.profile(uid)
            except SheetsError as e:
                logger.warning("live profile fetch failed: %s", e)
            if sheet_data:
                fields = {_SHEET_TO_CREATOR[k]: v for k, v in sheet_data.items() if k in _SHEET_TO_CREATOR}
                await upsert_creator(uid, username=message.from_user.username, fields=fields)
                text = _profile_text_from_sheet(sheet_data) + await _track_record_line(uid)
                kb = profile_edit_keyboard()
            else:
                text = "У тебя пока нет анкеты. Нажми /start, чтобы заполнить."

    sent = await render_screen(
        bot, message.chat.id, text, reply_markup=kb,
        delete_trigger=None if lg["shown"] else message,
    )
    # Фон: обновить БД из таблицы и, если изменилось, сам перерисовать экран.
    if used_cache:
        asyncio.create_task(
            _refresh_profile_hybrid(
                bot, message.chat.id, uid, message.from_user.username, text, sent.message_id
            )
        )


@router.callback_query(F.data == "profile:edit")
async def edit_menu(call: CallbackQuery, state: FSMContext, bot: Bot):
    """Показать меню «Что хочешь скорректировать?» со списком полей."""
    await call.answer()
    await state.set_state(None)
    await render_screen(
        bot,
        call.message.chat.id,
        "✏️ Что хочешь скорректировать?",
        reply_markup=edit_fields_keyboard(),
    )


@router.callback_query(F.data.startswith("editf:"))
async def edit_pick(call: CallbackQuery, state: FSMContext, bot: Bot):
    key = call.data.split(":", 1)[1]

    if key == "done":
        await call.answer("Готово ✅")
        await state.clear()
        creator = await get_creator_by_tg_id(call.from_user.id)
        if creator is not None:
            await render_screen(
                bot, call.message.chat.id, _profile_text(creator), reply_markup=profile_edit_keyboard()
            )
        return

    if key not in _LABELS:
        await call.answer()
        return

    await call.answer()
    await state.set_state(EditField.waiting_value)
    await state.update_data(edit_field=key)
    if key == "photo":
        prompt = "🖼 Добавь актуальную ссылку на свои фото (любое облако):"
    elif key == "category":
        prompt = "✏️ Введи новое значение — категории через запятую (например: Бьюти, Еда):"
    else:
        prompt = f"✏️ Введи новое значение — {_LABELS[key]}:"
    await render_screen(bot, call.message.chat.id, prompt)


@router.message(EditField.waiting_value, F.text, ~F.text.in_(_MENU_BTNS))
async def edit_receive(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    key = data.get("edit_field")
    value = (message.text or "").strip()
    if not key:
        await state.clear()
        return

    creator = await get_creator_by_tg_id(message.from_user.id)
    sheet_row = getattr(creator, "sheet_row", None) if creator else None

    # Запись в таблицу (update_row) — медленная (вебхук), поэтому под индикатором >0.4с.
    async with loading_guard(
        bot, message.chat.id, delete_trigger=message, text="Сохраняю…"
    ) as lg:
        # 1) обновить только это поле в строке таблицы (строка ищется по chat_id)
        if sheet_row:
            try:
                res = await sheets_client.update_row(sheet_row, {key: value}, chat_id=message.chat.id)
                if not res.get("updated"):
                    logger.warning("edit: строка креатора %s не найдена в таблице", message.from_user.id)
            except SheetsError as e:
                logger.warning("edit field update_row failed: %s", e)
        # 2) обновить локальную БД
        await upsert_creator(
            message.from_user.id,
            username=message.from_user.username,
            fields={_DB_FIELD.get(key, key): value},
            sheet_row=sheet_row,
        )

    # 3) вернуться в меню правки
    await state.set_state(None)
    await render_screen(
        bot,
        message.chat.id,
        f"✅ Обновил «{_LABELS.get(key, key)}». Что ещё скорректировать?",
        reply_markup=edit_fields_keyboard(),
        delete_trigger=None if lg["shown"] else message,
    )
