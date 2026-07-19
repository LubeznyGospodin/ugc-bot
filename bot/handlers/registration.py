"""
Анкета нового креатора. Линейный FSM-опросник, как и раньше, но:
  - каждый шаг рендерится через render_screen (одно эволюционирующее сообщение,
    сообщения пользователя удаляются) — критерий приёмки #1;
  - на confirm пишем в Google Sheets (action=register, как раньше) И
    дополнительно делаем lookup, чтобы узнать номер строки и сохранить
    его локально (sheet_row) — на будущее, для doUpdateRow_.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaDocument, InputMediaPhoto, Message

from bot.categories import categories_keyboard
from bot.config import settings
from bot.keyboards import (
    BTN_ADMIN,
    BTN_BRANDS,
    BTN_HELP,
    BTN_MY_APPS,
    BTN_PROJECTS,
    BTN_PROFILE,
    BTN_SHARE_CONTACT,
    contact_request_keyboard,
    main_menu,
    photo_done_keyboard,
    profile_edit_keyboard,
    skip_photo_keyboard,
)
from bot.sheets import SheetsError, sheets_client
from bot.states import Registration
from bot.utils.chat_cleanup import ensure_menu, render_loading, render_screen, reset_menu
from bot.utils.db_helpers import get_creator_by_tg_id, upsert_creator

logger = logging.getLogger(__name__)
router = Router(name="registration")

_MENU_BTNS = [BTN_PROFILE, BTN_BRANDS, BTN_MY_APPS, BTN_PROJECTS, BTN_HELP, BTN_ADMIN]

# Фото/файлы, присланные прямо в чат на шаге «фото», копим в памяти по chat_id как
# (kind, file_id), kind ∈ {"photo","doc"}. list.append в одном event-loop без await между
# get и append — гонки нет, в отличие от FSM-хранилища, куда альбом из N сообщений писал
# бы конкурентно. Забираем на «Готово»/submit.
_photo_buffer: dict[int, list[tuple[str, str]]] = {}
_MAX_FWD = 10  # столько медиа Telegram отдаёт одной медиагруппой

STEP_PROMPTS = {
    Registration.instagram: "Ссылка на Instagram (или другую соцсеть с примерами работ)?",
    Registration.other_socials: "Другие соцсети (TikTok/YouTube и т.д.)? Если нет — напиши «нет».",
    Registration.rate: "Желаемая оплата за 1 ролик под ключ?",
    Registration.portfolio: "Ссылка на портфолио/примеры работ?",
    Registration.photo: (
        "🖼 Пришли свои фото в хорошем качестве (идеально студийные, не менее 4 фото).\n\n"
        "📎 Можно отправить фото <b>прямо сюда</b> — по одному или альбомом. "
        "Либо вставь ссылку на них.\n\n"
        "Это нужно для базы креаторов: https://t.me/ugc_creatory.\n\n"
        "Когда загрузишь все фото — жми «✅ Готово». Или «Добавить позже»."
    ),
    Registration.age: "Сколько тебе лет?",
    Registration.city: "В каком городе живёшь?",
}

# Порядок шагов (для вычисления следующего). Telegram больше НЕ спрашиваем — берём
# @username автоматически. Телефон — отдельным шагом через «Поделиться контактом».
STEP_ORDER = [
    Registration.instagram,
    Registration.other_socials,
    Registration.rate,
    Registration.portfolio,
    Registration.photo,
    Registration.age,
    Registration.city,
    Registration.phone,
    Registration.categories,
]

# Текстовые шаги, которые обрабатывает _generic_step (без phone и categories).
TEXT_STEPS = [
    Registration.instagram,
    Registration.other_socials,
    Registration.rate,
    Registration.portfolio,
    Registration.photo,
    Registration.age,
    Registration.city,
]

FIELD_BY_STATE = {
    Registration.instagram: "instagram",
    Registration.other_socials: "other_socials",
    Registration.rate: "rate",
    Registration.portfolio: "portfolio",
    Registration.photo: "photo",
    Registration.age: "age",
    Registration.city: "city",
    Registration.phone: "phone",
}

# Нумерация шагов для прогресса «Шаг X из N» (чтобы креатору было видно, сколько осталось).
STEP_NO = {
    Registration.full_name: 1,
    Registration.instagram: 2,
    Registration.other_socials: 3,
    Registration.rate: 4,
    Registration.portfolio: 5,
    Registration.photo: 6,
    Registration.age: 7,
    Registration.city: 8,
    Registration.phone: 9,
    Registration.categories: 10,
}
TOTAL_STEPS = 10


def _prog(state) -> str:
    """Префикс прогресса перед вопросом, напр. «📋 Шаг 3 из 10»."""
    n = STEP_NO.get(state)
    return f"📋 <b>Шаг {n} из {TOTAL_STEPS}</b>\n\n" if n else ""


def _prompt(state) -> str:
    """Текст шага с префиксом прогресса (для шагов из STEP_PROMPTS)."""
    return _prog(state) + STEP_PROMPTS.get(state, "")


@router.message(Registration.full_name, ~F.text.in_(_MENU_BTNS))
async def step_full_name(message: Message, state: FSMContext, bot: Bot):
    # Telegram считываем автоматически из профиля — не спрашиваем отдельно.
    telegram = f"@{message.from_user.username}" if message.from_user.username else ""
    await state.update_data(full_name=message.text.strip(), telegram=telegram)
    await state.set_state(Registration.instagram)
    await render_screen(bot, message.chat.id, _prompt(Registration.instagram), delete_trigger=message)


def _next_state(current) -> "State | None":
    idx = STEP_ORDER.index(current)
    return STEP_ORDER[idx + 1] if idx + 1 < len(STEP_ORDER) else None


async def _ask_phone(chat_id: int, state: FSMContext, bot: Bot, trigger: Message | None) -> None:
    await state.set_state(Registration.phone)
    await render_screen(
        bot,
        chat_id,
        _prog(Registration.phone)
        + "📱 Оставь номер телефона: нажми «Поделиться контактом» ниже или введи вручную.",
        reply_markup=contact_request_keyboard(),
        delete_trigger=trigger,
    )


async def _ask_photo(chat_id: int, state: FSMContext, bot: Bot, trigger: Message | None) -> None:
    """Шаг «фото» — с инлайн-кнопкой «Добавить позже» (фото необязательно на входе)."""
    await state.set_state(Registration.photo)
    await render_screen(
        bot,
        chat_id,
        _prompt(Registration.photo),
        reply_markup=skip_photo_keyboard(),
        delete_trigger=trigger,
    )


# Регистрируются РАНЬШЕ дженерик-цикла (по порядку в файле) → фото/файл на шаге «фото»
# перехватывают эти хендлеры, а не защита «напиши текстом».
async def _accept_media(message: Message, bot: Bot, kind: str, file_id: str) -> None:
    """Принять присланное медиа (фото или файл). Копим file_id; на альбом не спамим —
    экран с кнопкой «Готово» рисуем только на первом, остальные сообщения убираем."""
    buf = _photo_buffer.setdefault(message.chat.id, [])
    buf.append((kind, file_id))
    n = len(buf)  # синхронно после append — гонки в альбоме нет
    if n == 1:
        await render_screen(
            bot,
            message.chat.id,
            _prog(Registration.photo)
            + "📸 Принял! Пришли ещё, если нужно — фото или файлом.\n\n"
            "Когда всё загрузишь — жми «✅ Готово».",
            reply_markup=photo_done_keyboard(),
            delete_trigger=message,
        )
    else:
        try:
            await bot.delete_message(message.chat.id, message.message_id)
        except Exception:  # noqa: BLE001
            pass


@router.message(Registration.photo, F.photo)
async def photo_upload(message: Message, state: FSMContext, bot: Bot):
    """Фото прислали сжатым (обычный способ)."""
    await _accept_media(message, bot, "photo", message.photo[-1].file_id)


@router.message(Registration.photo, F.document)
async def photo_as_document(message: Message, state: FSMContext, bot: Bot):
    """Фото прислали файлом (без сжатия) — тоже принимаем."""
    await _accept_media(message, bot, "doc", message.document.file_id)


async def _generic_step(message: Message, state: FSMContext, bot: Bot):
    # Пришло не текстом (фото/стикер/видео) — не роняем шаг (раньше message.text.strip()
    # падал на None и опрос «зависал»). Фото на шаге фото ловит отдельный хендлер ниже;
    # прочий не-текст просто игнорируем и мягко просим текст.
    if message.text is None:
        await render_screen(
            bot, message.chat.id, "Напиши, пожалуйста, ответ текстом 🙂", delete_trigger=message
        )
        return
    current = await state.get_state()
    current_enum = next(s for s in STEP_ORDER if s.state == current)
    field = FIELD_BY_STATE[current_enum]
    await state.update_data(**{field: message.text.strip()})
    # На шаге фото прислали ссылку текстом — но могли ДО этого кинуть и фото в чат.
    # Забираем накопленное, чтобы тоже переслать команде.
    if current_enum == Registration.photo:
        ids = _photo_buffer.pop(message.chat.id, [])
        if ids:
            await state.update_data(photo_ids=ids)

    nxt = _next_state(current_enum)
    if nxt == Registration.phone:
        await _ask_phone(message.chat.id, state, bot, message)
    elif nxt == Registration.photo:
        await _ask_photo(message.chat.id, state, bot, message)
    elif nxt == Registration.categories or nxt is None:
        await _show_categories(message.chat.id, state, bot, message)
    else:
        await state.set_state(nxt)
        await render_screen(bot, message.chat.id, _prompt(nxt), delete_trigger=message)


for _state in TEXT_STEPS:
    router.message.register(_generic_step, _state, ~F.text.in_(_MENU_BTNS))


async def _after_phone(chat_id: int, state: FSMContext, bot: Bot, trigger: Message, tg_id: int) -> None:
    # Контакт-клавиатура заменила основное меню — возвращаем его на место.
    reset_menu(chat_id)
    await ensure_menu(bot, chat_id, main_menu(settings.is_admin(tg_id)))
    await _show_categories(chat_id, state, bot, trigger)


@router.message(Registration.phone, F.contact)
async def phone_via_contact(message: Message, state: FSMContext, bot: Bot):
    await state.update_data(phone=message.contact.phone_number)
    await _after_phone(message.chat.id, state, bot, message, message.from_user.id)


@router.message(Registration.phone, F.text, ~F.text.in_(_MENU_BTNS + [BTN_SHARE_CONTACT]))
async def phone_via_text(message: Message, state: FSMContext, bot: Bot):
    await state.update_data(phone=message.text.strip())
    await _after_phone(message.chat.id, state, bot, message, message.from_user.id)


@router.callback_query(Registration.photo, F.data == "reg:photo_done")
async def photo_done(call: CallbackQuery, state: FSMContext, bot: Bot):
    """«Готово» на шаге фото — фиксируем присланные фото и идём дальше к возрасту."""
    await call.answer()
    ids = _photo_buffer.pop(call.message.chat.id, [])
    n = len(ids)
    await state.update_data(photo=(f"📷 {n} фото загружено в бот" if n else ""), photo_ids=ids)
    await state.set_state(Registration.age)
    await render_screen(bot, call.message.chat.id, _prompt(Registration.age))


@router.callback_query(Registration.photo, F.data == "reg:photo_skip")
async def photo_skip(call: CallbackQuery, state: FSMContext, bot: Bot):
    """«Добавить позже» на шаге фото — пропускаем, идём дальше к возрасту."""
    await call.answer()
    _photo_buffer.pop(call.message.chat.id, None)  # если что-то накидали, но передумали
    await state.update_data(photo="", photo_ids=[])
    await state.set_state(Registration.age)
    await render_screen(bot, call.message.chat.id, _prompt(Registration.age))


async def _show_categories(chat_id: int, state: FSMContext, bot: Bot, trigger: Message | None = None):
    await state.update_data(categories=[])
    await state.set_state(Registration.categories)
    await render_screen(
        bot,
        chat_id,
        _prog(Registration.categories)
        + "Выбери свои категории контента (можно несколько), затем «Готово»:",
        reply_markup=categories_keyboard(set()),
        delete_trigger=trigger,
    )


@router.callback_query(Registration.categories, F.data.startswith("cat:"))
async def pick_category(call: CallbackQuery, state: FSMContext, bot: Bot):
    value = call.data.split(":", 1)[1]
    data = await state.get_data()
    selected = set(data.get("categories") or [])

    if value == "done":
        await state.update_data(categories=list(selected))
        await _show_confirm(call.message.chat.id, state, bot)
        await call.answer()
        return

    if value in selected:
        selected.discard(value)
    else:
        selected.add(value)
    await state.update_data(categories=list(selected))
    await call.message.edit_reply_markup(reply_markup=categories_keyboard(selected))
    await call.answer()


async def _show_confirm(chat_id: int, state: FSMContext, bot: Bot):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    data = await state.get_data()
    summary = (
        f"Проверь анкету:\n\n"
        f"Имя: {data.get('full_name')}\n"
        f"Telegram: {data.get('telegram')}\n"
        f"Instagram: {data.get('instagram')}\n"
        f"Соцсети: {data.get('other_socials')}\n"
        f"Оплата: {data.get('rate')}\n"
        f"Портфолио: {data.get('portfolio')}\n"
        f"Фото: {data.get('photo')}\n"
        f"Возраст: {data.get('age')}\n"
        f"Город: {data.get('city')}\n"
        f"Телефон: {data.get('phone')}\n"
        f"Категории: {', '.join(data.get('categories') or [])}\n"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Всё верно, отправить", callback_data="reg:submit"),
                InlineKeyboardButton(text="🔄 Начать заново", callback_data="reg:restart"),
            ]
        ]
    )
    await state.set_state(Registration.confirm)
    await render_screen(bot, chat_id, summary, reply_markup=kb)


@router.callback_query(Registration.confirm, F.data == "reg:restart")
async def restart(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.clear()
    await state.set_state(Registration.full_name)
    await render_screen(bot, call.message.chat.id, "Хорошо, начнём заново. Как тебя зовут (имя и фамилия)?")


@router.callback_query(Registration.confirm, F.data == "reg:submit")
async def submit(call: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    data["category"] = ", ".join(data.pop("categories", []) or [])
    # photo_ids — служебное (file_id присланных фото), в таблицу НЕ шлём, а пересылаем
    # команде после сохранения.
    photo_ids = data.pop("photo_ids", None) or []
    chat_id = call.message.chat.id
    tg_id = call.from_user.id

    logger.info(f"Registration submit for user {tg_id}: {data.get('full_name')}")
    await call.answer("Отправляю...")
    await render_loading(bot, chat_id, "Сохраняю анкету…")

    # Обновляем строку в таблице ТОЛЬКО если она уже привязана к этому юзеру (его
    # узнали и он подтвердил «Да, это я» на /start → в БД есть sheet_row). Иначе —
    # это новый креатор → ДОБАВЛЯЕМ новую строку. НЕ ищем по нечёткому совпадению
    # перед записью: раньше это перезаписывало чужую строку с похожим именем.
    existing = await get_creator_by_tg_id(tg_id)
    is_edit = existing is not None and existing.sheet_row is not None
    sheet_row = existing.sheet_row if is_edit else None

    # Отправляй в Google Sheets (обновляй привязанную строку или добавляй новую)
    try:
        logger.info(f"Saving to sheets: is_edit={is_edit}, sheet_row={sheet_row}")
        if is_edit and sheet_row is not None:
            res = await sheets_client.update_row(sheet_row, data, chat_id=chat_id)
            if not res.get("updated"):
                # Строку удалили / номер устарел → чужую не трогаем, ДОБАВЛЯЕМ новую.
                logger.info("update: row gone, appending new")
                await sheets_client.register_creator(data, chat_id=chat_id)
                sheet_row = None  # sheet_row самоправится на синке к новой строке
        else:
            logger.info("Creating new row")
            await sheets_client.register_creator(data, chat_id=chat_id)
    except SheetsError as e:
        logger.error("register/update failed: %s", e)
        await render_screen(
            bot, chat_id, "😔 Не получилось сохранить анкету — попробуйте ещё раз чуть позже (/start)."
        )
        await state.clear()
        return

    # 4. Сохрани в локальной БД с sheet_row
    await upsert_creator(tg_id, username=call.from_user.username, fields=data, sheet_row=sheet_row)
    await state.clear()

    # Фото, присланные в чат: СНАЧАЛА сохраняем file_id в БД (чтобы их можно было
    # отправить повторно/в другое место), затем шлём в рабочую группу.
    if photo_ids:
        await _save_photo_ids(tg_id, photo_ids)
        await _forward_photos_to_admins(
            bot, data.get("full_name"), data.get("telegram"), chat_id, photo_ids
        )

    await render_screen(
        bot,
        chat_id,
        "🎉 Готово! Анкета сохранена. Мы на связи, если появятся подходящие проекты.",
        reply_markup=profile_edit_keyboard(),
    )


async def _send_media_group(bot: Bot, admin_id: int, media_cls, ids: list[str]) -> None:
    """Отправить пачку медиа одного типа (фото ИЛИ документы) — по одному или группой."""
    if not ids:
        return
    if len(ids) == 1:
        if media_cls is InputMediaPhoto:
            await bot.send_photo(admin_id, ids[0])
        else:
            await bot.send_document(admin_id, ids[0])
    else:
        await bot.send_media_group(admin_id, [media_cls(media=fid) for fid in ids])


async def _save_photo_ids(tg_id: int, items: list[tuple[str, str]]) -> None:
    """Сохранить file_id в БД — чтобы фото можно было отправить повторно/куда угодно."""
    from bot.database import get_session
    from bot.models import CreatorPhoto

    try:
        async with get_session() as session:
            for kind, fid in items:
                session.add(CreatorPhoto(tg_id=tg_id, kind=kind, file_id=fid))
            await session.commit()
    except Exception as e:  # noqa: BLE001 — потеря анкеты из-за этого недопустима
        logger.warning("save photo ids failed for %s: %s", tg_id, e)


async def _forward_photos_to_admins(
    bot: Bot, name: str | None, telegram: str | None, chat_id: int, items: list[tuple[str, str]]
) -> None:
    """Отправить присланные креатором фото/файлы в рабочую группу (PHOTOS_CHAT_ID), а если
    она не задана — админам в личку, как раньше. Фото и документы шлём раздельными
    медиагруппами — смешивать типы нельзя."""
    items = items[:_MAX_FWD]
    photos = [fid for kind, fid in items if kind == "photo"]
    docs = [fid for kind, fid in items if kind == "doc"]
    header = f"📷 Фото креатора {name or '—'} ({telegram or '—'}, id {chat_id})"
    targets = [settings.photos_chat_id] if settings.photos_chat_id else list(settings.admin_ids)
    for target in targets:
        try:
            await bot.send_message(target, header)
            await _send_media_group(bot, target, InputMediaPhoto, photos)
            await _send_media_group(bot, target, InputMediaDocument, docs)
        except Exception as e:  # noqa: BLE001
            logger.warning("send photos to %s failed: %s", target, e)
