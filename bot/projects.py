"""
Клиентские мини-проекты (PapKids и др.): участник загружает N роликов в бота, они
уходят в группу клиента + счётчик в клиентскую таблицу.

Проще пайплайна «Сингл» (нет оффер/срок/оплата): только сбор роликов у заранее
вшитого списка участников. Отображается в «Мои проекты» ТОЛЬКО у участников.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, select

from bot.database import get_session
from bot.models import CreatorWork, ProjectMember

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Project:
    code: str
    title: str          # как показываем креатору
    emoji: str
    limit: int          # сколько роликов максимум
    add_cb: str         # callback кнопки «добавить ролик»
    intro: str          # текст-приглашение на шаге сбора


PROJECTS: dict[str, Project] = {
    "papkids": Project(
        code="papkids",
        title="PapKids",
        emoji="🧸",
        limit=5,
        add_cb="pk:add",
        intro=(
            "🧸 <b>Проект PapKids</b>\n\n"
            "Сними и загрузи <b>до 5 роликов</b> с продукцией PapKids. Их посмотрит "
            "клиент — чем сильнее ролики, тем выше шанс на оплату и новые заказы 🚀\n\n"
            "<b>Как прислать — на выбор:</b>\n"
            "🔗 <b>Ссылками</b> — скинь ссылки на Reels в одном сообщении "
            "(1 ссылка — 1 строка), я сам скачаю\n"
            "📹 <b>Файлами</b> — если роликов нет в соцсетях\n\n"
            "Как закончишь — жми «Готово»."
        ),
    ),
}


async def is_member(tg_id: int, project: str) -> bool:
    async with get_session() as s:
        row = (
            await s.execute(
                select(ProjectMember.id).where(
                    ProjectMember.tg_id == tg_id, ProjectMember.project == project
                )
            )
        ).first()
    return row is not None


async def add_member(tg_id: int, project: str) -> bool:
    """Вшить участника. True — добавлен, False — уже был."""
    async with get_session() as s:
        exists = (
            await s.execute(
                select(ProjectMember.id).where(
                    ProjectMember.tg_id == tg_id, ProjectMember.project == project
                )
            )
        ).first()
        if exists:
            return False
        s.add(ProjectMember(tg_id=tg_id, project=project))
        await s.commit()
        return True


async def member_projects(tg_id: int) -> list[Project]:
    """Проекты, в которых состоит креатор (для «Мои проекты»)."""
    async with get_session() as s:
        codes = (
            await s.execute(select(ProjectMember.project).where(ProjectMember.tg_id == tg_id))
        ).scalars().all()
    return [PROJECTS[c] for c in codes if c in PROJECTS]


async def add_work_pk(tg_id: int, file_id: str | None, url: str | None = None) -> int:
    """Добавить ролик в проект PapKids (лимит из конфига). → новый счётчик."""
    from bot.handlers.works import add_work

    p = PROJECTS["papkids"]
    return await add_work(tg_id, file_id=file_id, url=url, source="self", project=p.code, limit=p.limit)


async def count_project_works(tg_id: int, project: str) -> int:
    async with get_session() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(CreatorWork)
                    .where(CreatorWork.tg_id == tg_id, CreatorWork.project == project)
                )
            ).scalar()
            or 0
        )


def project_card(p: Project, done: int):
    """(текст, [(подпись, callback)]) карточки проекта в «Мои проекты»."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    text = (
        f"{p.emoji} <b>Проект {p.title}</b>\n\n"
        f"Загружено роликов: <b>{done} / {p.limit}</b>\n"
    )
    if done >= p.limit:
        text += "\n✅ Готово — все ролики приняты, спасибо! Клиент их посмотрит."
        rows = []
    else:
        text += "\nЗагрузи ролики с продукцией — их увидит клиент."
        rows = [[InlineKeyboardButton(text="➕ Добавить ролик", callback_data=p.add_cb)]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
