"""
Сбор «лучших работ» креатора — материал для карточки в канале @ugc_creatory.

Почему отдельный поток, а не шаг анкеты: анкета — 10 шагов с нумерацией, добавление
11-го шага сбило бы её и ударило по конверсии регистрации. Здесь креатор в любой момент
досылает ролики, а фото у нас уже есть с шага «фото» (см. CreatorPhoto).

Файлы принимаем именно ФАЙЛАМИ (не ссылками): тогда они лежат в Telegram и карточку
можно собрать репостом — ничего не надо скачивать со сторонних площадок.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import delete, func, select

from bot.database import get_session
from bot.models import CreatorWork
from bot.states import Works

logger = logging.getLogger(__name__)
router = Router(name="works")

MAX_WORKS = 4

ASK_TEXT = (
    "🎬 <b>Твои лучшие работы</b>\n\n"
    f"Пришли {MAX_WORKS} своих лучших UGC-ролика (идеально с демонстрацией "
    "продукта/услуги). Если таких нет, то просто лучшие рилсы.\n\n"
    "Их увидят бренды в нашей базе креаторов — чем сильнее работы, тем чаще зовут "
    "на проекты 🚀\n\n"
    "<b>Как прислать — на выбор:</b>\n"
    "🔗 <b>Ссылками</b> — просто скинь ссылки на свои Reels в одном сообщении "
    "(1 ссылка — 1 строка), я сам скачаю\n"
    "📹 <b>Файлами</b> — если роликов нет в соцсетях\n\n"
    "Как закончишь — жми «Готово»."
)

# Инструкция-подстраховка: показываем, только если скачать по ссылке не вышло.
# ВАЖНО: ссылку с kkclip НЕ открывают в браузере — её отправляют сообщением в Telegram,
# он сам разворачивает предпросмотр с видео, откуда файл и сохраняют.
MANUAL_HINT = (
    "🔗 <b>Как быстро скачать видео из Instagram</b>\n\n"
    "1️⃣ Скопируй полную ссылку на свой ролик:\n"
    "<code>https://www.instagram.com/p/DaM-ST1Av6-/</code>\n\n"
    "2️⃣ Замени в ней <b>instagram</b> на <b>kkclip</b> — больше в ссылке ничего "
    "не меняй:\n<code>https://www.kkclip.com/p/DaM-ST1Av6-/</code>\n\n"
    "3️⃣ <b>Отправь эту ссылку сообщением в Telegram</b> — в любой чат, можно себе "
    "в «Избранное». Переходить по ней никуда не надо!\n\n"
    "4️⃣ В сообщении развернётся предпросмотр с видео → нажми на него правой кнопкой "
    "(на телефоне — долгим нажатием) → <b>«Сохранить как…»</b>\n\n"
    "5️⃣ Пришли скачанный файл сюда 👇"
)


def _kb(done: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"✅ Готово ({done}/{MAX_WORKS})", callback_data="works:done")]]
    if done:
        rows.append([InlineKeyboardButton(text="🗑 Очистить и прислать заново", callback_data="works:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def count_works(tg_id: int) -> int:
    async with get_session() as s:
        return int(
            (await s.execute(select(func.count()).select_from(CreatorWork).where(CreatorWork.tg_id == tg_id))).scalar()
            or 0
        )


async def add_work(tg_id: int, file_id: str | None, url: str | None = None, source: str = "self") -> int:
    """→ сколько работ стало у креатора (не больше MAX_WORKS)."""
    async with get_session() as s:
        n = int(
            (await s.execute(select(func.count()).select_from(CreatorWork).where(CreatorWork.tg_id == tg_id))).scalar()
            or 0
        )
        if n >= MAX_WORKS:
            return n
        s.add(CreatorWork(tg_id=tg_id, file_id=file_id, url=url, source=source, position=n))
        await s.commit()
        return n + 1


@router.callback_query(F.data == "works:add")
async def works_start(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.set_state(Works.collecting)
    n = await count_works(call.from_user.id)
    await bot.send_message(call.message.chat.id, ASK_TEXT, reply_markup=_kb(n))


@router.callback_query(F.data == "works:reset")
async def works_reset(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer("Очистил")
    async with get_session() as s:
        await s.execute(delete(CreatorWork).where(CreatorWork.tg_id == call.from_user.id))
        await s.commit()
    await state.set_state(Works.collecting)
    await bot.send_message(call.message.chat.id, "🗑 Готово, присылай заново.", reply_markup=_kb(0))


@router.message(Works.collecting, F.video | F.document | F.animation)
async def works_collect(message: Message, state: FSMContext, bot: Bot):
    obj = message.video or message.document or message.animation
    mime = (getattr(obj, "mime_type", "") or "").lower()
    if message.document and "video" not in mime:
        await message.answer("Это не видео 🙈 Пришли ролик видеофайлом.")
        return
    n = await add_work(message.from_user.id, file_id=obj.file_id)
    if n >= MAX_WORKS:
        await state.clear()
        await message.answer(
            f"✅ Отлично, собрал {MAX_WORKS} работы — этого достаточно!\n"
            "Добавлю тебя в базу креаторов для брендов 🚀"
        )
        await publish_works(bot, message.from_user.id)
        return
    await message.answer(f"➕ Принял ({n}/{MAX_WORKS}). Присылай ещё или жми «Готово».", reply_markup=_kb(n))


async def _download_video(url: str, dest_dir: str) -> tuple[str | None, str | None]:
    """Скачать ролик по ссылке через yt-dlp → (путь к файлу, ошибка).

    Работает для Reels/TikTok/VK/YouTube. Ограничиваем размер: Telegram не даёт боту
    заливать больше 50 МБ, поэтому просим формат до ~45 МБ."""
    import asyncio
    import glob
    import os

    out = os.path.join(dest_dir, "w_%(id)s.%(ext)s")
    cmd = [
        "yt-dlp", "--no-warnings", "--no-playlist", "--socket-timeout", "30",
        "-f", "best[filesize<45M][ext=mp4]/best[ext=mp4]/best",
        "-o", out, url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=180)
    except asyncio.TimeoutError:
        return None, "слишком долго качается"
    except FileNotFoundError:
        return None, "yt-dlp не установлен"
    if proc.returncode != 0:
        return None, (err.decode("utf-8", "ignore").strip().splitlines() or ["не смог скачать"])[-1][:120]
    files = sorted(glob.glob(os.path.join(dest_dir, "w_*")), key=os.path.getmtime)
    if not files:
        return None, "файл не появился"
    path = files[-1]
    if os.path.getsize(path) > 49 * 1024 * 1024:
        os.remove(path)
        return None, "ролик тяжелее 50 МБ"
    return path, None


@router.message(Works.collecting, F.text.contains("http"))
async def works_by_link(message: Message, state: FSMContext, bot: Bot):
    """Креатор прислал ССЫЛКУ — пробуем скачать сами. Не вышло → показываем инструкцию."""
    import os
    import tempfile

    from aiogram.types import FSInputFile

    from bot.reach import extract_urls

    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer("Не вижу ссылку 🙈 Пришли ссылку на ролик или сам файл.")
        return

    n = await count_works(message.from_user.id)
    if n >= MAX_WORKS:
        await state.clear()
        await message.answer(f"У меня уже {MAX_WORKS} твоих работы — этого достаточно 👌")
        return

    urls = urls[: MAX_WORKS - n]  # берём столько, сколько влезает в лимит
    plural = "ролик" if len(urls) == 1 else "ролики"
    wait = await message.answer(f"⏳ Скачиваю {plural}, это займёт немного времени…")
    failed: list[str] = []

    for url in urls:
        with tempfile.TemporaryDirectory() as tmp:
            path, err = await _download_video(url, tmp)
            if err:
                logger.warning("works: не скачал %s — %s", url[:60], err)
                failed.append(err)
                continue
            try:
                sent = await bot.send_video(
                    message.chat.id, FSInputFile(path), supports_streaming=True,
                    caption="✅ Забрал этот ролик",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("works: не залил видео: %s", e)
                failed.append("не загрузилось в Telegram")
                continue
            finally:
                if os.path.exists(path):
                    os.remove(path)
        n = await add_work(message.from_user.id, file_id=sent.video.file_id, url=url, source="self")

    try:
        await wait.delete()
    except Exception:  # noqa: BLE001
        pass

    if failed:
        await message.answer(
            f"😔 Не получилось скачать: {len(failed)} из {len(urls)} ({failed[0]}).\n\n{MANUAL_HINT}"
        )
    if n >= MAX_WORKS:
        await state.clear()
        await message.answer(
            f"✅ Отлично, собрал {MAX_WORKS} работы — этого достаточно!\n"
            "Добавлю тебя в базу креаторов для брендов 🚀"
        )
        await publish_works(bot, message.from_user.id)
        return
    if not failed:
        await message.answer(
            f"➕ Принял ({n}/{MAX_WORKS}). Присылай ещё или жми «Готово».", reply_markup=_kb(n)
        )


@router.callback_query(Works.collecting, F.data == "works:done")
async def works_done(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    await state.clear()
    n = await count_works(call.from_user.id)
    if not n:
        await bot.send_message(
            call.message.chat.id,
            "Ок, вернёмся к этому позже — работы можно добавить в «🧾 Моя анкета».",
        )
        return
    await bot.send_message(
        call.message.chat.id,
        f"✅ Сохранил работы: {n}. Спасибо!\nБренды увидят их в нашей базе креаторов 🚀",
    )
    await publish_works(bot, call.from_user.id)


async def publish_works(bot: Bot, tg_id: int) -> None:
    """Работы → в рабочую группу (там их видно глазами) + счётчик в колонку «Работы»
    листа креаторов. Без этого ролики оседали только в БД бота и наружу не попадали."""
    from datetime import datetime, timedelta

    from bot.config import settings
    from bot.sheets import SheetsError, sheets_client
    from bot.utils.db_helpers import get_creator_by_tg_id

    async with get_session() as s:
        works = (
            await s.execute(
                select(CreatorWork).where(CreatorWork.tg_id == tg_id).order_by(CreatorWork.position)
            )
        ).scalars().all()
    if not works:
        return

    creator = await get_creator_by_tg_id(tg_id)
    name = (getattr(creator, "full_name", None) if creator else None) or "—"
    tg = (getattr(creator, "telegram_contact", None) if creator else None) or "—"

    # 1) в рабочую группу (как фото; если группа не задана — админам)
    targets = [settings.photos_chat_id] if settings.photos_chat_id else list(settings.admin_ids)
    for target in targets:
        try:
            await bot.send_message(target, f"🎬 Работы креатора {name} ({tg}, id {tg_id}) — {len(works)} шт.")
            for w in works:
                if w.file_id:
                    await bot.send_video(target, w.file_id, supports_streaming=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("send works to %s failed: %s", target, e)

    # 2) счётчик в таблицу креаторов
    when = (datetime.utcnow() + timedelta(hours=3)).strftime("%d.%m.%Y")
    try:
        await sheets_client.works_update(tg_id, f"{len(works)} ролика(ов) · {when}")
    except SheetsError as e:  # noqa: BLE001
        logger.warning("works_update failed for %s: %s", tg_id, e)
