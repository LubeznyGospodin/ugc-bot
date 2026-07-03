"""
Конфигурация бота. Значения читаются из переменных окружения (.env локально,
Railway Variables в проде). Ничего секретного здесь не хардкодится.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


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
        default_factory=lambda: os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/bot.db")
    )
    sheets_webhook_url: str = field(default_factory=lambda: os.getenv("SHEETS_WEBHOOK_URL", ""))
    sheets_webhook_secret: str = field(default_factory=lambda: os.getenv("SHEETS_WEBHOOK_SECRET", ""))

    # Пороги confidence для дедупа (см. doLookup_ в Apps Script — держим синхронно)
    lookup_auto_match_threshold: float = 0.92
    lookup_confirm_threshold: float = 0.70

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


settings = Settings()
