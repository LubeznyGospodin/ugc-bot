"""
Сбор охватов по роликам проекта «Сингл» → клиентская таблица.

Источники (все проверены на живых ссылках):
- YouTube  → YouTube Data API (свой ключ)
- VK       → VK API video.get (user-токен)
- Instagram→ EnsembleData /instagram/post/details
- TikTok   → EnsembleData /tt/post/info
- Telegram → парс публичного поста t.me

Каждый фетчер возвращает (views:int|None, error:str|None). None+error → пометим ⚠️,
прошлое значение не теряем (см. reach_run).
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from bot.config import settings

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
# Алфавит base64 инстаграма для shortcode → media_id (не используется для EnsembleData,
# но оставим для возможной проверки).
_IG_ALPH = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _http_json(url: str, timeout: int = 45, headers: dict | None = None) -> dict:
    hdr = {"User-Agent": _UA, "Accept": "*/*"}
    if headers:
        hdr.update(headers)
    req = urllib.request.Request(url, headers=hdr)
    with urllib.request.urlopen(req, timeout=timeout) as x:
        return json.load(x)


def detect_platform(url: str) -> str:
    u = (url or "").lower()
    if "youtube" in u or "youtu.be" in u:
        return "youtube"
    if "vk.ru" in u or "vk.com" in u or "vkvideo" in u:
        return "vk"
    if "instagram" in u:
        return "instagram"
    if "tiktok" in u:
        return "tiktok"
    if "t.me" in u or "telegram.me" in u:
        return "telegram"
    if "threads.com" in u or "threads.net" in u:
        return "threads"
    if "facebook" in u or "fb.watch" in u:
        return "facebook"
    if "likee.video" in u or "like.video" in u or "likee.com" in u:
        return "likee"
    return "other"


# ── Парсеры id из ссылок ──────────────────────────────────────────────────────
def _youtube_id(url: str) -> str | None:
    m = re.search(r"(?:shorts/|watch\?v=|youtu\.be/|/embed/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("v")
    return q[0] if q else None


def _vk_id(url: str) -> str | None:
    m = re.search(r"(?:clip|video)(-?\d+)_(\d+)", url)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _vk_wall_id(url: str) -> str | None:
    m = re.search(r"wall(-?\d+)_(\d+)", url)
    return f"{m.group(1)}_{m.group(2)}" if m else None


def _ig_shortcode(url: str) -> str | None:
    m = re.search(r"instagram\.com/(?:reels?|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


# ── Фетчеры ───────────────────────────────────────────────────────────────────
def fetch_youtube(url: str) -> tuple[int | None, str | None]:
    vid = _youtube_id(url)
    if not vid:
        return None, "не распознал youtube id"
    if not settings.youtube_api_key:
        return None, "нет YOUTUBE_API_KEY"
    try:
        api = (
            "https://www.googleapis.com/youtube/v3/videos?part=statistics&id="
            f"{vid}&key={settings.youtube_api_key}"
        )
        d = _http_json(api)
        items = d.get("items", [])
        if not items:
            return None, "видео не найдено/удалено"
        return int(items[0]["statistics"].get("viewCount", 0)), None
    except Exception as e:  # noqa: BLE001
        return None, f"youtube: {e}"


def fetch_vk(url: str) -> tuple[int | None, str | None]:
    if not settings.vk_token:
        return None, "нет VK_TOKEN"
    wall = _vk_wall_id(url)  # wall-посты (репосты в сообщество) — считаем просмотры поста
    vid = None if wall else _vk_id(url)
    if not wall and not vid:
        return None, "не распознал vk id"
    try:
        if wall:
            api = "https://api.vk.com/method/wall.getById?" + urllib.parse.urlencode(
                {"posts": wall, "access_token": settings.vk_token, "v": "5.199"}
            )
            d = _http_json(api)
            if "error" in d:
                return None, f"vk: {d['error'].get('error_msg')}"
            resp = d.get("response")
            items = resp.get("items") if isinstance(resp, dict) else resp  # схема зависит от версии
            if not items:
                return None, "пост не найден/удалён"
            views = (items[0].get("views") or {}).get("count")
            if views is None:
                return None, "у поста нет счётчика просмотров"
            return int(views), None
        api = "https://api.vk.com/method/video.get?" + urllib.parse.urlencode(
            {"videos": vid, "access_token": settings.vk_token, "v": "5.199"}
        )
        d = _http_json(api)
        if "error" in d:
            return None, f"vk: {d['error'].get('error_msg')}"
        items = d.get("response", {}).get("items", [])
        if not items:
            return None, "клип не найден/приватный"
        return int(items[0].get("views", 0)), None
    except Exception as e:  # noqa: BLE001
        return None, f"vk: {e}"


def _sc_get(path: str, params: dict) -> dict:
    """ScrapeCreators с ретраями (заменил EnsembleData). Ключ идёт в заголовке x-api-key,
    ссылка/код — в query. Бэкенд под нагрузкой временами отдаёт 429/5xx."""
    import time

    url = "https://api.scrapecreators.com" + path + "?" + urllib.parse.urlencode(params)
    hdr = {"x-api-key": settings.scrapecreators_api_key}
    last = None
    for attempt in range(4):
        try:
            return _http_json(url, timeout=60, headers=hdr)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(4 * (attempt + 1))  # 4,8,12с
                continue
            raise
    raise last


def fetch_instagram(url: str) -> tuple[int | None, str | None]:
    if not _ig_shortcode(url):
        return None, "не распознал ig shortcode"
    if not settings.scrapecreators_api_key:
        return None, "нет SCRAPECREATORS_API_KEY"
    try:
        d = _sc_get("/v1/instagram/post", {"url": url})
        media = (d.get("data") or {}).get("xdt_shortcode_media")
        if not isinstance(media, dict):
            # НЕ утверждаем «удалён»: чаще приватный аккаунт, лимит API или сбой.
            # Прошлое значение сохраняем, строку помечаем ⚠️.
            return None, "API не вернул данные (приватный аккаунт / лимит / сбой)"
        pc = media.get("video_play_count") or media.get("video_view_count")
        if pc is None:  # фото/карусель — счётчика просмотров у IG нет
            return None, "фото-пост без счётчика просмотров — впиши вручную"
        return int(pc), None
    except Exception as e:  # noqa: BLE001
        return None, f"instagram: {e}"


def fetch_tiktok(url: str) -> tuple[int | None, str | None]:
    if not settings.scrapecreators_api_key:
        return None, "нет SCRAPECREATORS_API_KEY"
    try:
        d = _sc_get("/v2/tiktok/video", {"url": url})
        st = (d.get("aweme_detail") or {}).get("statistics")
        if not isinstance(st, dict):
            return None, "видео недоступно"
        return int(st.get("play_count", 0)), None
    except Exception as e:  # noqa: BLE001
        return None, f"tiktok: {e}"


def _threads_shortcode(url: str) -> str | None:
    m = re.search(r"threads\.(?:com|net)/@[^/]+/post/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def _find_views(obj, _depth: int = 0):
    """Рекурсивно ищем в ответе поле с числом просмотров. Threads/Meta меняют схему,
    поэтому не привязываемся к одному пути: берём первый ключ вида *view*/*play* с int."""
    if _depth > 6:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if isinstance(v, int) and ("view" in kl or "play" in kl) and "disabled" not in kl:
                return v
        for v in obj.values():
            got = _find_views(v, _depth + 1)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj[:5]:
            got = _find_views(v, _depth + 1)
            if got is not None:
                return got
    return None


def fetch_threads(url: str) -> tuple[int | None, str | None]:
    """Threads через ScrapeCreators (/v1/threads/post) — отдаёт post.view_counts."""
    if not _threads_shortcode(url):
        return None, "не распознал threads shortcode"
    if not settings.scrapecreators_api_key:
        return None, "нет SCRAPECREATORS_API_KEY"
    try:
        d = _sc_get("/v1/threads/post", {"url": url})
        post = d.get("post")
        if not isinstance(post, dict):
            return None, "пост не найден в API"
        views = post.get("view_counts")
        if views is None:  # ищем защитно — Meta иногда меняет схему
            views = _find_views(post)
        if views is None:
            return None, "API не отдаёт просмотры — впиши вручную"
        return int(views), None
    except Exception as e:  # noqa: BLE001
        return None, f"threads: {e}"


_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def fetch_likee(url: str) -> tuple[int | None, str | None]:
    """Likee — бесплатно, из мета-описания страницы («812 views, 11 likes, …»).

    Два нюанса, без которых не работает:
    1) нужен МОБИЛЬНЫЙ User-Agent: с десктопным короткая ссылка l.likee.video/v/<code>
       редиректит на главную вместо поста;
    2) короткую ссылку сразу переписываем на likee.video/v/<code> — надёжнее редиректа.
    """
    u = re.sub(r"//l\.likee\.video/", "//likee.video/", url)
    try:
        req = urllib.request.Request(
            u, headers={"User-Agent": _MOBILE_UA, "Accept-Language": "en-US,en;q=0.9"}
        )
        with urllib.request.urlopen(req, timeout=45) as x:
            html = x.read().decode("utf-8", "ignore")
            final = x.geturl()
        if "/v/" not in final and "/video/" not in final:
            return None, "ссылка не открылась (пост удалён/приватный?)"
        m = re.search(r"([\d][\d.,\s]*[KMkMкм]?)\s*views", html)
        if not m:
            return None, "просмотры не найдены на странице"
        return _parse_short_num(m.group(1).replace(" ", "").replace(",", "")), None
    except Exception as e:  # noqa: BLE001
        return None, f"likee: {e}"


def fetch_facebook(url: str) -> tuple[int | None, str | None]:
    """Facebook: авто-источника нет. EnsembleData FB не поддерживает, а публичные
    share-ссылки отдают 400 без сессии. Только ручной ввод (он не перезатирается)."""
    return None, "FB только вручную (авто-источника нет)"


def fetch_telegram(url: str) -> tuple[int | None, str | None]:
    """Просмотры публичного поста t.me/<channel>/<id> — из встраиваемого виджета.
    Лайков в виджете нет (реакции не отдаются) — всегда None."""
    if re.search(r"t\.me/c/", url):  # t.me/c/ — приватный канал, просмотры недоступны
        return None, "приватный канал — недоступно"
    m = re.search(r"t\.me/([^/]+)/(\d+)", url)
    if not m:
        return None, "не распознал telegram пост"
    try:
        emb = f"https://t.me/{m.group(1)}/{m.group(2)}?embed=1&mode=tme"
        req = urllib.request.Request(emb, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as x:
            html = x.read().decode("utf-8", "ignore")
        vm = re.search(r'tgme_widget_message_views[^>]*>([\d.,KkМмKM]+)</span>', html)
        if not vm:
            return None, "просмотры не найдены (не публичный?)"
        return _parse_short_num(vm.group(1)), None
    except Exception as e:  # noqa: BLE001
        return None, f"telegram: {e}"


def _parse_short_num(s: str) -> int:
    s = s.strip().replace(",", ".").lower()
    mult = 1
    if s.endswith(("k", "к")):
        mult, s = 1000, s[:-1]
    elif s.endswith(("m", "м")):
        mult, s = 1_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


_FETCHERS = {
    "youtube": fetch_youtube,
    "vk": fetch_vk,
    "instagram": fetch_instagram,
    "tiktok": fetch_tiktok,
    "telegram": fetch_telegram,
    "threads": fetch_threads,
    "facebook": fetch_facebook,
    "likee": fetch_likee,
}
# Площадки через EnsembleData — им нужна пауза побольше (их IG/TikTok-бэкенд рейт-лимитит).
_SLOW = {"instagram", "tiktok"}


# Наши посевные аккаунты Сингла: не жжём ScrapeCreators, просмотры по ним
# пишет singl_seed.py stats из upload-post (строка в таблице уйдёт в «вручную»).
_OWN_HANDLES = (
    "singl.zvuk", "singl_zvuk", "skazhi_pesney", "skazhi.pesney",
    "pesnya.vpodarok", "pesnya_vpodarok", "pesnya_v_podar_ok",
)


def fetch_reach(url: str) -> tuple[str, int | None, str | None]:
    """→ (платформа, views|None, error|None)."""
    pl = detect_platform(url)
    low = url.lower()
    if pl in _SLOW and any(h in low for h in _OWN_HANDLES):
        return pl, None, "наш аккаунт — учёт через upload-post"
    fn = _FETCHERS.get(pl)
    if fn is None:
        return pl, None, "площадка не поддерживается"
    views, err = fn(url)
    return pl, views, err


# ── Добавление ссылок в трекинг (из бота: креатор/админ) ──────────────────────
_URL_RE = re.compile(r"https?://[^\s,]+")


def extract_urls(text: str) -> list[str]:
    return [u.rstrip(").,") for u in _URL_RE.findall(text or "")]


async def add_reach_link(url: str, creator: str, telegram: str = "") -> tuple[bool, str]:
    """Добавить ссылку в трекинг охватов. Дата добавления = сейчас. Возвращает (новая?, платформа)."""
    from datetime import datetime

    from sqlalchemy import select

    from bot.database import get_session
    from bot.models import ReachRow

    url = url.rstrip(").,").strip()
    platform = detect_platform(url)
    async with get_session() as s:
        row = (await s.execute(select(ReachRow).where(ReachRow.url == url))).scalar_one_or_none()
        if row is not None:
            row.active = True  # уже есть — не дублируем, не сбрасываем дату
            row.pinned = True  # пришла через бота → reach_run её не погасит
            await s.commit()
            return False, platform
        s.add(ReachRow(url=url, creator=creator, telegram=telegram, platform=platform,
                       first_seen=datetime.utcnow(), active=True, pinned=True))
        await s.commit()
    return True, platform


# ── Оркестрация: сбор всех ссылок → БД → клиентская таблица ────────────────────


async def write_reach_sheet(prev_map: dict[str, int | None] | None = None) -> dict:
    """Выгрузить текущее состояние трекинга в клиентскую таблицу.

    ВАЖНО: охваты НЕ парсит — ни одного запроса к платным API (только Google Sheets).
    Поэтому вызывается сразу при добавлении ссылки: ссылка появляется в таблице
    мгновенно (с «нет данных»), а цифра охвата подтянется ближайшим reach_run.

    prev_map — что бот писал в таблицу в прошлый раз (для детекта ручной правки);
    по умолчанию берём текущие значения из БД."""
    from datetime import datetime

    from sqlalchemy import select

    from bot.database import get_session
    from bot.models import ReachRow
    from bot.sheets import SheetsError, sheets_client
    from bot.single import MSK

    if not settings.reach_sheet_id:
        return {"ok": False, "error": "нет REACH_SHEET_ID"}

    now = datetime.utcnow()
    freeze_days = settings.reach_freeze_days
    async with get_session() as s:
        rows_db = list(
            (await s.execute(select(ReachRow).where(ReachRow.active.is_(True)))).scalars().all()
        )
    if prev_map is None:  # отдельная выгрузка: последнее записанное == текущее в БД
        prev_map = {r.url: r.views for r in rows_db}

    def flag(r) -> str:
        if r.manual:
            return "✍️ вручную"
        if r.first_seen and (now - r.first_seen).days >= freeze_days:
            return f"🏁 финал ({freeze_days}д)"
        if r.views is None:
            return "⏳ ждём охват"
        if r.last_error:  # есть прошлое значение, но обновить не смогли
            return "⚠️ не обновилось"
        if r.views >= 10000:
            return "🔥 >10к"
        return "✓"

    # порядок: по дате добавления (новые внизу) — сортировку в таблице делает пользователь,
    # бот только апсертит (см. doReachWrite_).
    rows_db.sort(key=lambda r: (r.first_seen or now))
    sheet_rows = [
        {
            "date_added": (r.first_seen + MSK).strftime("%d.%m.%Y") if r.first_seen else "",
            "creator": r.creator or "—",
            "platform": r.platform,
            "url": r.url,
            "views": r.views,
            "updated": (r.updated_at + MSK).strftime("%d.%m %H:%M") if r.updated_at else "",
            "flag": flag(r),
            "prev": prev_map.get(r.url),   # для детекта ручной правки на стороне таблицы
            "manual": bool(r.manual),
        }
        for r in rows_db
    ]
    total = sum(r.views for r in rows_db if r.views is not None)

    try:
        res = await sheets_client.reach_write(settings.reach_sheet_id, sheet_rows, total)
    except SheetsError as e:
        logger.warning("reach_write failed: %s", e)
        return {"ok": False, "error": f"запись таблицы: {e}"}

    # Таблица сообщила, где охват правили руками → запоминаем и больше не трогаем/не парсим.
    newly = [u for u in (res.get("manual") or []) if u]
    freed = [u for u in (res.get("unmanual") or []) if u]  # в таблице стёрли «✍️ вручную»
    if newly or freed:
        async with get_session() as s:
            for u in newly:
                row = (await s.execute(select(ReachRow).where(ReachRow.url == u))).scalar_one_or_none()
                if row and not row.manual:
                    row.manual = True
                    logger.info("reach: %s помечен как ручной — больше не парсим", u[:60])
            for u in freed:
                row = (await s.execute(select(ReachRow).where(ReachRow.url == u))).scalar_one_or_none()
                if row and row.manual:
                    row.manual = False
                    logger.info("reach: %s разморожен — парсим снова", u[:60])
            await s.commit()
    return {"ok": True, "rows": len(rows_db), "total": total, "manual_new": len(newly)}


async def reach_run(bot) -> dict:
    """Собрать охваты по всем роликам «Сингл», обновить БД (не теряя прошлое при сбое),
    записать клиентскую таблицу, оповестить админов о сбоях. Возвращает сводку."""
    import asyncio
    from datetime import datetime

    from sqlalchemy import select

    from bot.database import get_session
    from bot.models import ReachRow
    from bot.sheets import SheetsError, sheets_client
    from bot.single import MSK, is_single

    if not settings.reach_sheet_id:
        logger.warning("reach_run: REACH_SHEET_ID не задан")
        return {"ok": False, "error": "нет REACH_SHEET_ID"}

    items = await sheets_client.all_applications()
    # url -> (creator, telegram)  (только «Сингл»/ЗВУК, только с ссылкой)
    link_owner: dict[str, tuple[str, str]] = {}
    for a in items:
        if not is_single(a.get("brand_title")):
            continue
        for url in _URL_RE.findall(a.get("video") or ""):
            url = url.rstrip(").,")
            link_owner.setdefault(url, (a.get("name") or "", a.get("telegram") or ""))

    now = datetime.utcnow()
    freeze_days = settings.reach_freeze_days
    failed: list[str] = []
    frozen_cnt = 0
    manual_cnt = 0
    prev_map: dict[str, int | None] = {}  # что бот писал в таблицу в ПРОШЛЫЙ раз
    async with get_session() as s:
        existing = {r.url: r for r in (await s.execute(select(ReachRow))).scalars().all()}
        # Ссылки, добавленные ЧЕРЕЗ БОТА («➕ Добавить ещё ссылку» / админ), в листе
        # «Отклики» не появляются. Добавляем их к списку из листа, иначе гашение ниже
        # выкидывало бы их из клиентской таблицы.
        for r in existing.values():
            if r.pinned and r.url not in link_owner:
                link_owner[r.url] = (r.creator or "", r.telegram or "")
        for r in existing.values():
            r.active = False  # отметим ушедшие; активные включим ниже
        for url, (creator, tg) in link_owner.items():
            row = existing.get(url)
            if row is None:
                row = ReachRow(url=url, first_seen=now)
                s.add(row)
                existing[url] = row
            if row.first_seen is None:
                row.first_seen = now
            row.creator, row.telegram, row.active = creator, tg, True
            # Снимаем ДО фетча: с этим таблица сверит ячейку и поймёт ручную правку.
            prev_map[url] = row.views
            # Ручной ввод — не парсим совсем (экономим юниты) и не перезаписываем.
            if row.manual:
                manual_cnt += 1
                continue
            # Заморозка: ролик старше N дней не парсим (экономим юниты), значение остаётся.
            if (now - row.first_seen).days >= freeze_days:
                frozen_cnt += 1
                continue
            platform, views, err = await asyncio.to_thread(fetch_reach, url)
            row.platform, row.last_try_at = platform, now
            if views is not None:
                row.views, row.updated_at, row.last_error = views, now, None
            else:
                row.last_error = err
                failed.append(f"{platform}: {url[:50]} — {err}")
            await asyncio.sleep(2.0 if platform in _SLOW else 0.3)  # EnsembleData не частить
        await s.commit()

    # Выгрузка в таблицу — отдельным шагом (без обращений к платным API).
    w = await write_reach_sheet(prev_map)
    if not w.get("ok"):
        return {"ok": False, "error": w.get("error")}
    total, newly = w["total"], w.get("manual_new", 0)

    # алерт админам о сбоях
    if failed:
        from datetime import datetime as _dt

        head = f"⚠️ Охваты: не спарсилось {len(failed)} из {len(link_owner)}:\n"
        body = "\n".join(f"• {x}" for x in failed[:20])
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(admin_id, head + body)
            except Exception:  # noqa: BLE001
                pass
    return {"ok": True, "links": len(link_owner), "failed": len(failed), "frozen": frozen_cnt,
            "manual": manual_cnt + newly, "total": total}


async def run_reach_loop(bot, interval: int = 900) -> None:
    """Раз в сутки (~10:00 МСК) собирает охваты и пишет клиентскую таблицу."""
    import asyncio
    from datetime import datetime

    from bot.single import MSK, _get_state, _set_state

    await asyncio.sleep(120)
    while True:
        try:
            now_msk = datetime.utcnow() + MSK
            today = now_msk.date().isoformat()
            if now_msk.hour >= 10 and await _get_state("reach_run_date") != today:
                res = await reach_run(bot)
                if res.get("ok"):
                    await _set_state("reach_run_date", today)
                    logger.info("reach_run: %s", res)
        except Exception as e:  # noqa: BLE001
            logger.warning("reach loop error: %s", e)
        await asyncio.sleep(interval)


if __name__ == "__main__":  # офлайн self-check парсинга ссылок (без сети): python -m bot.reach
    assert _vk_wall_id("https://vk.ru/wall-237248549_6") == "-237248549_6"
    assert _vk_wall_id("https://m.vk.ru/wall-237248549_5") == "-237248549_5"
    assert _vk_id("https://vk.ru/clip-227378214_456239442") == "-227378214_456239442"
    assert _vk_id("https://vk.ru/clips/singl_zvuk?z=clip-240399977_456239021") == "-240399977_456239021"
    assert _vk_wall_id("https://vk.ru/clip-227378214_456239442") is None
    assert detect_platform("https://vk.ru/wall-237248549_6") == "vk"
    assert _ig_shortcode("https://www.instagram.com/reel/DbAPrfYInqr/?igsh=x") == "DbAPrfYInqr"
    assert _threads_shortcode("https://www.threads.com/@u/post/DbA27injDue?xmt=1") == "DbA27injDue"
    print("reach self-check OK")
