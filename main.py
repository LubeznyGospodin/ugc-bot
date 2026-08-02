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
from bot.sync import run_sync_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN не задан")

    await init_db()

    # Разово засеять счётчик заходов известными chat_id (кто ТОЧНО нажимал /start —
    # креаторы в боте + откликнувшиеся). Заходы до трекинга иначе не восстановить.
    try:
        from bot.utils.db_helpers import seed_visits_from_known

        n = await seed_visits_from_known()
        if n:
            logger.info("seed_visits: добавлено %s заходов из известных chat_id", n)
    except Exception as e:  # noqa: BLE001 — сидинг не критичен, не роняем старт
        logger.warning("seed_visits failed: %s", e)

    # «Заморозить» текущий бэклог заходов, чтобы авто-луп напоминаний не разослал им
    # пуш пачкой при деплое — они уйдут отдельно, вручную (/nudge_backlog).
    try:
        from bot.utils.db_helpers import grandfather_nudges

        g = await grandfather_nudges()
        if g:
            logger.info("grandfather_nudges: помечено %s заходов бэклога", g)
    except Exception as e:  # noqa: BLE001
        logger.warning("grandfather_nudges failed: %s", e)

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    # FSM в Postgres, а не в памяти: диалог (анкета, догрузка ссылок) переживает деплой.
    # Если БД почему-то недоступна — не падаем, откатываемся на память.
    try:
        from bot.fsm_storage import PostgresStorage

        storage = PostgresStorage()
        logger.info("FSM storage: Postgres")
    except Exception as e:  # noqa: BLE001
        logger.warning("FSM storage: откат на память (%s)", e)
        storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    # Прогрев — раньше root: у него свои /wu* и фото от ассистента, которые иначе
    # перехватят generic-обработчики регистрации.
    from bot.warmup import router as warmup_router

    dp.include_router(warmup_router)
    dp.include_router(root_router)

    # Фоновая синхронизация таблица → БД (бренды и т.д.) — чтения из БД мгновенны.
    asyncio.create_task(run_sync_loop())

    # Пуш-напоминание «доделай анкету» тем, кто зашёл ≥2ч назад и не зарегался.
    from bot.nudge import run_nudge_loop

    asyncio.create_task(run_nudge_loop(bot))

    # Пайплайн «Сингл»: напоминания креаторам про дедлайн + утренний отчёт админам.
    from bot.single import run_single_loop

    asyncio.create_task(run_single_loop(bot))

    # Отложенные рассылки по базе (задания в БД, проверка раз в минуту).
    from bot.scheduler import run_scheduler

    asyncio.create_task(run_scheduler(bot))

    # Эксплуатация: пинг внешнего монитора (алерт, если хостинг встал) + напоминания об оплате.
    from bot.ops import run_ops_loop

    asyncio.create_task(run_ops_loop(bot))

    # Сбор охватов по роликам «Сингл» → клиентская таблица (раз в сутки).
    from bot.reach import run_reach_loop

    asyncio.create_task(run_reach_loop(bot))

    # PapKids: сбор статистики (upload-post + VK) → лист «Посты» + публичный дашборд (каждые 6 ч).
    from bot.papkids_stats import run_papkids_stats_loop

    asyncio.create_task(run_papkids_stats_loop(bot))

    # Прогрев аккаунтов: задачи ассистенту в 10:00, аудит в 15:00, отчёт админам в 20:00.
    from bot.warmup import run_warmup_loop

    asyncio.create_task(run_warmup_loop(bot))

    import shutil
    logger.info("ffmpeg=%s ffprobe=%s (нужны для нормализации видео в канал)",
                shutil.which("ffmpeg"), shutil.which("ffprobe"))
    logger.info("Бот запускается (polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
