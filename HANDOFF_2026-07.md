# UGC RADAR bot — контекст для нового чата (июль 2026)

## Что это
Telegram-бот **@ugc_radarbot** (Packman Production): регистрация UGC-креаторов, отклики на бренды,
проекты, аналитика. Стек: Python **aiogram 3** (polling), **Postgres на Railway**, источник правды —
Google Sheet через **Apps Script webhook**.

## Инфраструктура / доступы
- Код: `~/Desktop/ugc_bot_v2`. Деплой: `railway up --detach` (иногда TLS-сбои — ретраить).
- Railway: сервис `spectacular-art` (41f7c2d5). Postgres — отдельный сервис.
- **Прод-БД снаружи**: `DATABASE_PUBLIC_URL` из `railway variables --service Postgres --json`.
  Railway CLI режет частые вызовы — кэшировать вывод в файл, не дёргать по 5 раз подряд.
- Локальный python: **`.venv`** (3.11). Системный 3.9 падает на `Mapped[str | None]`.
- Apps Script (webhook) правится через **clasp** в `apps_script/Код.js`:
  `clasp push -f && clasp create-deployment -i AKfycbyDWDeOWwdQFT-UHJSdBaHvcXCNF4DyS1rA1Vq61MrKk50DxAbk0mWLlDZUF-1saGUY`.
  `openById()` пишет и в чужие таблицы (так работают reach/PapKids).
- Ключи ТОЛЬКО в Railway env. Admin id: **357892821**.

## Google-таблицы
- Основная (креаторы + лист «Отклики»): `1Hoqi-lLxyJtApNR6WGN59Rwz0u0hLPqFuDE-fQbzYyI`.
- Reach-аналитика (Сингл): `14iH1s6bctklEuQ5kVGgjolvrP4XAKPScZkhTcz_-qRY`.
- PapKids (клиентская): `1qZHSUgAMkuGSJQWKNm4n0osBGYdrOMoGGuk-0jcuhL4`.

## Что построено
1. **Регистрация/дедуп** по chat_id+telegram (имя не гейтит). Фото → CreatorPhoto.
2. **Проект «Сингл»** (br1): пайплайн оффер→участвую→срок→ссылки→оплата. **Авто-оффер**: отклик =
   сразу «оффер». Воронка в админке.
3. **Reach-трекинг** роликов Сингла → клиентская таблица. Бесплатно: YouTube/VK/Telegram/**Likee**.
   Платно: Instagram/TikTok через **EnsembleData** (~50 юнитов/сутки — ⚠️ НЕ гонять ручные прогоны,
   только авто 10:00 МСК). Threads просмотров не отдаёт, FB — вручную. Ручной ввод в таблице
   приоритетнее (флаг manual). Запись ссылок расцеплена с парсингом охватов.
4. **Трекинг источников** `?start=МЕТКА` (first-touch) + реф-ссылки `?start=ref<tg_id>`; в админке
   «🔗 Источники» ref расшифровывается в имя.
5. **База @ugc_creatory** (карточки креаторов): сбор работ (works.py, до 4), генератор карточки
   `bot/cards.py` (пол по фамилии/имени, хештеги из категорий). Публикация — пока ВРУЧНУЮ скриптом
   (скачать видео yt-dlp / инста-API из браузера с VPN → обложки ffmpeg → media_group в канал).
   Кнопки публикации в админке НЕТ, ffmpeg на Railway НЕТ.
6. **Проект PapKids**: мини-проект, 12 вшитых участников (таблица ProjectMember). «Мои проекты» →
   🧸 PapKids → до 5 роликов (ссылка/файл, project="papkids" в CreatorWork). Каждый ролик → тема
   «Контент» группы клиента (env PAPKIDS_GROUP_ID=-1004354643998, PAPKIDS_TOPIC_ID=153) + счётчик
   в лист «Ролики (бот)» клиентской таблицы (имя+id, без логинов). Пуш 12 участникам разослан.
7. **FSM в Postgres** (bot/fsm_storage.py) — диалоги переживают деплой.

## Ключевые файлы
`bot/handlers/{registration,brands,single,works,papkids,admin,start}.py`, `bot/{single,projects,cards,reach,
fsm_storage,models,sheets,config}.py`, `apps_script/Код.js`.

## Грабли
- Новые колонки в листах — ТОЛЬКО в конец, не двигать Статус/Причину (ломает офферы/отказы).
- Зеркала листа: длинный текст ронял sync — держать Text, не varchar(16).
- Инстаграм с сервера/curl недоступен (РФ-блок); только через браузер с VPN (внутренний API инсты
  `/api/v1/media/<id>/info/`, media_id = base64-декод shortcode).
- Видео >50МБ бот не зальёт; длинные жать ffmpeg. Обложка чёрная → брать не первый кадр.
- `/chatid` в группе отвечает только админу.

## Незакрытое
- @ugc_creatory: ~11 готовых креаторов ждут карточек; кнопка публикации в админке не сделана.
- PapKids: напоминание «кто не загрузил», счётчик «X/12» в админку — по желанию.
- Второй канал **@ugc_ref** (лучшие работы) — формат не обсуждён.
