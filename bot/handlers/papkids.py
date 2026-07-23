"""
Проект PapKids: участник загружает до 5 роликов в бота. Каждый ролик СРАЗУ уходит в
группу клиента (в отдельную тему) — без модерации — и обновляется счётчик в клиентской
таблице (только имя + id + счётчик, без логинов).

Переиспользуем скачивание по ссылке из works.py (yt-dlp) — код не дублируем.
"""
from __future__ import annotations

import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.config import settings
from bot.handlers.works import MANUAL_HINT, _download_video
from bot.projects import PROJECTS, add_work_pk, count_project_works, is_member
from bot.states import PapKids

logger = logging.getLogger(__name__)
router = Router(name="papkids")

P = PROJECTS["papkids"]


def _kb(done: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=f"✅ Готово ({done}/{P.limit})", callback_data="pk:done")]]
    )


@router.callback_query(F.data == "pk:add")
async def pk_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    if not await is_member(call.from_user.id, "papkids"):
        await call.answer("Проект недоступен", show_alert=True)
        return
    await state.set_state(PapKids.collecting)
    n = await count_project_works(call.from_user.id, "papkids")
    if n >= P.limit:
        await bot.send_message(call.message.chat.id, f"У тебя уже {P.limit} роликов — этого достаточно 👌")
        await state.clear()
        return
    await bot.send_message(call.message.chat.id, P.intro, reply_markup=_kb(n))


async def _accept_video(bot: Bot, tg_id: int, chat_id: int, file_id: str) -> int:
    """Сохранить ролик, СРАЗУ отправить клиенту, обновить таблицу. → новый счётчик."""
    n = await add_work_pk(tg_id, file_id)
    await _forward_to_client(bot, tg_id, file_id)
    await _update_client_sheet(tg_id, n)
    return n


@router.message(PapKids.collecting, F.video | F.document | F.animation)
async def pk_collect_file(message: Message, state: FSMContext, bot: Bot):
    obj = message.video or message.document or message.animation
    mime = (getattr(obj, "mime_type", "") or "").lower()
    if message.document and "video" not in mime:
        await message.answer("Это не видео 🙈 Пришли ролик видеофайлом.")
        return
    n = await _accept_video(bot, message.from_user.id, message.chat.id, obj.file_id)
    await _after(message, state, n)


@router.message(PapKids.collecting, F.text.contains("http"))
async def pk_collect_link(message: Message, state: FSMContext, bot: Bot):
    from bot.reach import extract_urls

    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer("Не вижу ссылку 🙈 Пришли ссылку на ролик или сам файл.")
        return
    n = await count_project_works(message.from_user.id, "papkids")
    if n >= P.limit:
        await state.clear()
        await message.answer(f"У тебя уже {P.limit} роликов — этого достаточно 👌")
        return

    urls = urls[: P.limit - n]
    plural = "ролик" if len(urls) == 1 else "ролики"
    wait = await message.answer(f"⏳ Скачиваю {plural}, это займёт немного времени…")
    failed: list[str] = []
    for url in urls:
        with tempfile.TemporaryDirectory() as tmp:
            path, err = await _download_video(url, tmp)
            if err:
                logger.warning("papkids: не скачал %s — %s", url[:60], err)
                failed.append(err)
                continue
            try:
                sent = await bot.send_video(
                    message.chat.id, FSInputFile(path), supports_streaming=True,
                    caption="✅ Забрал этот ролик",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("papkids: не залил видео: %s", e)
                failed.append("не загрузилось в Telegram")
                continue
            finally:
                if os.path.exists(path):
                    os.remove(path)
        n = await _accept_video(bot, message.from_user.id, message.chat.id, sent.video.file_id)

    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass
    if failed:
        await message.answer(
            f"😔 Не получилось скачать: {len(failed)} из {len(urls)} ({failed[0]}).\n\n{MANUAL_HINT}"
        )
    if not failed:
        await _after(message, state, n)


async def _after(message: Message, state: FSMContext, n: int) -> None:
    if n >= P.limit:
        await state.clear()
        await message.answer(
            f"✅ Отлично, собрал {P.limit} роликов — этого достаточно!\n"
            "Клиент их посмотрит, дальше сообщим по оплате 🚀"
        )
        return
    await message.answer(
        f"➕ Принял ({n}/{P.limit}). Присылай ещё или жми «Готово».", reply_markup=_kb(n)
    )


@router.callback_query(PapKids.collecting, F.data == "pk:done")
async def pk_done(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.clear()
    n = await count_project_works(call.from_user.id, "papkids")
    if not n:
        await bot.send_message(call.message.chat.id, "Ок, вернёшься позже — проект в «📁 Мои проекты».")
        return
    await bot.send_message(
        call.message.chat.id,
        f"✅ Сохранил ролики: {n}. Спасибо!\nКлиент их посмотрит 🚀",
    )


# ── Куда уходят ролики ─────────────────────────────────────────────────────────
async def _forward_to_client(bot: Bot, tg_id: int, file_id: str) -> None:
    """Ролик СРАЗУ в группу клиента (в отдельную тему). Пока группа не задана —
    складываем админам, чтобы ничего не потерялось на этапе настройки."""
    from bot.utils.db_helpers import get_creator_by_tg_id

    creator = await get_creator_by_tg_id(tg_id)
    name = (getattr(creator, "full_name", None) if creator else None) or f"id {tg_id}"
    caption = f"🧸 PapKids · {name}"

    if settings.papkids_group_id:
        kwargs = {"caption": caption, "supports_streaming": True}
        if settings.papkids_topic_id:
            kwargs["message_thread_id"] = int(settings.papkids_topic_id)
        try:
            await bot.send_video(int(settings.papkids_group_id), file_id, **kwargs)
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("papkids: не отправил в группу клиента: %s", e)
    # fallback — админам
    for admin_id in settings.admin_ids:
        try:
            await bot.send_video(admin_id, file_id, caption=f"{caption}\n(группа клиента ещё не настроена)", supports_streaming=True)
        except Exception:  # noqa: BLE001
            pass


async def _update_client_sheet(tg_id: int, count: int) -> None:
    """Счётчик в клиентскую таблицу PapKids (имя + id + число роликов; без логинов)."""
    if not settings.papkids_sheet_id:
        return
    from bot.sheets import SheetsError, sheets_client
    from bot.utils.db_helpers import get_creator_by_tg_id

    creator = await get_creator_by_tg_id(tg_id)
    name = (getattr(creator, "full_name", None) if creator else None) or ""
    try:
        await sheets_client.papkids_update(settings.papkids_sheet_id, tg_id, name, count)
    except SheetsError as e:  # noqa: BLE001
        logger.warning("papkids_update failed for %s: %s", tg_id, e)
