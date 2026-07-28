"""
Размещение креатора в канале @ugc_creatory по кнопке админа.

Когда у креатора есть И фото (шаг анкеты), И работы (видео), бот шлёт админам карточку
с кнопками [✅ Разместить] [❌ Нахер] (см. works.publish_works → send_placement_prompt).

«Разместить» → бот-админ канала собирает альбом из своих file_id (2 фото + до 4 видео)
и постит в канал МГНОВЕННО. Медиа шлём оригинальными file_id — Telegram хранит исходные
атрибуты, поэтому ни сплющивания, ни чёрных обложек (в отличие от перезаливки Telethon).
Ссылка на портфолио — прямая карточка Тильды (creator.tilda_uid) или общий каталог, если
карточки ещё нет; тогда ставим site_queued=True (локальный ночной воркер создаёт карточку).

«Нахер» → placement=rejected, ничего не публикуем (отсев пустых/спам-заявок).
"""
from __future__ import annotations

import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, InputMediaVideo
from sqlalchemy import select

from bot.cards import CATALOG_URL, build_creator_card, short_name
from bot.config import settings
from bot.database import get_session
from bot.keyboards import placement_keyboard
from bot.models import Creator, CreatorPhoto, CreatorWork
from bot.sheets import SheetsError, sheets_client
from bot.utils.video import make_thumb, probe_dims, to_playable_mp4

logger = logging.getLogger(__name__)
router = Router(name="placement")

CARD_URL = "https://packman-prod.ru/ugc_creators/tproduct/{uid}"
MAX_PHOTOS = 2
MAX_VIDEOS = 4
MAX_DL = 20 * 1024 * 1024  # Bot API качает file_id только до 20МБ


async def _video_ids(bot: Bot, works: list) -> list[str]:
    """file_id работ, готовые к альбому: КАЖДОЕ видео перезаливаем с корректными размерами
    (ffprobe, не квадрат) + обложкой (ffmpeg, не чёрная), новый file_id кэшируем в БД (флаг
    normalized). Нормализуем ВСЕ, а не только document: Telegram у исходного video-file_id
    часто держит кривые (320×320) метаданные даже для портретного файла → квадрат в альбоме.
    Уже normalized → отдаём как есть. >20МБ Bot API не скачает → отдаём оригинал (редко)."""
    out: list[str] = []
    for w in works[:MAX_VIDEOS]:
        if not w.file_id:
            continue
        if w.normalized:  # уже перезалито корректно
            out.append(w.file_id)
            continue
        try:
            f = await bot.get_file(w.file_id)
        except Exception:  # noqa: BLE001
            out.append(w.file_id)
            continue
        if (f.file_size or 0) > MAX_DL:
            logger.warning("work %s: >20МБ, не нормализуем (нужен Telethon)", w.id)
            out.append(w.file_id)
            continue
        try:
            data = (await bot.download_file(f.file_path)).read()
            # ТВЁРДОЕ ПРАВИЛО (docs/CHANNEL_POSTING.md): H.264 (иначе VP9/HEVC не играют) +
            # обложка (не чёрная) + размеры (не квадрат).
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                tf.write(data)
                tmp = tf.name
            out = tmp + ".h264.mp4"
            try:
                if to_playable_mp4(tmp, out):
                    data = open(out, "rb").read()  # H.264 + faststart
                    src = out
                else:
                    src = tmp  # фолбэк: исходник как есть
                dur, vw, vh = probe_dims(src)
                thumb = make_thumb(src)
            finally:
                for p in (tmp, out):
                    if os.path.exists(p):
                        os.unlink(p)
            kwargs: dict = {"supports_streaming": True, "disable_notification": True}
            if vw and vh:
                kwargs.update(width=vw, height=vh, duration=dur)
            if thumb:
                kwargs["thumbnail"] = BufferedInputFile(thumb, "t.jpg")
            m = await bot.send_video(settings.admin_ids[0], BufferedInputFile(data, "v.mp4"), **kwargs)
            vid = m.video.file_id if m.video else None
            try:
                await bot.delete_message(settings.admin_ids[0], m.message_id)
            except Exception:  # noqa: BLE001
                pass
            if vid:
                async with get_session() as s:
                    ww = await s.get(CreatorWork, w.id)
                    if ww:
                        ww.file_id = vid
                        ww.normalized = True
                        await s.commit()
                out.append(vid)
            else:
                out.append(w.file_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("normalize work %s failed: %s", w.id, e)
            out.append(w.file_id)
    return out


async def _load(tg_id: int):
    async with get_session() as s:
        creator = (
            await s.execute(select(Creator).where(Creator.tg_id == tg_id))
        ).scalar_one_or_none()
        photos = (
            await s.execute(
                select(CreatorPhoto)
                .where(CreatorPhoto.tg_id == tg_id, CreatorPhoto.kind == "photo")
                .order_by(CreatorPhoto.created_at)
            )
        ).scalars().all()
        works = (
            await s.execute(
                select(CreatorWork)
                .where(CreatorWork.tg_id == tg_id, CreatorWork.project == "base")
                .order_by(CreatorWork.position)
            )
        ).scalars().all()
    return creator, photos, works


async def _set(tg_id: int, **fields) -> None:
    async with get_session() as s:
        c = (await s.execute(select(Creator).where(Creator.tg_id == tg_id))).scalar_one_or_none()
        if c:
            for k, v in fields.items():
                setattr(c, k, v)
            await s.commit()


async def send_placement_prompt(bot: Bot, tg_id: int) -> None:
    """Прислать админам карточку-заявку с кнопками. Один раз (placement=None → pending)."""
    creator, photos, works = await _load(tg_id)
    if not creator or not photos or not works:
        return
    if creator.placement:  # уже pending/placed/rejected — не спамим повторными работами
        return
    await _set(tg_id, placement="pending")
    ph = photos[:MAX_PHOTOS]
    vids = await _video_ids(bot, works)  # нормализуем document→video (и кэшируем)
    # Альбом-превью ровно того, что уйдёт в канал — админ видит контент и решает.
    media: list = [InputMediaPhoto(media=p.file_id) for p in ph]
    media += [InputMediaVideo(media=v) for v in vids]
    handle = (creator.username and f"@{creator.username}") or creator.telegram_contact or "—"
    text = (
        f"👆 <b>{short_name(creator.full_name)}</b> · 📍 {creator.city or '—'} · "
        f"🎬 {creator.categories or '—'}\n"
        f"👤 {handle} · id {tg_id} · фото {len(ph)} + видео {len(vids)}\n\n"
        f"Разместить в канал @ugc_creatory и на сайт?"
    )
    for admin_id in settings.admin_ids:
        try:
            if media:
                await bot.send_media_group(admin_id, media)
            await bot.send_message(
                admin_id, text, reply_markup=placement_keyboard(tg_id), parse_mode="HTML"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("placement prompt to %s failed: %s", admin_id, e)


@router.callback_query(F.data.startswith("place:"))
async def do_place(call: CallbackQuery, bot: Bot) -> None:
    if not settings.is_admin(call.from_user.id):
        await call.answer("Только для админов", show_alert=True)
        return
    tg_id = int(call.data.split(":", 1)[1])
    creator, photos, works = await _load(tg_id)
    if not creator:
        await call.answer("Креатор не найден", show_alert=True)
        return
    if creator.placement == "placed":
        await call.answer("Уже размещён")
        return
    ph = photos[:MAX_PHOTOS]
    vids = await _video_ids(bot, works)  # обычно уже видео (кэш из превью) → без перезаливки
    if not ph and not vids:
        await call.answer("Нет валидного медиа", show_alert=True)
        return

    card_url = CARD_URL.format(uid=creator.tilda_uid) if creator.tilda_uid else CATALOG_URL
    cap = build_creator_card(creator.full_name, creator.city, creator.categories, portfolio_url=card_url)

    media: list = []
    for p in ph:
        media.append(InputMediaPhoto(
            media=p.file_id, caption=cap if not media else None, parse_mode="HTML"))
    for v in vids:
        media.append(InputMediaVideo(
            media=v, caption=cap if not media else None, parse_mode="HTML"))

    try:
        sent = await bot.send_media_group(int(settings.channel_id), media)
    except Exception as e:  # noqa: BLE001
        logger.exception("send to channel failed for %s", tg_id)
        await call.answer(f"Ошибка постинга: {e}", show_alert=True)
        return

    msg_id = sent[0].message_id
    queued = creator.tilda_uid is None
    await _set(tg_id, placement="placed", channel_msg_id=msg_id, site_queued=queued)

    channel_url = f"https://t.me/ugc_creatory/{msg_id}"
    try:
        await sheets_client.report_write([{
            "chat_id": tg_id, "channel_url": channel_url,
            "card_url": card_url if creator.tilda_uid else "",
        }])
    except SheetsError as e:  # noqa: BLE001
        logger.warning("report_write failed for %s: %s", tg_id, e)

    note = "✅ Размещено в канал" + (" · сайт в очереди" if queued else " + сайт")
    try:
        await call.message.edit_text(f"{note}\n{channel_url}")
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Готово")


@router.callback_query(F.data.startswith("reject:"))
async def do_reject(call: CallbackQuery, bot: Bot) -> None:
    if not settings.is_admin(call.from_user.id):
        await call.answer("Только для админов", show_alert=True)
        return
    tg_id = int(call.data.split(":", 1)[1])
    await _set(tg_id, placement="rejected")
    try:
        await call.message.edit_text("❌ Отклонён — не размещаем")
    except Exception:  # noqa: BLE001
        pass
    await call.answer("Ок")
