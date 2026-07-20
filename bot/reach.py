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


def _http_json(url: str, timeout: int = 45) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
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
    vid = _vk_id(url)
    if not vid:
        return None, "не распознал vk id"
    if not settings.vk_token:
        return None, "нет VK_TOKEN"
    try:
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


def _ed_get(path: str, params: dict) -> dict:
    """EnsembleData с ретраями: их IG/TikTok-бэкенд под нагрузкой временно отдаёт 495/429/5xx."""
    import time

    url = "https://ensembledata.com/apis" + path + "?" + urllib.parse.urlencode(
        {**params, "token": settings.ensembledata_token}
    )
    last = None
    for attempt in range(4):
        try:
            return _http_json(url, timeout=60)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (495, 429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(4 * (attempt + 1))  # 4,8,12с
                continue
            raise
    raise last


def fetch_instagram(url: str) -> tuple[int | None, str | None]:
    code = _ig_shortcode(url)
    if not code:
        return None, "не распознал ig shortcode"
    if not settings.ensembledata_token:
        return None, "нет ENSEMBLEDATA_TOKEN"
    try:
        d = _ed_get("/instagram/post/details", {"code": code})
        data = d.get("data")
        if not isinstance(data, dict):
            return None, "рилс недоступен (удалён/скрыт)"
        pc = data.get("video_play_count") or data.get("video_view_count") or 0
        return int(pc), None
    except Exception as e:  # noqa: BLE001
        return None, f"instagram: {e}"


def fetch_tiktok(url: str) -> tuple[int | None, str | None]:
    if not settings.ensembledata_token:
        return None, "нет ENSEMBLEDATA_TOKEN"
    try:
        d = _ed_get("/tt/post/info", {"url": url})
        data = d.get("data")
        rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        if not rows or not isinstance(rows[0], dict):
            return None, "видео недоступно"
        st = rows[0].get("statistics", {}) or {}
        return int(st.get("play_count", 0)), None
    except Exception as e:  # noqa: BLE001
        return None, f"tiktok: {e}"


def fetch_unsupported(url: str) -> tuple[int | None, str | None]:
    """Threads/Facebook — авто-источник пока не подключён, помечаем на ручной ввод."""
    return None, "площадка пока вручную (Threads/FB)"


def fetch_telegram(url: str) -> tuple[int | None, str | None]:
    """Просмотры публичного поста t.me/<channel>/<id> — из встраиваемого виджета."""
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
    "threads": fetch_unsupported,
    "facebook": fetch_unsupported,
}
# Площадки через EnsembleData — им нужна пауза побольше (их IG/TikTok-бэкенд рейт-лимитит).
_SLOW = {"instagram", "tiktok"}


def fetch_reach(url: str) -> tuple[str, int | None, str | None]:
    """→ (платформа, views|None, error|None)."""
    pl = detect_platform(url)
    fn = _FETCHERS.get(pl)
    if fn is None:
        return pl, None, "площадка не поддерживается"
    views, err = fn(url)
    return pl, views, err


# ── Оркестрация: сбор всех ссылок → БД → клиентская таблица ────────────────────
_URL_RE = re.compile(r"https?://[^\s,]+")


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
    async with get_session() as s:
        existing = {r.url: r for r in (await s.execute(select(ReachRow))).scalars().all()}
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
        rows_db = [r for r in (await s.execute(select(ReachRow).where(ReachRow.active.is_(True)))).scalars().all()]

    # строки для таблицы + сумма
    def flag(r) -> str:
        if r.first_seen and (now - r.first_seen).days >= freeze_days:
            return f"🏁 финал ({freeze_days}д)"
        if r.views is None:
            return "⚠️ нет данных"
        if r.last_error:  # есть прошлое значение, но обновить не смогли
            return "⚠️ не обновилось"
        if r.views >= 10000:
            return "🔥 >10к"
        return "✓"

    rows_db.sort(key=lambda r: (-(r.views or -1)))
    sheet_rows = [
        {
            "creator": r.creator or "—",
            "platform": r.platform,
            "url": r.url,
            "views": r.views,
            "updated": (r.updated_at + MSK).strftime("%d.%m %H:%M") if r.updated_at else "",
            "flag": flag(r),
        }
        for r in rows_db
    ]
    total = sum(r.views for r in rows_db if r.views is not None)

    try:
        await sheets_client.reach_write(settings.reach_sheet_id, sheet_rows, total)
    except SheetsError as e:
        logger.warning("reach_write failed: %s", e)
        return {"ok": False, "error": f"запись таблицы: {e}"}

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
    return {"ok": True, "links": len(link_owner), "failed": len(failed), "frozen": frozen_cnt, "total": total}


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
