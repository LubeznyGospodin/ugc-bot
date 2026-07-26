# Decisions

Use this file for durable project decisions.

---

## 2026-07-04 — Миграция с SQLite на PostgreSQL

**Status:** accepted
**Context:** SQLite на Railway эфемерна (данные теряются при редеплое), плюс не поддерживает concurrent access.
**Decision:** Перейти на PostgreSQL (Railway managed). Драйвер asyncpg. `bot/config._normalize_db_url` понимает оба формата.
**Why:** Надёжное хранение, Railway даёт managed Postgres с бэкапами.
**Consequences:** DATABASE_URL теперь обязательна. Синк из таблицы держит БД свежей.
**Rollback:** Переключить DATABASE_URL обратно на sqlite, но потеряется персистентность.

---

## 2026-07-04 — Edit-in-place вместо delete+send

**Status:** accepted
**Context:** Баг автоскролла: удаление сообщения + отправка нового ломало скролл на десктоп-клиенте Telegram.
**Decision:** `chat_cleanup.render_screen` переписан на edit-in-place. Fallback на delete+send если правка невозможна.
**Why:** Эталон @bro_hit_bot не удаляет сообщения — нативный автоскролл работает.
**Consequences:** UX стал плавнее. Ограничение: edit не может менять тип медиа.
**Rollback:** Вернуть delete+send в `bot/utils/chat_cleanup.py`.

---

## 2026-07-05 — Реферальная система = приоритет откликов

**Status:** accepted
**Context:** Игорь решил: реферальная награда — не деньги, а приоритет откликов.
**Decision:** Deep-link `t.me/ugc_radarbot?start=ref_<tgid>`, реферер получает priority-флаг, его отклики помечаются ⭐.
**Why:** Мотивирует креаторов приводить друзей без денежных затрат.
**Consequences:** Нужен трекинг реферальных связей в БД.
**Rollback:** Убрать обработку `?start=ref_` в start handler.

---

## 2026-07-08 — FSM state в PostgreSQL вместо памяти

**Status:** accepted
**Context:** MemoryStorage теряет состояние диалога при редеплое — анкета сбрасывалась.
**Decision:** `bot/fsm_storage.PostgresStorage` — FSM state в той же Postgres. Fallback на MemoryStorage если БД недоступна.
**Why:** Диалог (анкета, догрузка ссылок) переживает деплой.
**Consequences:** Зависимость от БД для FSM. При падении Postgres — fallback на память.
**Rollback:** Убрать импорт PostgresStorage в main.py, оставить MemoryStorage.

---

## 2026-07-12 — Apps Script: header-based чтение/запись

**Status:** accepted
**Context:** Игорь удалил 2 столбца из таблицы → индексы сбились → бот писал в неверные колонки.
**Decision:** Apps Script читает/пишет по заголовкам, а не по номерам столбцов.
**Why:** Таблица может меняться (перестановка/удаление колонок) без поломки бота.
**Consequences:** Чуть медленнее (поиск заголовка), но устойчивее.
**Rollback:** Вернуть индексную адресацию (хрупко).

---

## 2026-07-20 — Контроль звонков B24 → whisper → Claude → TG

**Status:** accepted
**Context:** Нужен автоматический анализ звонков менеджеров из Bitrix24.
**Decision:** Отдельный pipeline в `call_control/`: крон каждые 15 мин, скачивание записей через `disk.file.get`, транскрипция whisper, анализ Claude, отчёт в Telegram.
**Why:** CoPilot B24 по REST API не читается — собственный pipeline.
**Consequences:** Отдельная инфраструктура, зависимость от whisper и Claude API.
**Rollback:** Остановить крон, удалить `call_control/`.

---

## 2026-07-22 — Сбор охватов reach.py (EnsembleData)

**Status:** accepted
**Context:** Клиентам нужны данные по охватам роликов в соцсетях.
**Decision:** `bot/reach.py` — автоматический сбор раз в сутки (10:00 MSK). Лимит 50 units/day.
**Why:** Ручной сбор охватов нереалистичен при масштабе 50+ роликов.
**Consequences:** Жёсткий лимит API. Никаких ручных вызовов и тестов.
**Rollback:** Отключить `run_reach_loop` в main.py.

---

## 2026-07-23 — PapKids: клиентский мини-проект

**Status:** accepted
**Context:** Клиент PapKids: сбор до 5 роликов у 12 участников.
**Decision:** Встроен как отдельный project в `bot/projects.py` с собственной FSM и трекингом.
**Why:** Переиспользование инфраструктуры бота для клиентских проектов.
**Consequences:** Бот усложняется, но не нужен отдельный сервис.
**Rollback:** Удалить PapKids handlers из router.

---

## 2026-07-26 — Система AI-суверенитета

**Status:** accepted
**Context:** Проект зависел от истории чата для контекста. При потере аккаунта/чата весь контекст терялся.
**Decision:** Создать полную систему локального хранения: docs/, logs/sessions/, prompts/, AGENTS.md, RECOVERY.md — по руководству «AI-суверенитет».
**Why:** Проект продолжит жить, даже если история чата или аккаунт станут недоступны.
**Consequences:** Небольшой overhead на ведение логов сессий. Полная независимость от провайдера AI.
**Rollback:** Не требуется — система только добавляет, ничего не ломает.
