"""
Конфигурация бота. Значения читаются из переменных окружения (.env локально,
Railway Variables в проде). Ничего секретного здесь не хардкодится.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _normalize_db_url(raw: str | None) -> str:
    """Приводим DATABASE_URL к async-драйверу.
    Railway отдаёт postgres://... — переводим на postgresql+asyncpg://.
    SQLite — на sqlite+aiosqlite://. Так бот работает и локально (SQLite),
    и на сервере (Postgres) без правок кода."""
    url = (raw or "").strip()
    if not url:
        return "sqlite+aiosqlite:///data/bot.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        # asyncpg не понимает ?sslmode= в URL — убираем, ssl не нужен во внутренней сети Railway
        if "?" in url:
            url = url.split("?", 1)[0]
    elif url.startswith("sqlite://") and "+aiosqlite" not in url:
        url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def _parse_admin_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    result = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                result.append(int(part))
            except ValueError:
                pass
    return result


@dataclass
class Settings:
    bot_token: str = field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    admin_ids: list[int] = field(default_factory=lambda: _parse_admin_ids(os.getenv("ADMIN_IDS")))
    database_url: str = field(
        default_factory=lambda: _normalize_db_url(os.getenv("DATABASE_URL"))
    )
    sheets_webhook_url: str = field(default_factory=lambda: os.getenv("SHEETS_WEBHOOK_URL", ""))
    sheets_webhook_secret: str = field(default_factory=lambda: os.getenv("SHEETS_WEBHOOK_SECRET", ""))
    # Группа, куда шлём фото креаторов. Пусто → шлём админам в личку (как раньше).
    # id группы узнать: добавить бота в группу и отправить там /chatid.
    photos_chat_id: str = field(default_factory=lambda: os.getenv("PHOTOS_CHAT_ID", "").strip())
    # Канал-витрина @ugc_creatory: бот-админ постит туда карточки по кнопке «Разместить».
    channel_id: str = field(default_factory=lambda: os.getenv("CHANNEL_ID", "-1001752246005").strip())
    # Dead-man's-switch: URL внешнего монитора (healthchecks.io), который бот пингует
    # раз в несколько минут. Если пинги пропали (хостинг встал) — монитор шлёт алерт.
    heartbeat_url: str = field(default_factory=lambda: os.getenv("HEARTBEAT_URL", "").strip())

    # Ассистент (Арина, Днепр): ей уходят задачи прогрева аккаунтов и запросы на аудит.
    assistant_id: str = field(default_factory=lambda: os.getenv("ASSISTANT_ID", "1197101122").strip())

    # Сбор охватов по роликам (проект «Сингл») → клиентская таблица.
    youtube_api_key: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY", "").strip())
    vk_token: str = field(default_factory=lambda: os.getenv("VK_TOKEN", "").strip())
    ensembledata_token: str = field(default_factory=lambda: os.getenv("ENSEMBLEDATA_TOKEN", "").strip())
    # ScrapeCreators — заменил EnsembleData для IG/TikTok/Threads (триал EnsembleData кончился).
    # Отдаёт total plays (не reach). Ключ в .env → SCRAPECREATORS_API_KEY.
    scrapecreators_api_key: str = field(default_factory=lambda: os.getenv("SCRAPECREATORS_API_KEY", "").strip())
    reach_sheet_id: str = field(default_factory=lambda: os.getenv("REACH_SHEET_ID", "").strip())
    # Через сколько дней после первого сбора замораживаем ролик (перестаём парсить — экономия).
    reach_freeze_days: int = field(default_factory=lambda: int(os.getenv("REACH_FREEZE_DAYS", "14")))

    # Проект PapKids: клиентская таблица (только id/имя/счётчик) + группа с клиентом,
    # куда пересылаются ролики (в отдельную тему форума). Пусто → пока не пересылаем.
    papkids_sheet_id: str = field(default_factory=lambda: os.getenv("PAPKIDS_SHEET_ID", "").strip())
    papkids_group_id: str = field(default_factory=lambda: os.getenv("PAPKIDS_GROUP_ID", "").strip())
    papkids_topic_id: str = field(default_factory=lambda: os.getenv("PAPKIDS_TOPIC_ID", "").strip())

    # Пороги confidence для дедупа (см. doLookup_ в Apps Script — держим синхронно)
    lookup_auto_match_threshold: float = 0.92
    lookup_confirm_threshold: float = 0.70

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


settings = Settings()
