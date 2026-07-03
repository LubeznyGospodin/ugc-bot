"""
Точка входа. Polling, aiogram 3. Совместим с деплоем на Railway так же,
как и предыдущая версия (railway up, без Volumes — не нужен, дедуп идёт
через Google Sheet lookup, см. bot/sheets.py).
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings
from bot.database import init_db
from bot.handlers import router as root_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN не задан")

    init_db()

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(root_router)

    logger.info("Бот запускается (polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
