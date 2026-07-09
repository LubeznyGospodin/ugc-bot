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
from bot.keyboards import BTN_BRANDS, BTN_MY_APPS, brand_card_keyboard, brands_list_keyboard
from bot.sheets import SheetsError, sheets_client
from bot.utils.chat_cleanup import current_screen_id, render_loading, render_screen
from bot.utils.db_helpers import get_creator_by_tg_id

logger = logging.getLogger(__name__)
router = Router(name="brands")

# Отображение статуса отклика: сырое значение из таблицы -> человекочитаемая строка.
_STATUS_VIEW = {
    "на рассмотрении": "🕐 На рассмотрении",
    "отказ": "❌ Отказ",
    "оффер": "🎉 Оффер!",
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
    cached = []
    try:
        cached = await get_cached_applications(uid)
    except Exception as e:  # noqa: BLE001
        logger.warning("cached applications read failed: %s", e)

    if cached:
        # Есть кэш → один рендер мгновенно (автоскролл цел) + гибридное обновление.
        shown = _apps_text(cached)
        sent = await render_screen(bot, message.chat.id, shown, delete_trigger=message)
        asyncio.create_task(
            _refresh_apps_hybrid(bot, message.chat.id, uid, shown, sent.message_id)
        )
        return

    # Кэша нет (первый раз) → показываем анимированную загрузку и тянем один раз.
    await render_loading(bot, message.chat.id, "Загружаю отклики…", delete_trigger=message)
    try:
        fresh = await sheets_client.my_applications(uid)
        await cache_applications(uid, fresh)
    except SheetsError as e:
        logger.warning("my_applications fetch failed: %s", e)
        fresh = []
    # delete_trigger=None → правим экран-загрузку на месте (внизу).
    await render_screen(bot, message.chat.id, _apps_text(fresh))


def _brands_text(brands, hot) -> str:
    if not brands:
        return "Пока нет активных запросов от брендов — загляните позже 🙂"
    text = "📢 Актуальные запросы от брендов:"
    if hot:
        text += "\n🔥 — подходит под твои категории"
    return text


@router.message(F.text == BTN_BRANDS)
async def show_brands(message: Message, bot: Bot):
    # Бренды теперь из БД-кэша (мгновенно) — рендерим ОДИН раз, чтобы автоскролл
    # работал как раньше (двойной рендер с индикатором ломал скролл).
    brands = await _fetch_brands()
    creator = await get_creator_by_tg_id(message.from_user.id)
    brands, hot = _personalize(brands, _creator_categories(creator))
    await render_screen(
        bot, message.chat.id, _brands_text(brands, hot),
        reply_markup=brands_list_keyboard(brands, hot), delete_trigger=message,
    )


@router.callback_query(F.data == "brands:list")
async def back_to_list(call: CallbackQuery, bot: Bot):
    await call.answer()
    brands = await _fetch_brands()
    creator = await get_creator_by_tg_id(call.from_user.id)
    brands, hot = _personalize(brands, _creator_categories(creator))
    await render_screen(
        bot, call.message.chat.id, _brands_text(brands, hot),
        reply_markup=brands_list_keyboard(brands, hot),
    )


@router.callback_query(F.data.startswith("brand:"))
async def brand_card(call: CallbackQuery, bot: Bot):
    brand_id = call.data.split(":", 1)[1]

    brands = await _fetch_brands()
    brand = next((b for b in brands if b.id == brand_id), None)
    await call.answer()
    if brand is None:
        await render_screen(bot, call.message.chat.id, "Этот запрос уже неактуален.", reply_markup=brands_list_keyboard(brands))
        return

    text = f"<b>{brand.title}</b>\nКатегория: {brand.category}\n\n{brand.description}"
    await render_screen(bot, call.message.chat.id, text, reply_markup=brand_card_keyboard(brand.id))


async def _record_apply_bg(
    bot: Bot, brand_id: str, brand_title: str, name: str, telegram: str, chat_id: int, full_name: str
) -> None:
    """Фон: пишем отклик в таблицу (с ретраем в _post) и уведомляем админов.
    Пользователь уже получил подтверждение и отклик уже в БД — таймаут таблицы
    больше не теряет отклик и не заставляет ждать."""
    try:
        await sheets_client.apply(
            brand_id=brand_id, brand_title=brand_title, name=name,
            telegram=telegram, chat_id=chat_id,
        )
    except SheetsError as e:
        logger.warning("brand apply sheet write failed (в БД уже есть): %s", e)
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(
                admin_id,
                f"🙋 Отклик на бренд <b>{brand_title}</b> (<code>{brand_id}</code>)\n"
                f"От: {name or full_name} ({telegram or '—'}), id {chat_id}",
            )
        except Exception:
            logger.warning("failed to notify admin %s about brand response", admin_id)


@router.callback_query(F.data.startswith("brand_apply:"))
async def brand_apply(call: CallbackQuery, bot: Bot):
    from bot.sync import add_cached_application

    brand_id = call.data.split(":", 1)[1]

    # Ответ показываем СРАЗУ.
    await call.answer(
        "Отклик отправлен! За статусом следи в меню «📨 Мои отклики».",
        show_alert=True,
    )

    brands = await _fetch_brands()
    brand = next((b for b in brands if b.id == brand_id), None)
    brand_title = brand.title if brand else brand_id

    creator = await get_creator_by_tg_id(call.from_user.id)
    name = (getattr(creator, "full_name", None) if creator else None) or call.from_user.full_name or ""
    telegram = (getattr(creator, "telegram_contact", None) if creator else None) or (
        f"@{call.from_user.username}" if call.from_user.username else ""
    )

    # 1) МГНОВЕННО в БД — отклик гарантированно записан и сразу виден в «Мои отклики».
    try:
        await add_cached_application(call.from_user.id, brand_title)
    except Exception as e:  # noqa: BLE001
        logger.warning("add_cached_application failed: %s", e)

    # 2) Запись в таблицу + уведомление админов — в ФОНЕ (не блокируем пользователя,
    #    таймаут таблицы не теряет отклик).
    asyncio.create_task(
        _record_apply_bg(
            bot, brand_id, brand_title, name, telegram, call.from_user.id, call.from_user.full_name
        )
    )
