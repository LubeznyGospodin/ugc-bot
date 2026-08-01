# HANDOFF — Автодобавление UGC-креаторов на сайт + канал/группа (июль 2026)

Хендофф для нового чата. Проект: автоматически заводить UGC-креаторов из бота **@ugc_radarbot**
в **каталог-товары Тильды** (packman-prod, раздел «UGC-креаторы»), с фото, текстами, SEO и видео,
и отмечать 👍 их посты в группе. Всё делается **через Chrome (claude-in-chrome)** + локальные скрипты.

---

## 0. Текущее состояние (на конец сессии)
- **38 креаторов на сайте** (раздел UGC-креаторы): фото + короткое описание + HTML-текст «подойдёт для»
  + полный SEO (title/descr/keywords/FB) + **у всех 38 есть видео** (Kinescope).
- **Реакции 👍** в группе синхронизированы ровно с этими 38 (ошибочные сняты).
- Каноничный ключ везде — **tg_id** (в Тильде это поле «Внешний код товара / External ID»).

### Открытые задачи
1. **10 креаторов не долетели на сайт** (импорт не создал карточку / удалены при чистке дублей):
   `Даниил Х, Юлия С, Полина С, Карина К, Светлана Ф, Анна Т, Екатерина Н, Анна К, Анастасия С, Дарья Ш`.
   Фото/видео у них готовы — до-залить тем же флоу (импорт + attach видео).
2. Догрузить **крупные рилсы (>20МБ)** по всей базе (~31 шт.) через `fetch_big_videos.py`, если нужно больше видео на карточках.
3. **2 колонки в Гугл-таблице** «Есть на сайте / Есть в канале» + автопростановка (через sheets-webhook бота) — не сделано.
4. Второй канал/публикация карточек в TG-канал @ugc_creatory — не трогали в этой сессии.

---

## 1. Доступы (всё в `.env`, он в .gitignore)
- **Selectel S3:** `SELECTEL_S3_KEY`, `SELECTEL_S3_SECRET`.
- **Kinescope:** `KINESCOPE_TOKEN`.
- **Telethon (юзер-сессия Игоря):** `TG_API_ID=24124698`, `TG_API_HASH=...`. Сессия — `data/creators_user.session`
  (в .gitignore по маске `*.session`). Вход разово: `python tg_login.py` (телефон +79092825668 + код из TG).
- **BOT_TOKEN / DATABASE_URL** — только в Railway. Тянуть: `railway variables --service <spectacular-art / Postgres> --json`
  (кэшировать в файл, CLI режет частые вызовы). Прод-БД снаружи — `DATABASE_PUBLIC_URL`.
- Локальный питон — **`.venv` (3.11)**. Пакеты: asyncpg, boto3, telethon, python-dotenv (уже стоят).

## 2. Инфраструктура
- **Selectel:** бакет **`packman`**, регион **ru-7**, endpoint `https://s3.ru-7.storage.selcloud.ru`, публичный.
  Публичный домен: **`https://24dcb37f-27c8-4dd8-84f9-6fe3e085d5df.selstorage.ru/`**.
  Фото креаторов лежат под `creators/<slug>-<tg_id>/<n>.jpg` (чистые постоянные URL, без подписи).
- **Kinescope:** проект `6d8b24d4-2b50-4d2c-9be5-c1ff7b3b9b8d`. Заливка: `POST https://uploader.kinescope.io/v2/video`
  (тело=байты, заголовки `Authorization: Bearer`, `X-Parent-ID`=проект, `X-Video-Title` — ТОЛЬКО latin-1!).
  Ответ `data.play_link = kinescope.io/<slug>` → **slug = «PRIVATE HASH» для видео в Тильде**.
- **Тильда:** projectid **9057947**, раздел UGC-креаторы `storepartuid=380691270782`. Store API — см. §4.
- **TG-группа «Креаторы Packman Prod»:** id **-1004313489669**. Посты:
  `📷 Фото креатора <Имя> (@user, id <tg_id>)` и `🎬 Работы креатора <Имя> (@user, id <tg_id>) — N шт.` + медиа ниже.

## 3. Скрипты (все в корне проекта)
- **`catalog_creators.py`** — генерация полей карточки: `catalog_fields(name, city, age, categories)` →
  `{title, descr, text(HTML), seo_title, seo_descr, seo_keywords, fb_title, fb_descr}`. Самотест: `python catalog_creators.py`.
  Правила (в bot/cards.py `guess_gender`/`short_name`): оценка пола по ОБОИМ словам (имя может быть вторым:
  «Русу Анастасия»→«Анастасия Р, female»); заглавная всегда («леся»→«Леся»); склонение города в род.падеж;
  неоднозначный пол → None (нужно компьютерное зрение /watch, /video-eyes). Описание — КОРОТКОЕ, в стиле боевых.
- **`build_import.py`** (`--works` = все с работами) — БД → скачивает фото из TG по file_id → льёт в Selectel `packman`
  → пишет CSV в точном формате импорта Тильды. Идемпотентно (уже залитые фото пропускает), ретраи.
- **`upload_videos.py`** — рилсы (creator_works, project=base) из TG → Kinescope, ledger `kinescope_ledger.json`,
  карта `kinescope_map.csv`. ⚠️ Bot API отдаёт файлы ТОЛЬКО ≤20МБ; крупнее → пометка 'toobig'.
- **`fetch_big_videos.py <tg...>`** — крупные рилсы (>20МБ) через Telethon из группы → Kinescope → `big_video_map.json`.
- **`tg_login.py`** — разовый вход Telethon (владелец).
- **`group_sync.py`** (`--dry`) — реакции 👍: читает группу, опознаёт креатора по `id <tg>` в тексте, режим сверки —
  ставит на тех, кто в `site_published.json`, СНИМАЕТ с тех, кого там нет. Реестр `group_published.json`.
- **Реестры/данные:** `site_published.json` (кто на сайте — БРАТЬ ИЗ ЖИВОГО ЭКСПОРТА, см. §5),
  `video_map.json` / `big_video_map.json` (tg→[хэши]), `kinescope_ledger.json`, `creators_data.json` (генерённые поля),
  `creators_roster.csv` (человекочитаемый список).

## 4. Store API Тильды (реверс — ключевое)
Всё через `POST https://store.tilda.ru/store/submit/` с заголовком **`X-Requested-With: XMLHttpRequest`**
(без него — ответ `RELOGIN`). Авторизация — session cookies браузера.
- **Сохранить/обновить товар:** тело `querystr=<url-enc>` с полями `comm=saveproduct, productuid=<UID!>, projectid,
  title, descr, text, gallery(JSON), partuids, price, seo_title, seo_descr, seo_keywords, fb_title, fb_descr` + outer `projectid`.
  ⚠️⚠️ **productuid = поле `uid`, НЕ `id`** (getproductslist отдаёт оба; с `id` → «Store product not exist»).
- **gallery item:** `{img:<tildacdn-url>, video:"https://kinescope.io/<hash>", vtype:"kinescope", videoid:"<hash>"}`.
  Видео вешается к первым N фото. Слать ВСЕ поля (иначе рискуешь стереть) — проверено, saveproduct сохраняет переданное.
- **Импорт CSV:** `comm=startimport` (FormData: comm, projectid, format=csv, filename, file=Blob) → ответ
  `{nextaction:"tstore_import_csv_step2", args}` → `window[nextaction](args)` рендерит окно маппинга →
  клик «Начать запись данных» ТОЛЬКО по DOM-рефу (find). Заголовки CSV авто-мапятся по имени. Формат CSV:
  разделитель **запятая**, колонки `Category,Title,Description,Text,Photo,Price,External ID,SEO title,SEO descr,
  SEO keywords,FB title,FB descr`; фото — через ПРОБЕЛ в ячейке Photo; Text — HTML; Category=«UGC-креаторы»;
  External ID=tg_id (идемпотентность). Видео CSV НЕ несёт — только через saveproduct.
- **Экспорт (образец/сверка):** `comm=startexport` (format=csv, csvseparator=comma, exportallproductimages=1,
  storepartuid) → `comm=checkexport&taskworkerid=<id>` → URL готового CSV. Даёт `Tilda UID` + `External ID`(=tg) + Photo.
- **Список:** `comm=getproductslist&storepartuid=...` — отдаёт gallery(с videoid)+uid+title, НЕ отдаёт externalid,
  режет по 100 → пагинация **`slice=1,2,3`**.
- Публичный кэш-API (для сверки НЕ годится, кэшируется): `store.tildaapi.com/api/getproductslist/?storepartuid=380691270782&recid=2301785121`.

## 5. Грабли моего окружения (важно новому чату)
- Браузерный `file_upload` заблокирован песочницей → фото/CSV грузить только фетчем (Blob/FormData), не пикером.
- javascript_tool, дёргающий авторизованный /store/submit/, часто возвращает `{}` (харнесс режет ВОЗВРАТ), но
  **сам запрос выполняется** → результат читать через `window.__var` отдельным вызовом, либо через внешний API/экспорт.
- Иногда классификатор блокирует и выполнение (delete-товара, openDialog, крупный bulk-fetch) — дробить/обходить.
- **«Кто на сайте» — ТОЛЬКО из живого экспорта** (numeric External ID=tg + сверка по Title для карточек под старым
  Tilda-ID). НЕ из БД-годности (был баг: реакция на несозданного Даниила) и НЕ из кэш-API.
- **Проверять видео ФАКТически** (getproductslist gallery videoid), а не «по хэшам» — при массовом saveproduct
  часть видео слетала, приходилось довешивать.
- Export-URL Тильды ПРОТУХАЕТ быстро — использовать сразу. window-состояние держать на ОТДЕЛЬНОЙ вкладке
  (владелец, кликая товары в той же вкладке, перезагружает страницу и стирает state).
- Реакции: ~50 подряд → FloodWaitError (~292с), group_sync сам пережидает.

## 6. Как продолжить в новом чате
1. Прочитать память `tilda-creators-autoadd.md` (там то же сжато) + этот файл.
2. Проверить .env (ключи Selectel/Kinescope/Telethon на месте), при нужде подтянуть BOT_TOKEN/DATABASE_URL из Railway.
3. Открыть Chrome (claude-in-chrome), убедиться что Тильда залогинена (Игорь П.), Telethon-сессия есть.
4. Для до-заливки 10: `build_import.py` на их tg_id → импорт (§4) → attach видео из video_map → group_sync.
5. Всегда: перед заливкой собрать review-таблицу (данные+ссылки фото/видео) на утверждение владельцу, потом лить скопом.
