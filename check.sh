#!/usr/bin/env bash
# Смоук-тест ПЕРЕД деплоем: компиляция + импорт + тест БД-слоя.
# Если что-то падает — НЕ деплоить.
set -e
echo "1) Компиляция всех .py..."
python3 -m compileall -q bot main.py
echo "2) Импорт-смоук..."
DATABASE_URL="sqlite+aiosqlite:////tmp/check_bot.db" python3 -c "
import importlib
for m in ['bot.config','bot.models','bot.database','bot.sheets','bot.sync',
          'bot.handlers.start','bot.handlers.brands','bot.handlers.profile',
          'bot.handlers.registration','bot.handlers.admin','main']:
    importlib.import_module(m)
print('   импорт OK')
"
echo "3) Тест БД-слоя (создание таблиц)..."
DATABASE_URL="sqlite+aiosqlite:////tmp/check_bot.db" python3 -c "
import asyncio
from bot.database import init_db
from bot.utils.db_helpers import db_stats
async def m():
    await init_db(); await db_stats()
asyncio.run(m()); print('   БД OK')
"
rm -f /tmp/check_bot.db
echo '✅ Все проверки прошли — можно деплоить (railway up).'
