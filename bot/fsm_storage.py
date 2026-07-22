"""
FSM-хранилище в Postgres вместо MemoryStorage.

Зачем: MemoryStorage живёт в процессе и стирается при каждом рестарте/деплое. Из-за
этого у человека посреди анкеты или догрузки ссылки «пропадал» шаг: следующее его
сообщение не попадало ни в один обработчик и молча терялось (см. фикс в
bot/handlers/single.py::single_loose_link). С Postgres диалог переживает передеплой.

Таблица создаётся через Base.metadata.create_all (см. bot/database.py::init_db).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy import delete, select

from bot.database import get_session
from bot.models import FSMRecord

logger = logging.getLogger(__name__)


def _key(key: StorageKey) -> str:
    """StorageKey → строковый первичный ключ."""
    return ":".join(
        str(x) for x in (
            key.bot_id, key.chat_id, key.user_id, key.thread_id or 0, key.destiny or "default",
        )
    )


class PostgresStorage(BaseStorage):
    """Состояние и data FSM в таблице fsm_records. Пустая запись (нет состояния и нет
    данных) удаляется — таблица не пухнет от завершённых диалогов."""

    async def _get(self, session, k: str) -> FSMRecord | None:
        return (await session.execute(select(FSMRecord).where(FSMRecord.key == k))).scalar_one_or_none()

    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        k = _key(key)
        value = state.state if isinstance(state, State) else state
        async with get_session() as s:
            row = await self._get(s, k)
            if row is None:
                if value is None:
                    return  # нечего хранить
                s.add(FSMRecord(key=k, state=value, data="{}", updated_at=datetime.utcnow()))
            else:
                row.state = value
                row.updated_at = datetime.utcnow()
                # состояние снято и данных нет → чистим запись
                if value is None and row.data in ("", "{}", None):
                    await s.execute(delete(FSMRecord).where(FSMRecord.key == k))
            await s.commit()

    async def get_state(self, key: StorageKey) -> str | None:
        async with get_session() as s:
            row = await self._get(s, _key(key))
            return row.state if row else None

    async def set_data(self, key: StorageKey, data: dict[str, Any]) -> None:
        k = _key(key)
        payload = json.dumps(data or {}, ensure_ascii=False, default=str)
        async with get_session() as s:
            row = await self._get(s, k)
            if row is None:
                if not data:
                    return
                s.add(FSMRecord(key=k, state=None, data=payload, updated_at=datetime.utcnow()))
            else:
                row.data = payload
                row.updated_at = datetime.utcnow()
                if not data and row.state is None:
                    await s.execute(delete(FSMRecord).where(FSMRecord.key == k))
            await s.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        async with get_session() as s:
            row = await self._get(s, _key(key))
        if not row or not row.data:
            return {}
        try:
            return json.loads(row.data)
        except (ValueError, TypeError):  # битый JSON не должен ронять диалог
            logger.warning("fsm: битые данные по ключу %s — сбрасываю", _key(key))
            return {}

    async def close(self) -> None:
        return None
