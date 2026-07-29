"""Посев Сингла: уникализированные копии топ-роликов кампании → аккаунты брендов.

Ролики лежат в ~/Desktop/singl_seed/{src,out}. Копии сделаны uniq_bot/uniquify.py
(preset=strong, mirror=False — на роликах текст, hflip палится).

Профили upload-post (по 3 площадки: tiktok+instagram+youtube):
  Single1_skazhi_pesney  — «Скажи песней»   (singl.zvuk / skazhi_pesney / @skazhi.pesney)
  Single_pesnya_vpodarok — «Песня в подарок» (pesnya.vpodarok / pesnya_v_podar_ok / @pesnya.vpodarok)

Команды:
  .venv/bin/python singl_seed.py post   # запостить/зашедулить всё pending из PLAN
  .venv/bin/python singl_seed.py plan   # записать план в лист «План посева» клиентской таблицы
  .venv/bin/python singl_seed.py stats  # собрать просмотры постов → лист «Посевы (факт)» + ТГ-отчёт

Состояние (что уже отправлено + прошлые тоталы) — ~/Desktop/singl_seed/state.json.
stats гоняет cron ежедневно в 20:00 (запись в crontab пользователя).
"""

import json
import sys
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
PLAN_SHEET = "План посева"

SEED_DIR = Path.home() / "Desktop" / "singl_seed"
STATE = SEED_DIR / "state.json"
PLATFORMS = ["tiktok", "instagram", "youtube"]

A = "Single1_skazhi_pesney"
B = "Single_pesnya_vpodarok"

# файл (из out/), профиль, дата+время МСК, подпись; источник — для листа плана
PLAN = [
    ("mazitova_u1.mp4", A, "2026-07-29T12:30:00+03:00",
     "Лучший подарок — тот, что нельзя купить в магазине 🎁 Персональная песня для вашего человека 🎶 #песнявподарок #подарок #сюрприз #идеяподарка",
     "Регина Мазитова · https://youtube.com/shorts/83RACeOTO4U"),
    ("tishchenko_u1.mp4", B, "2026-07-29T18:00:00+03:00",
     "Она услышала песню про себя и расплакалась 🥹 Песня в подарок — эмоция, которую запомнят навсегда 🎶 #песнявподарок #подароксюрприз #эмоции",
     "Татьяна Тищенко · https://vk.ru/clip-186704538_456239176"),
    ("arina1_u1.mp4", A, "2026-07-30T12:30:00+03:00",
     "Что подарить человеку, у которого всё есть? Песню про него 🎤 #песнявподарок #идеяподарка #сюрприз",
     "Арина · https://vt.tiktok.com/ZSXpCncGB/"),
    ("vahrameeva_u1.mp4", B, "2026-07-30T18:00:00+03:00",
     "Подарок за 5 минут, а мурашки на всю жизнь 🎶 Персональная песня на заказ #песнявподарок #подарок #мурашки",
     "Алина Вахрамеева · https://vm.tiktok.com/ZGd9K1F9e/"),
    ("kuptsova_u1.mp4", A, "2026-07-31T12:30:00+03:00",
     "Такой подарок не передарят 😄 Песня, написанная специально про вашего человека 🎁 #песнявподарок #подарокдевушке #подарокмаме",
     "Анастасия Купцова · https://youtu.be/UfHQ_KQJuRw"),
    ("arina2_u1.mp4", B, "2026-07-31T18:00:00+03:00",
     "Реакция на песню про себя — бесценна 🥹🎶 Закажи песню в подарок #песнявподарок #реакция #сюрприз",
     "Арина · https://vt.tiktok.com/ZSXpCQkLp/"),
    ("bauer_u1.mp4", A, "2026-08-01T12:30:00+03:00",
     "Забудь про носки и сертификаты — подари песню 🎸 #песнявподарок #идеяподарка #подарокмужу",
     "Миша Бауэр · https://youtube.com/shorts/iqQ4q72fhHw"),
    ("gulakova_u1.mp4", B, "2026-08-01T18:00:00+03:00",
     "Хочешь довести до слёз счастья? Подари песню про вас двоих 🎶 #песнявподарок #подарок #любовь",
     "Анастасия Гулакова · https://youtube.com/shorts/To2GrbmytGw"),
]


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def post() -> None:
    state = load_state()
    for fname, profile, when, caption, _src in PLAN:
        if fname in state:
            print(f"skip {fname} (уже отправлен)")
            continue
        path = SEED_DIR / "out" / fname
        # YouTube: title ≤100 симв. — берём текст до хештегов, полная подпись в description
        yt_title = caption.split("#")[0].strip()
        if len(yt_title) > 100:
            yt_title = yt_title[:97].rsplit(" ", 1)[0] + "…"
        with path.open("rb") as fh:
            r = requests.post(
                UPLOAD_URL,
                headers={"Authorization": f"Apikey {API_KEY}"},
                data={"user": profile, "platform[]": PLATFORMS,
                      "title": caption, "youtube_title": yt_title,
                      "youtube_description": caption, "scheduled_date": when},
                files={"video": (fname, fh, "video/mp4")},
                timeout=300,
            )
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        print(f"{fname} -> {profile} @{when}: HTTP {r.status_code} {json.dumps(body, ensure_ascii=False)[:300]}")
        if r.status_code == 429:
            print("ЛИМИТ АПЛОАДОВ — стоп, остальное не шлём")
            break
        if r.status_code in (200, 202):
            state[fname] = {"when": when, "profile": profile,
                            "job_id": body.get("job_id") or body.get("request_id"),
                            "usage": body.get("usage")}
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def plan() -> None:
    state = load_state()
    rows = [["Дата (МСК)", "Профиль", "Площадки", "Файл-копия", "Исходник (креатор)",
             "Подпись", "Статус"]]
    for fname, profile, when, caption, src in PLAN:
        status = "⏰ запланирован" if fname in state else "— не отправлен"
        rows.append([when.replace("T", " ")[:16], profile, "TikTok+IG+YouTube",
                     fname, src, caption, status])
    r = requests.post(WEBHOOK, json={
        "secret": SECRET, "action": "grid_write", "sheet_id": SHEET_ID,
        "sheet_name": PLAN_SHEET, "clear": True, "row": 1, "rows": rows,
    }, timeout=120)
    print(r.status_code, r.text[:200])


def stats() -> None:
    """Просмотры всех постов обоих профилей → аппенд в «Посевы (факт)» + ТГ-отчёт."""
    from datetime import datetime

    from papkids_uploadpost import analytics, media

    state = load_state()
    today = datetime.now().strftime("%d.%m.%Y")
    rows, totals = [], {A: 0, B: 0}
    # ponytail: 1 вызов media + 1 analytics на пост за прогон; при >50 постах перейти на кэш id
    for profile in (A, B):
        for platform in PLATFORMS:
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
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tok, chat = ENV.get("CALLBOT_TOKEN"), ENV.get("CALLBOT_CHAT_ID")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      data={"chat_id": chat, "text": text,
                            "disable_web_page_preview": "true"}, timeout=60)
    print(text)


if __name__ == "__main__":
    {"post": post, "plan": plan, "stats": stats}[sys.argv[1]]()
