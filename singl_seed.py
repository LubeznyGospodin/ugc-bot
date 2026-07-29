"""Посев Сингла: уникализированные копии топ-роликов кампании → аккаунты брендов.

Схема «в разнобой»: аккаунт = профиль×площадка (8 шт: 2 бренда × TikTok/IG/YouTube/VK).
Каждая публикация — СВОЯ уникализированная копия исходника (uniq_bot/uniquify.py,
preset=strong, mirror=False — на роликах текст). Один исходник не повторяется на
аккаунте в пределах дня; когда свежие кончились — реюз (кап 10 копий/исходник).

Темп на аккаунт/день: 29.07 — 2, 30.07 — 3, 31.07 — 4, дальше — 5.
TikTok/IG/YouTube — через upload-post (отложка scheduled_date). VK — напрямую:
video.save в сообщество + отложенный wall.post (юзер-токен SINGL_VK_USER).

Профили upload-post:
  Single1_skazhi_pesney  — «Скажи песней»   (singl.zvuk / skazhi_pesney / @skazhi.pesney)
  Single_pesnya_vpodarok — «Песня в подарок» (pesnya.vpodarok / pesnya_v_podar_ok / @pesnya.vpodarok)

Команды:
  .venv/bin/python singl_seed.py wave   # спланировать+запостить публикации на сегодня (launchd 09:00)
  .venv/bin/python singl_seed.py plan   # перезаписать лист «План посева» из state
  .venv/bin/python singl_seed.py stats  # просмотры → «Посевы (факт)» + главный лист + ТГ (launchd 20:00)
  .venv/bin/python singl_seed.py links  # ссылки свежевышедших постов → база охвата (launchd 13/17/21/23:45)

Состояние — ~/Desktop/singl_seed/state.json. Исходники — src/<имя>.mp4: новый ролик
креатора = положить файл в src/ и добавить запись в SOURCES.
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import dotenv_values

ENV = dotenv_values(Path(__file__).parent / ".env")
API_KEY = ENV["UPLOAD_POST_API_KEY"]
UPLOAD_URL = "https://api.upload-post.com/api/upload"
WEBHOOK = ("https://script.google.com/macros/s/AKfycbyDWDeOWwdQFT-UHJSdBaHvcXCNF4Dy"
           "S1rA1Vq61MrKk50DxAbk0mWLlDZUF-1saGUY/exec")
SECRET = "YfNLxVxjB5UddfEpf-xfcRjC_ih4MusfJg1QDxVt4o0"
SHEET_ID = "14iH1s6bctklEuQ5kVGgjolvrP4XAKPScZkhTcz_-qRY"

SEED_DIR = Path.home() / "Desktop" / "singl_seed"
STATE = SEED_DIR / "state.json"

A = "Single1_skazhi_pesney"
B = "Single_pesnya_vpodarok"
SHORT = {A: "SP", B: "PV"}
LABEL = {A: "Посев · Скажи песней", B: "Посев · Песня в подарок"}
ACCOUNTS = [(p, pl) for p in (A, B) for pl in ("tiktok", "instagram", "youtube", "vk")]

# VK — напрямую (юзер-токен админа сообществ, бессрочный): video.save + отложенный wall.post
VK_TOKEN = ENV.get("SINGL_VK_USER", "")
VK_GROUPS = {A: 240399977, B: 240401430}  # skazhi_pesney / pesnya.vpodarok


def _vk(method: str, **params):
    params.update(access_token=VK_TOKEN, v="5.199")
    r = requests.post(f"https://api.vk.com/method/{method}", data=params, timeout=120).json()
    if "error" in r:
        raise RuntimeError(f"{method}: {r['error'].get('error_msg')}")
    return r["response"]


def vk_publish(profile: str, path: Path, caption: str, when_iso: str) -> tuple[int, int]:
    """Залить видео в сообщество и поставить отложенный пост на стену. → (video_id, post_id)."""
    g = VK_GROUPS[profile]
    up = _vk("video.save", group_id=g, name=caption.split("#")[0].strip()[:100],
             description=caption)
    with path.open("rb") as fh:
        vr = requests.post(up["upload_url"], files={"video_file": fh}, timeout=600).json()
    vid = vr.get("video_id") or up["video_id"]
    ts = int(datetime.strptime(when_iso[:16], "%Y-%m-%dT%H:%M").timestamp())
    post = _vk("wall.post", owner_id=-g, from_group=1, message=caption,
               attachments=f"video-{g}_{vid}", publish_date=ts)
    return vid, post.get("post_id", 0)

# темп: публикаций на аккаунт в день
RAMP = {"2026-07-29": 2, "2026-07-30": 3, "2026-07-31": 4}
RAMP_DEFAULT = 5

# исходники: имя файла в src/ → (креатор, ссылка-источник)
SOURCES = {
    "mazitova": ("Регина Мазитова", "https://youtube.com/shorts/83RACeOTO4U"),
    "tishchenko": ("Татьяна Тищенко", "https://vk.ru/clip-186704538_456239176"),
    "arina1": ("Арина", "https://vt.tiktok.com/ZSXpCncGB/"),
    "vahrameeva": ("Алина Вахрамеева", "https://vm.tiktok.com/ZGd9K1F9e/"),
    "kuptsova": ("Анастасия Купцова", "https://youtu.be/UfHQ_KQJuRw"),
    "arina2": ("Арина", "https://vt.tiktok.com/ZSXpCQkLp/"),
    "bauer": ("Миша Бауэр", "https://youtube.com/shorts/iqQ4q72fhHw"),
    "gulakova": ("Анастасия Гулакова", "https://youtube.com/shorts/To2GrbmytGw"),
    "trofimova": ("Дарья Трофимова", "https://vk.ru/clip-239123228_456239040"),
    "charaeva": ("Анастасия Чараева", "https://vm.tiktok.com/ZN81r2FsG/"),
    "zainieva": ("Наталья Зайниева", "https://youtube.com/shorts/svLRFI_E0eI"),
    "lazev": ("Владислав Лазев", "https://youtube.com/shorts/o2T-stKdHSs"),
    "gulakova2": ("Анастасия Гулакова", "https://youtube.com/shorts/Ab0bQwBOP0o"),
    "tishchenko2": ("Татьяна Тищенко", "https://vk.ru/clip-186704538_456239169"),
    "liliya": ("Рождественская лилия", "https://vk.ru/clip627774495_456239889"),
    "zelenkova": ("Анастасия Зеленкова", "https://youtube.com/shorts/PLRn0RmCRhc"),
    "shilova": ("Ирина Шилова", "https://youtube.com/shorts/4NuOySZ0XMA"),
}

CAPTIONS = {
    "mazitova": "Лучший подарок — тот, что нельзя купить в магазине 🎁 Персональная песня для вашего человека 🎶 #песнявподарок #подарок #сюрприз #идеяподарка",
    "tishchenko": "Она услышала песню про себя и расплакалась 🥹 Песня в подарок — эмоция, которую запомнят навсегда 🎶 #песнявподарок #подароксюрприз #эмоции",
    "arina1": "Что подарить человеку, у которого всё есть? Песню про него 🎤 #песнявподарок #идеяподарка #сюрприз",
    "vahrameeva": "Подарок за 5 минут, а мурашки на всю жизнь 🎶 Персональная песня на заказ #песнявподарок #подарок #мурашки",
    "kuptsova": "Такой подарок не передарят 😄 Песня, написанная специально про вашего человека 🎁 #песнявподарок #подарокдевушке #подарокмаме",
    "arina2": "Реакция на песню про себя — бесценна 🥹🎶 Закажи песню в подарок #песнявподарок #реакция #сюрприз",
    "bauer": "Забудь про носки и сертификаты — подари песню 🎸 #песнявподарок #идеяподарка #подарокмужу",
    "gulakova": "Хочешь довести до слёз счастья? Подари песню про вас двоих 🎶 #песнявподарок #подарок #любовь",
    "trofimova": "Когда слова заканчиваются — за дело берётся музыка 🎼 Песня про вашего человека #песнявподарок #подарок #идеяподарка",
    "charaeva": "Это не просто песня — это ваша история в куплетах 🎶 #песнявподарок #сюрприз #подарокдевушке",
    "zainieva": "Подарок, который слушают на репите 🔁 Персональная песня на заказ #песнявподарок #подарок #музыка",
    "lazev": "Хотел удивить — удивил до слёз 🥹 Песня в подарок работает всегда #песнявподарок #сюрприз #идеяподарка",
    "gulakova2": "10 секунд — и мурашки 🎶 Песня, написанная про вас #песнявподарок #мурашки #подарок",
    "tishchenko2": "Самый душевный подарок этого лета 🎁 Песня про вашего человека #песнявподарок #подарокмаме #эмоции",
    "liliya": "Песня в подарок — когда хочется большего, чем букет 💐➡️🎶 #песнявподарок #идеяподарка #подарокжене",
    "zelenkova": "Ваши воспоминания, ваши имена — ваша песня 🎤 #песнявподарок #подарок #сюрприз",
    "shilova": "Такое не забывают: песня в честь любимого человека 🎶 #песнявподарок #эмоции #подарок",
}


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _acc_key(profile: str, platform: str) -> str:
    return f"{profile}|{platform}"


def _ramp(date_s: str) -> int:
    return RAMP.get(date_s, RAMP_DEFAULT)


def pick_sources(state: dict, date_s: str) -> list[tuple[str, str, str, str]]:
    """Жадный латинский квадрат: → [(profile, platform, source, iso_datetime)].

    Правила: исходник не повторяется на аккаунте; в пределах дня — сначала те,
    что сегодня ещё не выходили; при прочих равных — наименее использованный.
    """
    hist = state.setdefault("acct_hist", {})
    day = state.setdefault("days", {}).setdefault(date_s, {})
    global_use: dict[str, int] = {}
    for used in hist.values():
        for s in used:
            global_use[s] = global_use.get(s, 0) + 1
    used_today = set(day.get("_sources", []))

    now = datetime.now()
    target = _ramp(date_s)
    plan = []
    for k in range(target):
        for idx, (profile, platform) in enumerate(ACCOUNTS):
            key = _acc_key(profile, platform)
            if day.get(key, 0) + sum(1 for p in plan if p[0] == profile and p[1] == platform) >= target - 0:
                continue
            done = day.get(key, 0)
            slot_i = done + sum(1 for p in plan if p[0] == profile and p[1] == platform)
            if slot_i > k:
                continue
            if slot_i != k:
                continue
            today_on_acct = {p[2] for p in plan if p[0] == profile and p[1] == platform}
            cands = [s for s in SOURCES if s not in hist.get(key, [])
                     and (SEED_DIR / "src" / f"{s}.mp4").exists()]
            if cands:
                cands.sort(key=lambda s: (s in used_today, global_use.get(s, 0)))
            else:
                # исходники на аккаунте закончились — реюз: оригиналы мы не постим,
                # каждая публикация всё равно свежая уникализация; кап 10 копий/исходник
                acct_use = {s: hist.get(key, []).count(s) for s in SOURCES}
                cands = [s for s in SOURCES if (SEED_DIR / "src" / f"{s}.mp4").exists()
                         and global_use.get(s, 0) < 10 and s not in today_on_acct]
                cands.sort(key=lambda s: (acct_use[s], s in used_today, global_use.get(s, 0)))
            if not cands:
                print(f"{key}: база исчерпана (кап 10 копий) — слот {k + 1} пропущен")
                continue
            src = cands[0]
            # слоты равномерно по дню 10:30–20:30 (утро/день/вечер при любом темпе):
            # N=2 → 10:30, 20:30; N=3 → 10:30, 15:30, 20:30; N=5 → каждые 2.5ч.
            # +12 мин сдвига на аккаунт; прошедшее время двигаем вперёд от «сейчас»
            step_h = 10 / max(target - 1, 1)
            when = datetime.strptime(date_s, "%Y-%m-%d").replace(hour=10, minute=30) \
                + timedelta(hours=step_h * k, minutes=12 * idx)
            if when < now + timedelta(minutes=30):
                when = now + timedelta(minutes=35 + 12 * idx + 90 * slot_i)
            plan.append((profile, platform, src, when.strftime("%Y-%m-%dT%H:%M:00+03:00")))
            used_today.add(src)
            global_use[src] = global_use.get(src, 0) + 1
    return plan


def seed_write(rows: list[dict]) -> None:
    """Апсерт строк в главный лист reach-таблицы (идут в «Общий охват»)."""
    if not rows:
        return
    r = requests.post(WEBHOOK, json={
        "secret": SECRET, "action": "seed_write", "sheet_id": SHEET_ID, "rows": rows,
    }, timeout=120)
    print("seed_write:", r.status_code, r.text[:120])


def links() -> None:
    """Подтянуть ссылки свежевышедших upload-post-постов в базу (охват добьёт stats).

    Гоняется launchd несколько раз в день: ссылка попадает в «Общий охват»
    сразу после публикации, не дожидаясь вечернего сбора.
    """
    from papkids_uploadpost import media

    state = load_state()
    seen = set(state.setdefault("seen_posts", []))
    now = datetime.now()
    new_rows = []
    for profile in (A, B):
        for platform in ("tiktok", "instagram", "youtube"):
            try:
                posts = media(profile, platform)
            except Exception as e:  # noqa: BLE001
                print(f"{profile}/{platform}: media failed: {e}")
                continue
            for p in posts:
                pid = f"{platform}:{p['id']}"
                if pid in seen or not p.get("permalink"):
                    continue
                seen.add(pid)
                new_rows.append({"date_added": now.strftime("%d.%m"), "creator": LABEL[profile],
                                 "platform": platform, "url": p["permalink"], "views": 0,
                                 "updated": now.strftime("%d.%m %H:%M")})
    seed_write(new_rows)
    state["seen_posts"] = sorted(seen)
    save_state(state)
    print(f"новых ссылок: {len(new_rows)}")


def wave() -> None:
    """Спланировать и зашедулить публикации на сегодня."""
    sys.path.insert(0, str(Path.home() / "Desktop" / "uniq_bot"))
    from uniquify import probe, uniquify

    state = load_state()
    date_s = datetime.now().strftime("%Y-%m-%d")
    plan = pick_sources(state, date_s)
    print(f"{date_s}: к отправке {len(plan)} публикаций")
    day = state["days"][date_s]
    for profile, platform, src, when in plan:
        copy = f"{src}_{SHORT[profile]}_{platform[:2]}_{date_s[5:].replace('-', '')}.mp4"
        dst = SEED_DIR / "out" / copy
        if not dst.exists():
            meta = probe(str(SEED_DIR / "src" / f"{src}.mp4"))
            asyncio.run(uniquify(str(SEED_DIR / "src" / f"{src}.mp4"), str(dst),
                                 meta, preset="strong", mirror=False))
        caption = CAPTIONS.get(src, next(iter(CAPTIONS.values())))
        if platform == "vk":
            try:
                vid, post_id = vk_publish(profile, dst, caption, when)
            except Exception as e:  # noqa: BLE001
                print(f"{copy} -> {profile}/vk @{when}: FAIL {e}")
                continue
            print(f"{copy} -> {profile}/vk @{when}: video-{VK_GROUPS[profile]}_{vid}, отложка {post_id}")
            key = _acc_key(profile, platform)
            day[key] = day.get(key, 0) + 1
            day.setdefault("_sources", []).append(src)
            state.setdefault("acct_hist", {}).setdefault(key, []).append(src)
            state.setdefault("vk_videos", {}).setdefault(str(VK_GROUPS[profile]), []).append(vid)
            state.setdefault("published", {})[copy] = {
                "when": when, "profile": profile, "platform": "vk", "src": src,
                "job_id": f"vk{vid}"}
            save_state(state)
            # ссылка известна сразу — в базу охвата не дожидаясь выхода
            seed_write([{"date_added": date_s[8:] + "." + date_s[5:7], "creator": LABEL[profile],
                         "platform": "vk", "url": f"https://vk.com/video-{VK_GROUPS[profile]}_{vid}",
                         "views": 0, "updated": datetime.now().strftime("%d.%m %H:%M")}])
            continue
        yt_title = caption.split("#")[0].strip()
        if len(yt_title) > 100:
            yt_title = yt_title[:97].rsplit(" ", 1)[0] + "…"
        with dst.open("rb") as fh:
            r = requests.post(
                UPLOAD_URL,
                headers={"Authorization": f"Apikey {API_KEY}"},
                data={"user": profile, "platform[]": [platform],
                      "title": caption, "youtube_title": yt_title,
                      "youtube_description": caption, "scheduled_date": when},
                files={"video": (copy, fh, "video/mp4")},
                timeout=300,
            )
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:200]}
        print(f"{copy} -> {profile}/{platform} @{when}: HTTP {r.status_code} "
              f"{json.dumps(body, ensure_ascii=False)[:160]}")
        if r.status_code == 429:
            print("ЛИМИТ АПЛОАДОВ — стоп")
            break
        if r.status_code in (200, 202):
            key = _acc_key(profile, platform)
            day[key] = day.get(key, 0) + 1
            day.setdefault("_sources", []).append(src)
            state.setdefault("acct_hist", {}).setdefault(key, []).append(src)
            state.setdefault("published", {})[copy] = {
                "when": when, "profile": profile, "platform": platform, "src": src,
                "job_id": body.get("job_id") or body.get("request_id")}
            save_state(state)
    plan_sheet(state)


def plan_sheet(state: dict | None = None) -> None:
    """Перезаписать лист «План посева» из state.published."""
    state = state or load_state()
    rows = [["Дата (МСК)", "Профиль", "Площадка", "Файл-копия", "Исходник (креатор)",
             "Подпись", "Статус"]]
    pub = state.get("published", {})
    for copy, info in sorted(pub.items(), key=lambda kv: kv[1]["when"]):
        src = info.get("src") or copy.split("_")[0]
        creator, url = SOURCES.get(src, ("", ""))
        plats = info.get("platform") or "TikTok+IG+YouTube"
        rows.append([info["when"].replace("T", " ")[:16], info["profile"], plats,
                     copy, f"{creator} · {url}", CAPTIONS.get(src, ""), "⏰ запланирован"])
    r = requests.post(WEBHOOK, json={
        "secret": SECRET, "action": "grid_write", "sheet_id": SHEET_ID,
        "sheet_name": "План посева", "clear": True, "row": 1, "rows": rows,
    }, timeout=120)
    print("план:", r.status_code, r.text[:120])


def stats() -> None:
    """Просмотры всех постов обоих профилей → «Посевы (факт)» + главный лист + ТГ."""
    from papkids_uploadpost import analytics, media

    state = load_state()
    today = datetime.now().strftime("%d.%m.%Y")
    rows, totals = [], {A: 0, B: 0}
    # ponytail: 1 вызов media + 1 analytics на пост за прогон; при >50 постах перейти на кэш id
    for profile in (A, B):
        for platform in ("tiktok", "instagram", "youtube"):
            try:
                posts = media(profile, platform)
            except Exception as e:  # площадка могла не отдать — не роняем весь сбор
                print(f"{profile}/{platform}: media failed: {e}")
                continue
            for p in posts:
                try:
                    m = analytics(profile, platform, p["id"])
                except Exception as e:
                    print(f"{profile}/{platform}/{p['id']}: analytics failed: {e}")
                    m = {}
                views = m.get("views") or m.get("video_views") or m.get("plays") or m.get("reach") or 0
                likes = m.get("likes") or m.get("like_count") or 0
                comments = m.get("comments") or m.get("comments_count") or 0
                totals[profile] += int(views or 0)
                rows.append([today, profile, platform, p["id"], p.get("permalink") or "",
                             views, likes, comments])
    # VK: только НАШИ залитые видео (state.vk_videos) — креаторские клипы в этих же
    # сообществах уже считает бот, иначе задвоим охват
    if VK_TOKEN:
        for profile in (A, B):
            g = VK_GROUPS[profile]
            ids = state.get("vk_videos", {}).get(str(g), [])
            if not ids:
                continue
            try:
                resp = _vk("video.get", videos=",".join(f"-{g}_{v}" for v in ids))
                for it in resp.get("items", []):
                    views = it.get("views", 0) or 0
                    likes = (it.get("likes") or {}).get("count", 0)
                    totals[profile] += int(views)
                    rows.append([today, profile, "vk", it["id"],
                                 f"https://vk.com/video-{g}_{it['id']}", views, likes, 0])
            except Exception as e:  # noqa: BLE001
                print(f"vk stats {profile}: {e}")

    # апсерт в ГЛАВНЫЙ лист reach-таблицы: посевные просмотры идут в «Общий охват»
    # (цель кампании 2 млн). Бот эти строки не знает и не парсит — токены целы.
    seed_write([{"date_added": today[:5], "creator": LABEL[r[1]], "platform": r[2],
                 "url": r[4], "views": r[5], "updated": datetime.now().strftime("%d.%m %H:%M")}
                for r in rows if r[4]])
    # пометить как увиденные, чтобы links не перезаписал свежие просмотры нулём
    seen = set(state.setdefault("seen_posts", []))
    seen.update(f"{r[2]}:{r[3]}" for r in rows)
    state["seen_posts"] = sorted(seen)

    if rows:
        header_needed = "_stats" not in state
        if header_needed:
            rows.insert(0, ["Дата сбора", "Профиль", "Площадка", "ID поста", "Ссылка",
                            "Просмотры", "Лайки", "Комменты"])
        r = requests.post(WEBHOOK, json={
            "secret": SECRET, "action": "grid_write", "sheet_id": SHEET_ID,
            "sheet_name": "Посевы (факт)", "rows": rows,
        }, timeout=120)
        print("sheet:", r.status_code, r.text[:120])

    total = sum(totals.values())
    prev = (state.get("_stats") or {}).get("total", 0)
    delta = f" (+{total - prev} за сутки)" if prev else ""
    text = (f"🌱 Посев Сингл — {today}\n"
            f"Скажи песней: {totals[A]} просмотров\n"
            f"Песня в подарок: {totals[B]} просмотров\n"
            f"Итого: {total}{delta}\n"
            f"Постов собрано: {len([r for r in rows if r and r[0] == today])}")
    state["_stats"] = {"date": today, "total": total}
    save_state(state)
    tok, chat = ENV.get("CALLBOT_TOKEN"), ENV.get("CALLBOT_CHAT_ID")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text,
                            "disable_web_page_preview": "true"}, timeout=60)
    print(text)


if __name__ == "__main__":
    {"wave": wave, "plan": lambda: plan_sheet(), "stats": stats, "links": links}[sys.argv[1]]()
