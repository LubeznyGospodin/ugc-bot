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


@dataclass
class Application:
    """Один отклик креатора на бренд со статусом рассмотрения."""

    brand_title: str
    status: str  # "на рассмотрении" | "отказ" | "оффер"
    date: str = ""
    reason: str = ""  # причина (обычно для статуса "отказ")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Application":
        return cls(
            brand_title=str(payload.get("brand_title") or payload.get("brand") or ""),
            status=(str(payload.get("status") or "на рассмотрении")).strip().lower(),
            date=str(payload.get("date") or ""),
            reason=str(payload.get("reason") or ""),
        )


class SheetsClient:
    """Тонкая обёртка над одним POST-эндпоинтом Apps Script."""

    def __init__(self, webhook_url: str | None = None, secret: str | None = None, timeout: int = 25):
        self.webhook_url = webhook_url or settings.sheets_webhook_url
        self.secret = secret or settings.sheets_webhook_secret
        # Apps Script на update_row (читает лист + пишет ячейки) нередко отвечает 3–8с,
        # а под нагрузкой/холодным стартом дольше. Прежние sock_read=3с роняли сохранение
        # анкеты КАЖДЫЙ раз. Даём нормальный запас: read до 20с, весь запрос до 25с.
        self.timeout = aiohttp.ClientTimeout(total=timeout, sock_connect=5, sock_read=20)

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.webhook_url:
            raise SheetsError("SHEETS_WEBHOOK_URL не задан")
        body = {"secret": self.secret, **payload}
        # Ретрай: холодный старт Apps Script часто роняет первый запрос по таймауту.
        # Вторая попытка идёт уже по «прогретому» эндпоинту — так «работает через раз»
        # превращается в «работает с первого раза» для пользователя.
        import asyncio as _asyncio

        text = None
        last_exc: Exception | None = None
        attempts = 3
        for attempt in range(attempts):
            try:
                async with aiohttp.ClientSession(timeout=self.timeout) as session:
                    async with session.post(self.webhook_url, json=body) as resp:
                        text = await resp.text()
                        if resp.status != 200:
                            raise SheetsError(f"HTTP {resp.status}: {text[:200]}")
                break
            except (aiohttp.ClientError, _asyncio.TimeoutError) as e:
                last_exc = e
                if attempt < attempts - 1:
                    await _asyncio.sleep(0.5 * (attempt + 1))  # 0.5с, 1.0с
                    continue
                raise SheetsError(f"Сетевая ошибка: {e}") from e
        if text is None:
            raise SheetsError(f"Сетевая ошибка: {last_exc}")
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
    async def lookup(self, telegram: str, full_name: str, chat_id: int | None = None) -> LookupResult:
        """Опознание строки креатора по КОНСТАНТАМ: chat_id (надёжнее всего — работает и
        после смены хендла), затем телеграм-хендл. Имя в решении не участвует: в строке
        может стоять имя креатора, а в аккаунте — совсем другое."""
        payload = await self._post(
            {
                "action": "lookup",
                "telegram": telegram,
                "full_name": full_name,
                "chat_id": chat_id or "",
            }
        )
        return LookupResult.from_payload(payload)

    async def update_row(
        self, row: int, fields: dict[str, Any], chat_id: int, telegram: str | None = None
    ) -> dict[str, Any]:
        """Обновить строку креатора (action=update). Строка опознаётся по Chat ID + сверка
        телеграма — поэтому telegram передаём всегда, когда знаем: без него сервер не сможет
        подтвердить личность перед записью. Номер строки — только fallback первичной привязки.
        Возвращает {ok, updated, notfound, identity_mismatch, row}. updated=False — строку не
        нашли/не подтвердили, писать не стали (чужую строку не трогаем) → вызывающий добавит новую."""
        return await self._post(
            {
                "action": "update",
                "row": row,
                "fields": fields,
                "chat_id": chat_id,
                "telegram": telegram or "",
            }
        )

    async def single_update(self, chat_id: int, fields: dict[str, Any]) -> dict[str, Any]:
        """Записать поля пайплайна «Сингл» в строку отклика (лист «Отклики», по chat_id +
        бренд br1). fields: подтвердил / срок / ссылка. Новые колонки создаются в конце."""
        return await self._post({"action": "single_update", "chat_id": chat_id, "fields": fields})

    async def stats(self) -> StatsResult:
        payload = await self._post({"action": "stats"})
        return StatsResult.from_payload(payload)

    async def all_creators(self) -> list[dict[str, Any]]:
        """Все креаторы с заполненным Chat ID (для bulk-синка в БД)."""
        payload = await self._post({"action": "all_creators"})
        items = payload if isinstance(payload, list) else payload.get("items", [])
        return [i for i in items if isinstance(i, dict)]

    async def all_applications(self) -> list[dict[str, Any]]:
        """Все отклики (для bulk-синка в БД)."""
        payload = await self._post({"action": "all_applications"})
        items = payload if isinstance(payload, list) else payload.get("items", [])
        return [i for i in items if isinstance(i, dict)]

    async def reach_write(self, sheet_id: str, rows: list[dict], total: int) -> dict[str, Any]:
        """Записать охваты в клиентскую таблицу (openById в Apps Script)."""
        return await self._post(
            {"action": "reach_write", "sheet_id": sheet_id, "rows": rows, "total": total}
        )

    async def profile(self, chat_id: int) -> dict[str, Any] | None:
        """Живой профиль креатора из таблицы по Chat ID (action=profile).
        Apps Script читает строку по колонке «Chat ID» и отдаёт поля по ЗАГОЛОВКАМ
        (устойчиво к добавлению/удалению столбцов). None — если строки нет."""
        payload = await self._post({"action": "profile", "chat_id": chat_id})
        if isinstance(payload, dict) and payload.get("found"):
            return payload.get("data") or {}
        return None

    _brands_cache: "tuple[float, list[Brand]] | None" = None

    async def brands(self, ttl: float = 30.0) -> list[Brand]:
        # Короткий кэш: листание карточек и отклик не бьют по webhook повторно.
        # Бренды в таблице меняются редко, 30с задержки допустимы.
        import time as _time

        now = _time.monotonic()
        cached = SheetsClient._brands_cache
        if cached and (now - cached[0]) < ttl:
            return cached[1]
        payload = await self._post({"action": "brands"})
        items = payload if isinstance(payload, list) else payload.get("items", [])
        result = [Brand.from_payload(item) for item in items]
        SheetsClient._brands_cache = (now, result)
        return result

    async def apply(
        self,
        brand_id: str,
        brand_title: str,
        name: str,
        telegram: str,
        chat_id: int,
    ) -> dict[str, Any]:
        """Записать отклик на бренд — action=apply. Возвращает payload:
        {ok, duplicate, status, reason}. duplicate=True — отклик уже существует
        (тот же Chat ID + Бренд ID), тогда status/reason — текущие по нему."""
        return await self._post(
            {
                "action": "apply",
                "brand_id": brand_id,
                "brand_title": brand_title,
                "name": name,
                "telegram": telegram,
                "chat_id": chat_id,
            }
        )

    async def my_applications(self, chat_id: int) -> list[Application]:
        """Отклики конкретного креатора со статусом — action=my_applications,
        Apps Script читает вкладку "Отклики", фильтр по Chat ID."""
        payload = await self._post({"action": "my_applications", "chat_id": chat_id})
        items = payload if isinstance(payload, list) else payload.get("items", [])
        return [Application.from_payload(item) for item in items]


sheets_client = SheetsClient()
