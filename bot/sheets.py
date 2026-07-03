"""
Клиент для Google Apps Script webhook (см. Код.gs в спредшите).
Один HTTP-эндпоинт, action в теле запроса решает, что делать на стороне таблицы:
  - action="register" -> старое поведение, appendRow + "Есть в боте"="да" (уже было)
  - action="lookup"    -> doLookup_: fuzzy-поиск креатора по telegram/имени
  - action="update"    -> doUpdateRow_: правит существующую строку
  - action="stats"     -> doStats_: количество креаторов всего/в боте/не в боте
  - action="brands"    -> doBrands_: активные карточки брендов

Таблица — источник правды для дедупа (см. project_map_ugc_bot.md, решение "вариант А":
lookup по Google Sheet вместо persistent volume на Railway, т.к. локальная SQLite
на Railway эфемерна и обнуляется при каждом передеплое).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from bot.config import settings

logger = logging.getLogger(__name__)


class SheetsError(Exception):
    """Ошибка обращения к Google Sheets webhook (сеть, таймаут, ok:false)."""


@dataclass
class LookupResult:
    found: bool
    needs_confirmation: bool = False
    confidence: float = 0.0
    row: int | None = None
    confirm_field: str | None = None
    confirm_value: str | None = None
    data: dict[str, Any] | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LookupResult":
        if not payload.get("found"):
            return cls(found=False)
        return cls(
            found=True,
            needs_confirmation=bool(payload.get("needsConfirmation")),
            confidence=float(payload.get("confidence") or 0),
            row=payload.get("row"),
            confirm_field=payload.get("confirmField"),
            confirm_value=payload.get("confirmValue"),
            data=payload.get("data") or {},
        )


@dataclass
class StatsResult:
    total_creators: int = 0
    in_bot: int = 0
    not_in_bot: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "StatsResult":
        return cls(
            total_creators=int(payload.get("totalCreators") or 0),
            in_bot=int(payload.get("inBot") or 0),
            not_in_bot=int(payload.get("notInBot") or 0),
        )


@dataclass
class Brand:
    id: str
    title: str
    description: str
    category: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Brand":
        return cls(
            id=str(payload.get("id") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            category=str(payload.get("category") or ""),
        )


class SheetsClient:
    """Тонкая обёртка над одним POST-эндпоинтом Apps Script."""

    def __init__(self, webhook_url: str | None = None, secret: str | None = None, timeout: int = 8):
        self.webhook_url = webhook_url or settings.sheets_webhook_url
        self.secret = secret or settings.sheets_webhook_secret
        # Жестче таймаут: 8 сек (было 10, еще жестче connect/read)
        self.timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=3, sock_read=3)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.webhook_url:
            raise SheetsError("SHEETS_WEBHOOK_URL не задан")
        body = {"secret": self.secret, **payload}
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(self.webhook_url, json=body) as resp:
                    text = await resp.text()
                    if resp.status != 200:
                        raise SheetsError(f"HTTP {resp.status}: {text[:200]}")
        except aiohttp.ClientError as e:
            raise SheetsError(f"Сетевая ошибка: {e}") from e
        import json as _json

        try:
            data = _json.loads(text)
        except ValueError as e:
            raise SheetsError(f"Не JSON в ответе: {text[:200]}") from e

        if isinstance(data, dict) and data.get("ok") is False:
            raise SheetsError(str(data.get("error") or "unknown error"))
        return data

    # --- существующая регистрация (без изменений в контракте) ---
    async def register_creator(self, form: dict[str, Any], chat_id: int) -> dict[str, Any]:
        payload = {"action": "register", "chat_id": chat_id, **form}
        return await self._post(payload)

    # --- новые действия ---
    async def lookup(self, telegram: str, full_name: str) -> LookupResult:
        payload = await self._post({"action": "lookup", "telegram": telegram, "full_name": full_name})
        return LookupResult.from_payload(payload)

    async def update_row(self, row: int, fields: dict[str, Any], chat_id: int) -> bool:
        payload = await self._post(
            {"action": "update", "row": row, "fields": fields, "chat_id": chat_id}
        )
        return bool(payload.get("ok"))

    async def stats(self) -> StatsResult:
        payload = await self._post({"action": "stats"})
        return StatsResult.from_payload(payload)

    async def brands(self) -> list[Brand]:
        payload = await self._post({"action": "brands"})
        items = payload if isinstance(payload, list) else payload.get("items", [])
        return [Brand.from_payload(item) for item in items]


sheets_client = SheetsClient()
