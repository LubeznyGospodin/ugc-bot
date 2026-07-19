"""
Каталог "Запросы брендов" (критерий приёмки #2). Инлайн-список карточек,
данные тянутся живьём из вкладки "Бренды" в Google Sheet через doBrands_ —
значит, чтобы добавить/убрать проект, не нужно трогать код бота, достаточно
отредактировать таблицу (колонка "Активен" = да/нет).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.keyboards import (
    BTN_BRANDS,
    BTN_MY_APPS,
    back_to_list_keyboard,
    brand_card_keyboard,
    brands_list_keyboard,
)
from bot.sheets import SheetsError, sheets_client
from bot.utils.chat_cleanup import current_screen_id, loading_guard, render_screen
from bot.utils.db_helpers import get_creator_by_tg_id

logger = logging.getLogger(__name__)
router = Router(name="brands")

# Отображение статуса отклика: сырое значение из таблицы -> человекочитаемая строка.
_STATUS_VIEW = {
    "на рассмотрении": "🕐 На рассмотрении",
    "отказ": "❌ Отказ",
    "оффер": "🎉 Оффер!",
}

# Важное сообщение ПОСЛЕ отклика — по конкретным брендам (инструкция + план работы).
# Показывается и на новом отклике, и при повторном (чтобы человек не потерял инструкцию).
_POST_APPLY_NOTE = {
    "br1": (
        "⏳ В течение нескольких часов ты получишь ответ по отклику и дальнейшие шаги — "
        "прямо здесь, в боте."
    ),
}


def _status_line(status: str) -> str:
    return _STATUS_VIEW.get((status or "").strip().lower(), "🕐 На рассмотрении")


def _creator_categories(creator) -> set[str]:
    raw = (getattr(creator, "categories", None) or "") if creator else ""
    return {c.strip().lower() for c in raw.replace(";", ",").split(",") if c.strip()}


def _personalize(brands, cats: set[str]):
    """Сортирует бренды: подходящие под категории креатора — вверх, помечены 🔥.
    Возвращает (упорядоченный список, множество id «горячих»)."""
    if not cats:
        return brands, set()
    hot = set()
    for b in brands:
        bc = (b.category or "").strip().lower()
        if bc and any(t in bc or bc in t for t in cats):
            hot.add(b.id)
    ordered = [b for b in brands if b.id in hot] + [b for b in brands if b.id not in hot]
    return ordered, hot


async def _fetch_brands():
    # 1) Мгновенно из БД-кэша (фоновый синк держит его свежим).
    try:
        from bot.sync import get_cached_brands

        cached = await get_cached_brands()
        if cached:
            return cached
    except Exception as e:  # noqa: BLE001
        logger.warning("cached brands read failed: %s", e)
    # 2) Холодный старт кэша — идём в таблицу напрямую.
    try:
        return await sheets_client.brands()
    except SheetsError as e:
        logger.warning("brands fetch failed: %s", e)
        return []


def _apps_text(apps) -> str:
    if not apps:
        return (
            "📨 <b>Мои отклики</b>\n\n"
            "Пока ты ни на что не откликался. Загляни в «🎯 Запросы брендов» "
            "и откликнись на подходящий проект."
        )
    lines = ["📨 <b>Мои отклики</b>\n"]
    for a in apps:
        line = f"• <b>{a.brand_title}</b> — {_status_line(a.status)}"
        if a.status == "отказ" and a.reason:
            line += f"\n  <i>Причина: {a.reason}</i>"
        lines.append(line)
    lines.append("\n<i>Статус обновляется, когда бренд рассмотрит твой отклик.</i>")
    return "\n".join(lines)


async def _refresh_apps_hybrid(
    bot: Bot, chat_id: int, uid: int, shown_text: str, screen_id: int
) -> None:
    """Фон: обновляет кэш откликов из таблицы и, если изменилось и юзер всё ещё
    на экране откликов — сам перерисовывает (гибрид)."""
    try:
        from bot.sync import cache_applications

        fresh = await sheets_client.my_applications(uid)
        await cache_applications(uid, fresh)
    except Exception as e:  # noqa: BLE001
        logger.warning("apps cache refresh failed: %s", e)
        return
    fresh_text = _apps_text(fresh)
    if fresh_text != shown_text and current_screen_id(chat_id) == screen_id:
        await render_screen(bot, chat_id, fresh_text)


@router.message(F.text == BTN_MY_APPS)
async def show_my_applications(message: Message, bot: Bot):
    from bot.sync import cache_applications, get_cached_applications

    uid = message.from_user.id
    used_cache = False
    async with loading_guard(
        bot, message.chat.id, delete_trigger=message, text="Загружаю отклики…"
    ) as lg:
        cached = []
        try:
            cached = await get_cached_applications(uid)
        except Exception as e:  # noqa: BLE001
            logger.warning("cached applications read failed: %s", e)
        if cached:
            used_cache = True
            shown = _apps_text(cached)
        else:
            try:
                fresh = await sheets_client.my_applications(uid)
                await cache_applications(uid, fresh)
            except SheetsError as e:
                logger.warning("my_applications fetch failed: %s", e)
                fresh = []
            shown = _apps_text(fresh)

    sent = await render_screen(
        bot, message.chat.id, shown, delete_trigger=None if lg["shown"] else message
    )
    # Гибридное фоновое обновление — только если показывали из кэша.
    if used_cache:
        asyncio.create_task(
            _refresh_apps_hybrid(bot, message.chat.id, uid, shown, sent.message_id)
        )


def _brands_text(brands, hot) -> str:
    if not brands:
        return "Пока нет активных запросов от брендов — загляните позже 🙂"
    text = "📢 Актуальные запросы от брендов:"
    if hot:
        text += "\n🔥 — подходит под твои категории"
    return text


@router.message(F.text == BTN_BRANDS)
async def show_brands(message: Message, bot: Bot):
    # Индикатор загрузки, если чтение брендов/креатора займёт > 0.4с (холодный кэш /
    # медленная БД). Из тёплого кэша — мгновенно, без мигания.
    async with loading_guard(
        bot, message.chat.id, delete_trigger=message, text="Загружаю запросы брендов…"
    ) as lg:
        brands = await _fetch_brands()
        creator = await get_creator_by_tg_id(message.from_user.id)
        brands, hot = _personalize(brands, _creator_categories(creator))
    await render_screen(
        bot, message.chat.id, _brands_text(brands, hot),
        reply_markup=brands_list_keyboard(brands, hot),
        delete_trigger=None if lg["shown"] else message,
    )


@router.callback_query(F.data == "brands:list")
async def back_to_list(call: CallbackQuery, bot: Bot):
    await call.answer()
    async with loading_guard(bot, call.message.chat.id, text="Загружаю запросы брендов…"):
        brands = await _fetch_brands()
        creator = await get_creator_by_tg_id(call.from_user.id)
        brands, hot = _personalize(brands, _creator_categories(creator))
    await render_screen(
        bot, call.message.chat.id, _brands_text(brands, hot),
        reply_markup=brands_list_keyboard(brands, hot),
    )


@router.callback_query(F.data.startswith("brand:"))
async def brand_card(call: CallbackQuery, bot: Bot):
    await call.answer()
    brand_id = call.data.split(":", 1)[1]

    async with loading_guard(bot, call.message.chat.id, text="Загружаю карточку…"):
        brands = await _fetch_brands()
        brand = next((b for b in brands if b.id == brand_id), None)
    if brand is None:
        await render_screen(bot, call.message.chat.id, "Этот запрос уже неактуален.", reply_markup=brands_list_keyboard(brands))
        return

    text = f"<b>{brand.title}</b>\nКатегория: {brand.category}\n\n{brand.description}"
    await render_screen(bot, call.message.chat.id, text, reply_markup=brand_card_keyboard(brand.id))


@router.callback_query(F.data.startswith("brand_apply:"))
async def brand_apply(call: CallbackQuery, bot: Bot):
    from bot.sync import add_cached_application

    await call.answer()
    brand_id = call.data.split(":", 1)[1]
    chat_id = call.from_user.id

    brands = await _fetch_brands()
    brand = next((b for b in brands if b.id == brand_id), None)
    brand_title = brand.title if brand else brand_id

    creator = await get_creator_by_tg_id(chat_id)
    name = (getattr(creator, "full_name", None) if creator else None) or call.from_user.full_name or ""
    telegram = (getattr(creator, "telegram_contact", None) if creator else None) or (
        f"@{call.from_user.username}" if call.from_user.username else ""
    )

    # Отклик пишем СИНХРОННО (под индикатором): нужно поймать дубль и показать статус.
    async with loading_guard(bot, chat_id, text="Отправляю отклик…"):
        try:
            res = await sheets_client.apply(brand_id, brand_title, name, telegram, chat_id)
        except SheetsError as e:
            logger.warning("brand apply failed: %s", e)
            res = None

    if res is None:
        await render_screen(
            bot, chat_id, "😔 Не получилось отправить отклик — попробуй ещё раз чуть позже.",
            reply_markup=back_to_list_keyboard(),
        )
        return

    # НЕ зарегистрирован (chat_id нет в базе анкет — проверка на стороне таблицы) →
    # откликаться нельзя, зовём заполнить анкету.
    if res.get("not_registered"):
        await render_screen(
            bot, chat_id,
            "📝 Чтобы откликаться на бренды, сначала заполни короткую анкету.\n"
            "Нажми /start — это займёт пару минут.",
            reply_markup=back_to_list_keyboard(),
        )
        return

    # Повторный отклик запрещён — показываем текущий статус (и причину, если есть).
    if res.get("duplicate"):
        status = str(res.get("status") or "на рассмотрении").strip().lower()
        text = (
            f"👀 Вижу, от тебя уже есть отклик на «{brand_title}».\n"
            f"Текущий статус: {_status_line(status)}"
        )
        reason = str(res.get("reason") or "")
        if reason and status in ("оффер", "отказ"):
            text += f"\nПричина: {reason}"
        note = _POST_APPLY_NOTE.get(brand_id)
        if note:
            text += f"\n\n{note}"
        await render_screen(bot, chat_id, text, reply_markup=back_to_list_keyboard())
        return

    # Новый отклик: в кэш (мгновенно виден в «Мои отклики») + уведомляем админов.
    try:
        await add_cached_application(chat_id, brand_title)
    except Exception as e:  # noqa: BLE001
        logger.warning("add_cached_application failed: %s", e)
    creator_ig = (getattr(creator, "instagram", None) if creator else None) or ""
    admin_text = (
        f"🙋 Новый отклик на бренд <b>{brand_title}</b> (<code>{brand_id}</code>)\n"
        f"От: {name or call.from_user.full_name} ({telegram or '—'}), id {chat_id}"
    )
    if creator_ig:
        admin_text += f"\nInstagram: {creator_ig}"
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, admin_text)
        except Exception:  # noqa: BLE001
            logger.warning("failed to notify admin %s about brand response", admin_id)
    note = _POST_APPLY_NOTE.get(brand_id)
    success = "🎉 Отклик принят!"
    if note:
        success += f"\n\n{note}"
    else:
        success += " Статус смотри в меню «📨 Мои отклики»."
    await render_screen(bot, chat_id, success, reply_markup=back_to_list_keyboard())
