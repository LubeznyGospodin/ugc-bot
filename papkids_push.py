#!/usr/bin/env python3
"""PapKids: собрать посты 5 профилей upload-post и записать в лист «Посты»
через вебхук бота (action grid_write, openById). Автономно, без ручного Apps Script.

- Сбор последовательный (надёжно, без рейт-лимитов).
- Пишем колонки A:O с ряда 2 (обходит баг getLastRow из-за arrayformula ERR/CTR).
- Даты — сериалы Google Sheets (MSK), чтобы формулы дат в «Контроле» работали.
"""
import datetime
import json
import os
import re
import time
import urllib.parse
import urllib.request

def _env(k, default=""):
    v = os.getenv(k)
    if v:
        return v.strip()
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            if ln.startswith(k + "="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    return default


KEY = _env("UPLOAD_POST_API_KEY")
UP = "https://api.upload-post.com/api/uploadposts"
SID = _env("PAPKIDS_SHEET_ID", "1qZHSUgAMkuGSJQWKNm4n0osBGYdrOMoGGuk-0jcuhL4")
SECRET = _env("SHEETS_WEBHOOK_SECRET")
WEBHOOK = _env("SHEETS_WEBHOOK_URL")
VK_TOKEN = _env("VK_TOKEN")
VK_GROUPS = {"papkids_baby": "Ксения Кузнецова", "papkids_child": "Дарья Трофимова",
             "papkids_towels": "Рождественская Лилия", "papkids_mom": "Татьяна Тищенко",
             "papkids_mommy": "Анастасия Китаева"}

PROFILES = {"PapKids1": "Анастасия Китаева", "PapKids2": "Ксения Кузнецова",
            "PapKids3": "Дарья Трофимова", "PapKids4": "Рождественская Лилия", "PapKids5": "Татьяна Тищенко"}
PLATS = ["instagram", "tiktok", "youtube"]
PLAT_LABEL = {"instagram": "Instagram", "tiktok": "TikTok", "youtube": "YouTube"}
HANDLE = {
    "PapKids1|instagram": "papkids.mommy", "PapKids1|tiktok": "papkids.care", "PapKids1|youtube": "@papkids_mommy",
    "PapKids2|instagram": "papkids.baby", "PapKids2|tiktok": "papkids.baby", "PapKids2|youtube": "@papkids_baby",
    "PapKids3|instagram": "papkids.child", "PapKids3|tiktok": "papkids.child", "PapKids3|youtube": "@papkids_child",
    "PapKids4|instagram": "papkids.towels", "PapKids4|tiktok": "papkids.towels", "PapKids4|youtube": "@papkids.towels",
    "PapKids5|instagram": "papkids.mom", "PapKids5|tiktok": "papkids.mom", "PapKids5|youtube": "@papkids_mom",
}
TOVAR = {
    "WW645447": "Полотенце махровое 105×105, молочное", "WW645457": "Полотенце махровое 105×105, бежевое",
    "WW645476": "Полотенце махровое 105×105, серое", "WW645480": "Бутылочка КОРИЧ б/р стекло 160",
    "WW645492": "Бутылочка КОРИЧ б/р стекло 240", "WW645502": "Бутылочка БЕЖ б/р стекло 160",
    "WW645510": "Бутылочка БЕЖ б/р PPSU 160", "WW645511": "Бутылочка бежруч PPSU 240", "WW645517": "Бутылочка бежруч PPSU 300",
    "WW649527": "Ложки, набор 2 шт", "WW649605": "Набор расчёсок, 2 шт", "WW649597": "Расчёска одиночная",
    "WW649591": "Набор расчёсок, дерево", "WW649582": "Расчёска одиночка", "WW649572": "Поильник силиконовый, розовый",
    "WW649559": "Поильник силиконовый, голубой", "WW649543": "Поильник силиконовый, зелёный", "WW649537": "Поильник силиконовый, бежевый",
}
WW = re.compile(r"#?\s*WW\s*(\d{4,})", re.I)   # #ww649537 / WW 649537 — регистр и пробел не важны
YT_KEY = _env("YOUTUBE_API_KEY")


def ww_of(text):
    m = WW.search(text or "")
    return ("WW" + m.group(1)) if m else ""


ACC_URL = {"instagram": "https://www.instagram.com/{h}/",
           "tiktok": "https://www.tiktok.com/@{h}",
           "youtube": "https://www.youtube.com/{h}",
           "vk": "https://vk.ru/{h}"}


def acc_cell(plat, handle):
    """Ячейка «Аккаунт» = хэндл ПЛОСКИМ текстом.
    Кликабельность вешаем отдельно (set_links, rich text): формула =HYPERLINK
    приживалась не во всех ячейках колонки — часть строк оставалась пустой."""
    return handle


def acc_url(plat_label, handle):
    """(Платформа из листа, хэндл) → URL профиля для rich-text-ссылки."""
    k = {"Instagram": "instagram", "TikTok": "tiktok", "YouTube": "youtube", "VK": "vk"}.get(plat_label)
    if not k or not handle:
        return ""
    return ACC_URL[k].format(h=handle if k == "youtube" else handle.lstrip("@"))


# (креатор, платформа-лейбл) → хэндл: чтобы восстановить «Аккаунт» у архивных строк
ACC_BY_CREATOR = {(cr, PLAT_LABEL[p]): HANDLE[f"{prof}|{p}"]
                  for prof, cr in PROFILES.items() for p in PLATS}
VK_SN = {cr: sn for sn, cr in VK_GROUPS.items()}       # креатор → группа VK


def yt_articles(ids):
    """YouTube: артикул лежит в ОПИСАНИИ, а upload-post отдаёт только заголовок.
    Тянем описания батчем через YouTube Data API (1 юнит на 50 видео). → {id: 'WW…'}"""
    out = {}
    if not YT_KEY or not ids:
        return out
    for i in range(0, len(ids), 50):
        chunk = ",".join(ids[i:i + 50])
        try:
            u = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={chunk}&key={YT_KEY}"
            d = json.load(urllib.request.urlopen(u, timeout=20))
            for it in d.get("items", []):
                sn = it.get("snippet", {})
                out[it["id"]] = ww_of(sn.get("description", "")) or ww_of(sn.get("title", ""))
        except Exception:
            pass
    return out

# Порядок колонок листа «Посты» (слева направо). Автоматизация (ID/Источник/Формат) — справа.
HEADERS = ["Дата публикации", "Платформа", "Аккаунт", "Креатор", "Ссылка на пост", "Охваты",
           "Артикул", "Товар", "Реакции", "Комментарии", "ERR", "Дата обновления",
           "ID поста", "Источник", "Формат"]
NOW = (time.time() + 3 * 3600) / 86400.0 + 25569  # сериал текущего момента (MSK)


def _err(reach, likes):
    if isinstance(reach, (int, float)) and reach and isinstance(likes, (int, float)):
        return round(likes / reach, 4)
    return ""


def up(path, params):
    u = UP + path + "?" + urllib.parse.urlencode(params)
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"Authorization": f"Apikey {KEY}"}), timeout=20))


def serial(ts):
    """Дата поста → сериал Google Sheets в MSK (или '')."""
    if not ts:
        return ""
    if str(ts).isdigit():
        u = int(ts)
    else:
        s = ts.replace("Z", "+00:00")
        s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
        u = int(datetime.datetime.fromisoformat(s).timestamp())
    return (u + 3 * 3600) / 86400.0 + 25569  # +3ч MSK


# (креатор, платформа), чью выдачу в этом прогоне УСПЕШНО перечислили — только для них
# можно честно сказать «поста больше нет» (архив). При сбое API группу не трогаем.
OK_GROUPS = set()


def collect():
    rows = []
    for prof, creator in PROFILES.items():
        for plat in PLATS:
            try:
                media = up("/media", {"user": prof, "platform": plat}).get("media", []) or []
                OK_GROUPS.add((creator, PLAT_LABEL[plat]))
            except Exception:
                media = []
            for m in media:
                try:
                    d = up("/post-analytics", {"platform_post_id": m["id"], "platform": plat, "user": prof})
                    met = (((d.get("platforms") or {}).get(plat, {})) or {}).get("post_metrics") or {}
                except Exception:
                    met = {}
                reach, views = met.get("reach"), met.get("views")
                # KPI кампании = сумма ПРОСМОТРОВ (и это же видно в приложении), а не уникальный reach
                ov = views if views is not None else (reach if reach is not None else "")
                ww = ww_of(m.get("caption", ""))
                likes, comments = met.get("likes", ""), met.get("comments", "")
                handle = HANDLE.get(f"{prof}|{plat}", prof)
                rows.append([serial(m.get("timestamp")), PLAT_LABEL[plat],
                             acc_cell(plat, handle), creator, m.get("permalink", ""),
                             ov, ww, TOVAR.get(ww, ""), likes, comments, _err(ov, likes),
                             NOW, str(m["id"]), "Ручной", ""])
                time.sleep(0.25)

    # YouTube: артикул только в описании → добираем батчем через YouTube Data API
    yt_ids = [r[12] for r in rows if r[1] == "YouTube" and not r[6]]
    arts = yt_articles(yt_ids)
    for r in rows:
        if r[1] == "YouTube" and not r[6]:
            r[6] = arts.get(r[12], "")
            r[7] = TOVAR.get(r[6], "")
    return rows


SC_KEY = _env("SCRAPECREATORS_API_KEY")


def sc(path, **params):
    u = "https://api.scrapecreators.com" + path + "?" + urllib.parse.urlencode(params)
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"x-api-key": SC_KEY}), timeout=60))


IG_CODE = re.compile(r"/(?:reel|p)/([^/?]+)")


def ig_code(url):
    m = IG_CODE.search(str(url or ""))
    return m.group(1) if m else ""


def collect_ig(known=()):
    """ДОБОР Instagram через ScrapeCreators: upload-post отдаёт только «свои» публикации
    (у Дарьи 2 из 10), лента профиля — все, включая совместки. Берём ТОЛЬКО те посты,
    которых нет в выдаче upload-post: его счётчик просмотров = как в приложении, а
    video_view_count у SC вдвое ниже, смешивать нельзя.
    1 кредит ScrapeCreators на аккаунт за прогон.
    ponytail: первая страница ленты (~12 постов); станет больше — добавить курсор
    page_info.end_cursor."""
    rows = []
    if not SC_KEY:
        return rows
    known = set(known)
    for prof, creator in PROFILES.items():
        h = HANDLE[f"{prof}|instagram"]
        try:
            tl = sc("/v1/instagram/profile", handle=h)["data"]["user"]["edge_owner_to_timeline_media"]
            OK_GROUPS.add((creator, "Instagram"))
        except Exception:
            continue
        for e in tl.get("edges") or []:
            n = e.get("node") or {}
            if str(n.get("shortcode")) in known:
                continue
            cap = ((n.get("edge_media_to_caption") or {}).get("edges") or [{}])[0].get("node", {}).get("text", "")
            ww = ww_of(cap)
            views = n.get("video_view_count")
            views = views if views is not None else ""      # у фото и каруселей счётчика нет
            likes = (n.get("edge_liked_by") or {}).get("count", "")
            comments = (n.get("edge_media_to_comment") or {}).get("count", "")
            kind = "reel" if n.get("is_video") else "p"
            rows.append([serial(n.get("taken_at_timestamp")), "Instagram", h, creator,
                         f"https://www.instagram.com/{kind}/{n.get('shortcode')}/", views, ww,
                         TOVAR.get(ww, ""), likes, comments, _err(views, likes), NOW,
                         str(n.get("shortcode")), "Ручной", ""])
    return rows


def vk_api(method, params):
    p = dict(params)
    p["access_token"] = VK_TOKEN
    p["v"] = "5.199"
    return json.load(urllib.request.urlopen("https://api.vk.com/method/" + method + "?" + urllib.parse.urlencode(p), timeout=20))


def collect_vk():
    """VK-клипы со стен 5 групп → строки A:O (просмотры из video-вложений)."""
    rows = []
    if not VK_TOKEN:
        return rows
    try:
        r = vk_api("groups.getById", {"group_ids": ",".join(VK_GROUPS)})
        gs = r["response"]["groups"] if isinstance(r["response"], dict) else r["response"]
        name2id = {g["screen_name"]: g["id"] for g in gs}
    except Exception:
        return rows
    for sn, creator in VK_GROUPS.items():
        gid = name2id.get(sn)
        if not gid:
            continue
        try:
            items = vk_api("wall.get", {"owner_id": -gid, "count": 100})["response"].get("items", [])
            OK_GROUPS.add((creator, "VK"))
        except Exception:
            items = []
        for p in items:
            for a in (p.get("attachments") or []):
                if a.get("type") != "video":
                    continue
                v = a["video"]
                vid = f"{v.get('owner_id')}_{v.get('id')}"
                ww = ww_of(p.get("text", "")) or ww_of(v.get("description", "")) or ww_of(v.get("title", ""))
                views = v.get("views", "")
                likes = (p.get("likes") or {}).get("count", "")
                comments = (p.get("comments") or {}).get("count", "")
                rows.append([serial(p.get("date")), "VK", sn, creator,
                             f"https://vk.com/video{vid}", views, ww, TOVAR.get(ww, ""),
                             likes, comments, _err(views, likes), NOW, vid, "Ручной", ""])
    return rows


def _iso_to_serial(v):
    """Значение даты из grid_dump (ISO-строка/число) → сериал Sheets (MSK)."""
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str) and v:
        try:
            dt = datetime.datetime.fromisoformat(v.replace("Z", "+00:00"))
            return (int(dt.timestamp()) + 3 * 3600) / 86400.0 + 25569
        except ValueError:
            return v
    return v


def _webhook(payload, timeout=120):
    data = json.dumps({"secret": SECRET, "sheet_id": SID, **payload}).encode()
    last = None
    for a in range(4):
        try:
            req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except Exception as e:
            last = e
            time.sleep(3 * (a + 1))
    raise last


def merge_archive(fresh):
    """АРХИВ: посты, ушедшие из выдачи платформ, не выбрасываем — оставляем с
    последними цифрами и пометкой «Удалён» (охват кампании/премии/CTR не сгорают).
    Помечаем только если выдачу этой (креатор, платформа) в этом прогоне реально
    перечислили (OK_GROUPS) — иначе сбой API превратился бы в ложные «удаления»."""
    try:
        old = _webhook({"action": "grid_dump", "sheet_name": "Посты"}).get("values", [])[1:]
    except Exception:
        return fresh
    fresh_ids = {str(r[12]) for r in fresh}
    archived = []
    for row in old:
        if not row or not row[0] or len(row) < 15 or str(row[12]) in fresh_ids:
            continue
        r = list(row[:15])
        r[0] = _iso_to_serial(r[0])
        r[11] = _iso_to_serial(r[11])
        # «Аккаунт» мог потеряться в прошлых прогонах — восстанавливаем по (креатор, платформа)
        plat = r[1]
        if not str(r[2]).strip():
            r[2] = VK_SN.get(r[3], "") if plat == "VK" else ACC_BY_CREATOR.get((r[3], plat), "")
        if (r[3], plat) in OK_GROUPS:
            r[13] = "Удалён"          # проверили выдачу — поста там нет
        archived.append(r)
    return fresh + archived


def push(rows):
    # clear:true чистит лист (убирает старые колонки/формулы), пишем шапку+данные с ряда 1.
    payload = {"secret": SECRET, "action": "grid_write", "sheet_id": SID,
               "sheet_name": "Посты", "clear": True, "row": 1, "rows": [HEADERS] + rows}
    data = json.dumps(payload).encode()
    last = None
    for attempt in range(4):                         # Apps Script иногда 404-ит на редиректе — ретраим
        try:
            req = urllib.request.Request(WEBHOOK, data=data, headers={"Content-Type": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=120))
        except Exception as e:
            last = e
            time.sleep(3 * (attempt + 1))
    raise last


def link_accounts(rows):
    """Колонку «Аккаунт» делаем кликабельной поверх текста (rich text).
    Вызывать ПОСЛЕ push — grid_write перезаписывает ячейки и сносит ссылки."""
    links = [[r[2], acc_url(r[1], str(r[2]))] for r in rows]
    try:
        return _webhook({"action": "set_links", "sheet_name": "Посты",
                         "row": 2, "col": 3, "links": links}, timeout=180)
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def snapshot(rows):
    """Журнал замеров: строка на каждый прогон → лист «Замеры».
    Даёт честный «охват, пришедший в дату» (дельты между замерами) —
    в отличие от среза по дате публикации."""
    total = sum(r[5] for r in rows if isinstance(r[5], (int, float)))
    by = {}
    for p in ("Instagram", "YouTube", "TikTok", "VK"):
        by[p] = sum(r[5] for r in rows if r[1] == p and isinstance(r[5], (int, float)))
    row = [NOW, total, by["Instagram"], by["YouTube"], by["TikTok"], by["VK"], len(rows)]
    payload = {"secret": SECRET, "action": "grid_write", "sheet_id": SID,
               "sheet_name": "Замеры", "rows": [row]}  # без row → append в конец
    try:
        req = urllib.request.Request(WEBHOOK, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=60))
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


def _num(n):
    return f"{int(n):,}".replace(",", " ")


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]
# Пожелание в конце отчёта — по одному на день (крутится по дню года, без повторов подряд).
WISHES = [
    "Пусть сегодня у вас случаются невероятные чудесные везения!",
    "Пусть сегодня на вашем сердце будет тепло и спокойно.",
    "Пусть сегодня вас окутывает счастье!",
    "Пусть день начнётся с хорошей новости и ею же закончится.",
    "Пусть всё сложное сегодня решится само собой.",
    "Пусть сегодня везёт даже в мелочах — особенно в мелочах.",
    "Пусть сегодня будет много поводов улыбнуться.",
    "Пусть день будет лёгким, как утренний свет.",
    "Пусть сегодня всё идёт вам навстречу.",
    "Пусть случится что-то маленькое, но очень приятное.",
    "Пусть сегодня хватит сил на всё, что задумали.",
    "Пусть день принесёт спокойствие и пару приятных сюрпризов.",
    "Пусть удача сегодня ходит за вами по пятам.",
    "Пусть в этом дне найдётся минутка только для себя.",
    "Пусть сегодня всё получается с первого раза.",
    "Пусть день будет добрым к вам и к вашим планам.",
    "Пусть сегодня встречаются только хорошие люди.",
    "Пусть этот день окажется лучше, чем вы ожидали.",
    "Пусть сегодня будет тепло — и на улице, и внутри.",
    "Пусть все двери сегодня открываются с первого толчка.",
    "Пусть сегодня радуют и цифры, и люди.",
    "Пусть день пройдёт спокойно и закончится вовремя.",
    "Пусть будет ощущение, что всё на своих местах.",
    "Пусть сегодня дел будет ровно столько, сколько хочется.",
    "Пусть этот день подарит хотя бы один момент чистой радости.",
    "Пусть сегодня всё складывается само, без усилий.",
    "Пусть в этом дне будет вкусный кофе и хорошие вести.",
    "Пусть хватит времени на важное и не хватит на суету.",
    "Пусть день будет щедрым на приятные мелочи.",
    "Пусть всё, что вы делаете сегодня, возвращается добром.",
]


def report_text():
    """Утренний отчёт по данным ТАБЛИЦЫ (не по памяти прогона — что в листе, то и в отчёте).
    Разметка HTML (заголовок жирным, пожелание цитатой) — слать с parse_mode='HTML'.
    «+х» у роликов — опубликованных вчера, у охвата — прирост между двумя последними
    замерами (при суточном сборе это ровно вчерашние сутки)."""
    rows = [r for r in _webhook({"action": "grid_dump", "sheet_name": "Посты"})["values"][1:] if r and r[0]]
    total = sum(r[5] for r in rows if isinstance(r[5], (int, float)))
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).date()
    yday = (today - datetime.timedelta(days=1)).isoformat()
    new_posts = sum(1 for r in rows if str(r[0])[:10] == yday)
    no_ww = sum(1 for r in rows if not str(r[6]).strip())
    try:                                   # прирост охвата — по журналу «Замеры»
        z = [r for r in _webhook({"action": "grid_dump", "sheet_name": "Замеры"})["values"][1:] if r and r[1]]
        d_reach = int(z[-1][1] - z[-2][1]) if len(z) > 1 else 0
    except Exception:
        d_reach = 0
    avg = round(total / len(rows)) if rows else 0

    # WB: всего — из «WB · ROI», за вчера — отдельным запросом к API за одну дату
    wb = [0, 0, 0, 0]
    try:
        roi = _webhook({"action": "grid_dump", "sheet_name": "WB · ROI"})["values"][1:]
        wb = [sum(r[i] for r in roi if len(r) > i and isinstance(r[i], (int, float)))
              for i in (3, 4, 5, 6)]          # переходы, корзина, заказы, выручка
    except Exception:
        pass
    try:
        import papkids_wb_queries as wq
        y = wq.totals(yday, yday)
    except Exception:
        y = {"open": 0, "cart": 0, "orders": 0, "revenue": 0}
    ctr = f"{100 * wb[0] / total:.2f}".replace(".", ",") if total else "0,00"
    wish = WISHES[today.timetuple().tm_yday % len(WISHES)]
    return (f"<b>Отчет PapKids {today.day} {MONTHS[today.month - 1]}</b>\n\n"
            f"Всего роликов: {len(rows)} (+{new_posts})\n"
            f"Всего охвата: {_num(total)} (+{_num(max(0, d_reach))})\n\n"
            f"Средний охват на ролик: {_num(avg)}\n\n"
            f"Кол-во роликов без артикула: {no_ww}\n\n"
            f"Переходов на WB: {_num(wb[0])} (+{_num(y['open'])})\n"
            f"CTR общий: {ctr}%\n"
            f"В корзину: {_num(wb[1])} (+{_num(y['cart'])})\n"
            f"Заказы: {_num(wb[2])} (+{_num(y['orders'])})\n"
            f"Выручка по заказам: {_num(wb[3])} ₽ (+{_num(y['revenue'])} ₽)\n\n"
            f"<blockquote>{wish}</blockquote>")


def run():
    """Полный прогон: сбор → merge с архивом → запись → журнал замеров. → сводка."""
    up = collect()
    extra = collect_ig(known=[ig_code(r[4]) for r in up if r[1] == "Instagram"])
    rows = merge_archive(up + extra + collect_vk())
    live = sum(1 for r in rows if r[13] != "Удалён")
    total = sum(r[5] for r in rows if isinstance(r[5], (int, float)))
    res = push(rows)
    lnk = link_accounts(rows)
    snap = snapshot(rows)
    return (f"постов {len(rows)} (живых {live}, добор IG {len(extra)}), Σ охват {total}, "
            f"sheet {res.get('ok')}, ссылок {lnk.get('set')}, замер {snap.get('ok')}")


def _selfcheck():
    assert acc_url("Instagram", "papkids.baby") == "https://www.instagram.com/papkids.baby/"
    assert acc_url("YouTube", "@papkids_baby") == "https://www.youtube.com/@papkids_baby"
    assert acc_url("TikTok", "papkids.mom") == "https://www.tiktok.com/@papkids.mom"
    assert acc_url("VK", "papkids_mom") == "https://vk.ru/papkids_mom"
    assert acc_url("Instagram", "") == "" and acc_url("Telegram", "x") == ""
    assert ACC_BY_CREATOR[("Ксения Кузнецова", "TikTok")] == "papkids.baby"
    assert VK_SN["Татьяна Тищенко"] == "papkids_mom"
    return "selfcheck ok"


if __name__ == "__main__":
    import sys
    print(_selfcheck() if "--check" in sys.argv else run())
