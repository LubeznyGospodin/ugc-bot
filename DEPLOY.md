# Деплой на Railway

## Что готово
- Весь новый код в папке `ugc_bot_v2/`
- requirements.txt с зависимостями (aiogram 3.15.0, SQLAlchemy 2.0, aiosqlite, openpyxl)
- Переменные окружения уже есть на Railway (BOT_TOKEN, ADMIN_IDS, DATABASE_URL, SHEETS_WEBHOOK_URL, SHEETS_WEBHOOK_SECRET)

## Как деплоить

### Способ 1: Railway CLI (если у тебя установлен railway)

```bash
cd ugc_bot_v2/
railway up
```

Railway CLI:
- автоматически запустит деплой на проект "spectacular-art"
- использует переменные окружения, которые уже есть в проекте
- создаст/обновит контейнер и запустит `python main.py`

### Способ 2: GitHub (если твой код лежит в GitHub)
1. Push изменения в репо (branch main или production)
2. Railway автоматически заметит push и пересоберёт контейнер

### Способ 3: Railway Dashboard (вручную)
1. Перейти на https://railway.com/project/{projectId}
2. Settings → Source → Connect Repo (если ещё не подключен)
3. Указать GitHub repo

## После деплоя
1. Перейти на https://railway.com/project/{projectId}/service/{serviceId}/console
2. Проверить логи:
   - "Бот запускается (polling)..." → good
   - Не должно быть ошибок импорта (если есть syntax errors, они выйдут в первые строки лога)
3. Отправить /start боту в Telegram → проверить, что бот отвечает

## Если что-то сломалось

### Логи
Railway Console tab → Logs: смотри последние 50 строк, ищи stack trace.

### Откат
1. Railway Dashboard → Deployments
2. Выбрать предыдущий ACTIVE деплой (был "railway_up" 17 часов назад)
3. Click "Revert" → вернёт старый код

### Основные ошибки
- `ModuleNotFoundError: No module named 'aiogram'` → pip install не прошла, check requirements.txt
- `BOT_TOKEN not found` → переменные окружения не установлены, check Railway Variables tab
- `SyntaxError` → проверить код в `/bot/handlers/*.py` или `main.py`

## Структура

```
ugc_bot_v2/
├── main.py                    # точка входа
├── requirements.txt           # зависимости
├── .env.example               # пример переменных (не используется на Railway)
├── bot/
│   ├── __init__.py
│   ├── config.py              # читает переменные окружения
│   ├── sheets.py              # клиент для Google Sheets (webhook)
│   ├── keyboards.py           # все клавиатуры
│   ├── categories.py          # список категорий контента
│   ├── states.py              # FSM states
│   ├── models.py              # SQLAlchemy ORM
│   ├── database.py            # инициализация БД
│   ├── utils/
│   │   ├── chat_cleanup.py    # "одно эволюционирующее сообщение"
│   │   ├── db_helpers.py      # CRUD операции
│   │   └── export.py          # экспорт в xlsx
│   └── handlers/
│       ├── __init__.py        # регистрация всех роутеров
│       ├── start.py           # /start + дедуп
│       ├── registration.py    # анкета на 9 шагов
│       ├── profile.py         # "👤 Моя анкета"
│       ├── brands.py          # "📢 Запросы брендов"
│       └── admin.py           # админ-панель
```

## Что изменилось в новом коде

1. **Меню + очистка чата** — постоянное нижнее меню (ReplyKeyboardMarkup), старые сообщения бота удаляются (одно "эволюционирующее")
2. **Дедуп на /start** — `doLookup_` из Google Sheets:
   - ≥92% схожесть → авто-узнан
   - 70-92% → переспрашиваем по Instagram
   - <70% → новая анкета
3. **Регистрация с правкой** — на confirm пишем через `doUpdateRow_` (если уже был) вместо appendRow
4. **Каталог брендов** — инлайн-список из вкладки "Бренды", кнопка "Откликнуться"
5. **Админ-панель** — аналитика (doStats_), рассылка (с троттлингом), экспорт xlsx
6. **UX-анимация** — "Ищу тебя..." с 3 кадрами перед поиском

## База данных
- SQLite (aiosqlite) в `/data/bot.db`
- Локальная, ephemeral (на Railway потеряется при редеплое, но это OK)
- Используется только как кэш и для отправки рассылок
- Источник истины — Google Sheets (column "Есть в боте" маркирует, кто в боте)
