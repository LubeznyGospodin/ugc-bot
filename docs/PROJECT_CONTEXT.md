# Project Context

## What This Project Is

Telegram-бот **@ugc_radarbot** («UGC RADAR») для Packman Production. Регистрирует UGC-креаторов, показывает запросы брендов, даёт откликаться, отслеживать статус откликов (на рассмотрении / отказ / оффер), а админам — аналитику/рассылку/экспорт. Включает клиентские мини-проекты: «Сингл» (сбор роликов), PapKids (12 участников, до 5 роликов), контроль звонков (B24 → whisper → Claude → Telegram).

## Why It Exists

Бизнес-цель Игоря Попова (Packman Production): автоматизировать работу с UGC-креаторами — от регистрации до трекинга выполнения заказов. Вместо ручного управления таблицами — бот, куда креатор хочет заходить.

## Current State

- **Phase:** production (бот работает, 80+ креаторов, реальные заказы)
- **Working version:** commit d0fc9ed (2026-07-25)
- **Main branch:** master (origin: `popovigory/ugc-bot-v2`)
- **Last important change:** fix sync_applications — varchar16 overflow при длинных статусах
- **Deploy:** Railway, проект "spectacular-art", `railway up` или GitHub push

## Architecture & Stack

- **Bot:** Python 3.11, aiogram 3 (polling), деплой на Railway
- **DB:** PostgreSQL на Railway (asyncpg). FSM state — тоже в Postgres (переживает деплой)
- **Data source of truth:** Google Sheets (`1Hoqi-lLxyJtApNR6WGN59Rwz0u0hLPqFuDE-fQbzYyI`)
- **Bridge:** Google Apps Script web-app (webhook, один POST-эндпоинт, роутинг по `action`)
- **Sync:** `bot/sync.py` — фоновый цикл каждые 120с тянет из таблицы в БД
- **Background loops:** nudge (2ч), single (дедлайны+отчёты), ops (пинг+оплата), reach (охваты 1/сутки)

### Data Flow
Пользователь → бот читает из **БД** (мгновенно). Фоновый синк держит БД свежей из таблицы. Запись (регистрация, отклик) → таблица через webhook + сразу в БД. Пуш о статусе: onEdit-триггер в Apps Script → Telegram напрямую.

## Users / Audience

- **Креаторы** (~80+): регистрация, просмотр брендов, отклики, трек-рекорд
- **Админы** (Игорь Попов, admin_ids): аналитика, рассылка, экспорт xlsx, управление брендами через таблицу
- **Клиенты** (Сингл, PapKids): мини-проекты по сбору роликов

## Constraints

- **Budget:** Railway free/starter, EnsembleData API 50 units/day
- **Stack:** Python 3.11+, aiogram 3, PostgreSQL, Google Sheets
- **Legal/security:** секреты только в `.env` / Railway Variables / Script Properties; никогда в логах
- **Things we will NOT do:** пушить в `LubeznyGospodin/ugc-bot` (мёртвый репо); рассылки креаторам без подтверждения Игоря; ручные вызовы reach API

## Important Paths

- **Source:** `bot/` (handlers, models, sync, sheets, config)
- **Call control:** `call_control/` (pipeline.py, prompts, records, daily reports)
- **Outreach:** `outreach/` (instagram_dm_log.csv)
- **Apps Script:** `apps_script/` (clasp, excluded from git — contains webhook SECRET)
- **Outputs:** `outputs/`
- **Logs:** `logs/`
- **Prompts:** `prompts/`
- **Docs:** `docs/`

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Entry point: polling, init_db, background loops |
| `bot/config.py` | Settings from ENV |
| `bot/database.py` | Async engine, init_db |
| `bot/models.py` | Creator, CachedBrand, CachedApplication |
| `bot/sheets.py` | Apps Script webhook client |
| `bot/sync.py` | Background sync sheets → DB |
| `bot/handlers/start.py` | /start, dedup, search animation |
| `bot/handlers/registration.py` | Questionnaire (FSM) |
| `bot/handlers/profile.py` | Profile view + edit |
| `bot/handlers/brands.py` | Brand list, apply, my applications |
| `bot/handlers/admin.py` | Admin: analytics, broadcast, export |
| `bot/single.py` | Сингл project: deadlines, reports |
| `bot/reach.py` | Reach collector (EnsembleData) |
| `bot/nudge.py` | Nudge unfinished registrations |
| `call_control/pipeline.py` | B24 → whisper → Claude → TG |
| `check.sh` | Smoke test before deploy |
| `HANDOFF.md` | Legacy full handoff |
| `AGENT_LOG.md` | Legacy full agent log (batches 1-11) |

## Definition of Done

A task is done only when:
- files are updated
- `bash check.sh` passes (or skipped with reason)
- session log is updated
- next step is clear
