#!/usr/bin/env python3
"""Контроль холодных звонков: Битрикс24 (Манго) -> whisper -> claude -> Telegram.

Запускается кроном каждые 15 минут. Берёт новые звонки из voximplant.statistic.get,
звонки >= MIN_DURATION сек с записью транскрибирует и анализирует, короткие копит
в счётчик недозвонов и шлёт сводкой после DIGEST_HOUR.
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
PROMPT_FILE = os.path.join(BASE_DIR, "prompt_analysis.md")
WORK_DIR = os.path.join(BASE_DIR, "records")
LOG_FILE = os.path.join(BASE_DIR, "pipeline.log")

CLAUDE = "/Users/nastasyapopova/.local/bin/claude"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "medium")
MIN_DURATION = 30          # сек: короче — недозвон
MAX_DURATION = 3600
DIGEST_HOUR = 19           # после этого часа первый прогон шлёт сводку недозвонов
LOOKBACK_HOURS = 24        # при первом запуске берём звонки за последние сутки


def load_env():
    env = {}
    with open(os.path.join(os.path.dirname(BASE_DIR), ".env")) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
B24 = ENV["B24_WEBHOOK"].rstrip("/")
TG_TOKEN = ENV["CALLBOT_TOKEN"]
TG_CHAT = ENV.get("CALLBOT_CHAT_ID", "")


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def curl(url, data=None, out_file=None):
    """urllib на этой сети рвётся по SSL (DPI), curl стабилен — ходим через него."""
    cmd = ["/usr/bin/curl", "-sS", "-L", "--retry", "3", "--retry-delay", "2",
           "--max-time", "120", "--retry-all-errors", url]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    if out_file:
        cmd += ["-o", out_file]
    r = subprocess.run(cmd, input=data, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"curl {url.split('?')[0][:60]}: {r.stderr[:200]}")
    return r.stdout


def b24(method, params=None):
    data = urllib.parse.urlencode(params or {}, doseq=True)
    resp = json.loads(curl(f"{B24}/{method}.json", data=data))
    if "error" in resp:
        raise RuntimeError(f"{method}: {resp}")
    return resp


def tg_send(text, chat_id=None):
    chat_id = chat_id or TG_CHAT
    if not chat_id:
        log("CALLBOT_CHAT_ID не задан — сообщение не отправлено:\n" + text)
        return
    payload = {
        "chat_id": chat_id,
        "text": text[:4000],
        "disable_web_page_preview": "true",
    }
    if ENV.get("CALLBOT_THREAD_ID"):
        payload["message_thread_id"] = ENV["CALLBOT_THREAD_ID"]
    data = urllib.parse.urlencode(payload)
    resp = json.loads(curl(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data))
    if not resp.get("ok"):
        log(f"telegram error: {resp}")


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"processed": {}, "missed": {}, "digest_sent": ""}


def save_state(state):
    # чистим processed старше 7 дней, чтобы файл не рос бесконечно
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    state["processed"] = {k: v for k, v in state["processed"].items() if v >= cutoff}
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE_FILE)


_users = {}

# В Битриксе сотрудник может сидеть под чужой учёткой — здесь реальные имена.
# 10168 «Юлия Гурьева» — на самом деле Динар (мужчина).
NAME_OVERRIDES = {"10168": "Динар"}

def user_name(uid):
    uid = str(uid)
    if uid in NAME_OVERRIDES:
        return NAME_OVERRIDES[uid]
    if uid not in _users:
        try:
            u = b24("user.get", {"ID": uid})["result"][0]
            _users[uid] = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
        except Exception:
            _users[uid] = f"id{uid}"
    return _users[uid]


def deal_info(activity_id):
    """По активности звонка достаём сделку (OWNER_TYPE_ID=2)."""
    try:
        a = b24("crm.activity.get", {"id": activity_id})["result"]
        if a.get("OWNER_TYPE_ID") == "2":
            d = b24("crm.deal.get", {"id": a["OWNER_ID"]})["result"]
            url = f"https://packman-agency.bitrix24.ru/crm/deal/details/{a['OWNER_ID']}/"
            return d.get("TITLE", ""), url
    except Exception as e:
        log(f"deal_info({activity_id}): {e}")
    return "", ""


def fetch_calls(since_iso):
    calls, start = [], 0
    while True:
        resp = b24("voximplant.statistic.get", {
            "FILTER[>CALL_START_DATE]": since_iso,
            "SORT": "CALL_START_DATE", "ORDER": "ASC", "start": start,
        })
        calls += resp["result"]
        if "next" not in resp:
            return calls
        start = resp["next"]


def download_record(file_id, call_id):
    info = b24("disk.file.get", {"id": file_id})["result"]
    path = os.path.join(WORK_DIR, f"{call_id.replace('.', '_')}.mp3")
    curl(info["DOWNLOAD_URL"], out_file=path)
    if not os.path.exists(path) or os.path.getsize(path) < 1000:
        raise RuntimeError(f"запись не скачалась: file_id={file_id}")
    return path


_whisper_model = None

def transcribe(mp3_path):
    global _whisper_model
    txt = mp3_path.rsplit(".", 1)[0] + ".txt"
    # если транскрипт уже есть (прошлый прогон упал на анализе) — не гоняем whisper заново
    if os.path.exists(txt) and os.path.getsize(txt) > 0:
        with open(txt) as f:
            return f.read().strip()
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu",
                                      compute_type="int8", cpu_threads=8)
    segs, _info = _whisper_model.transcribe(
        mp3_path, language="ru", vad_filter=True, beam_size=2)
    text = "\n".join(s.text.strip() for s in segs)
    with open(txt, "w") as f:
        f.write(text)
    return text


def analyze(transcript, meta):
    with open(PROMPT_FILE) as f:
        prompt = f.read()
    payload = (
        f"{prompt}\n\n---\n\nМЕТАДАННЫЕ ЗВОНКА:\n"
        f"Менеджер: {meta['manager']}\n"
        f"Клиент/сделка: {meta['deal_title'] or meta['phone']}\n"
        f"Ссылка на сделку: {meta['deal_url'] or 'нет'}\n"
        f"Дата и время: {meta['start']}\n"
        f"Длительность: {meta['duration']} сек\n"
        f"Направление: {'исходящий' if meta['type'] == '1' else 'входящий'}\n\n"
        f"ТРАНСКРИБАЦИЯ:\n{transcript}"
    )
    return run_claude(payload)


def sync_claude_creds():
    """Keychain и ~/.claude/.credentials.json расходятся после ротации токена.
    Берём копию с более поздним expiresAt и выравниваем обе."""
    cred_file = os.path.expanduser("~/.claude/.credentials.json")
    try:
        kc_raw = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=15)
        kc = json.loads(kc_raw.stdout) if kc_raw.returncode == 0 else {}
        fl = json.load(open(cred_file)) if os.path.exists(cred_file) else {}
        kc_exp = (kc.get("claudeAiOauth") or {}).get("expiresAt", 0)
        fl_exp = (fl.get("claudeAiOauth") or {}).get("expiresAt", 0)
        if kc_exp == fl_exp:
            return
        fresh = kc if kc_exp > fl_exp else fl
        merged_kc = dict(kc)
        merged_kc["claudeAiOauth"] = fresh["claudeAiOauth"]
        subprocess.run(
            ["security", "add-generic-password", "-U", "-s", "Claude Code-credentials",
             "-a", "nastasyapopova", "-w", json.dumps(merged_kc)],
            capture_output=True, timeout=15)
        fl["claudeAiOauth"] = fresh["claudeAiOauth"]
        with open(cred_file, "w") as f:
            json.dump(fl, f)
        os.chmod(cred_file, 0o600)
        log(f"креды синхронизированы ({'keychain' if kc_exp > fl_exp else 'файл'} свежее)")
    except Exception as e:
        log(f"sync_claude_creds: {e}")


def run_claude(payload):
    sync_claude_creds()
    # чистое окружение: без переменных вложенной Claude-сессии, с headless-токеном
    env = {
        "HOME": os.path.expanduser("~"),
        "USER": os.environ.get("USER", "nastasyapopova"),
        "PATH": "/Users/nastasyapopova/.local/bin:/usr/bin:/bin:/usr/local/bin",
        "LANG": "ru_RU.UTF-8",
    }
    if ENV.get("CLAUDE_CODE_OAUTH_TOKEN"):
        env["CLAUDE_CODE_OAUTH_TOKEN"] = ENV["CLAUDE_CODE_OAUTH_TOKEN"]
    r = subprocess.run(
        [CLAUDE, "-p", "--model", "sonnet"],
        input=payload, capture_output=True, text=True, timeout=600, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"claude failed: {(r.stderr or r.stdout)[:500]}")
    return r.stdout.strip()


def send_digest_if_due(state):
    """Шлём сводки за все дни, по которым они ещё не уходили.
    За сегодня — только после DIGEST_HOUR; за прошлые дни — при первом же
    запуске (догон после проспанного вечера)."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    done = set(state.get("digest_days", []))
    if state.get("digest_sent"):          # миграция со старого формата
        done.add(state["digest_sent"])
    daily_dir = os.path.join(BASE_DIR, "daily")
    days = set(state["missed"].keys())
    if os.path.isdir(daily_dir):
        days |= {f[:10] for f in os.listdir(daily_dir) if f.endswith(".md")}
    cutoff = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    for day in sorted(days):
        if day in done or day < cutoff or day > today:
            continue
        if day == today and now.hour < DIGEST_HOUR:
            continue
        try:
            send_daily_digest(day, state)
            done.add(day)
        except Exception as e:
            log(f"сводка за {day} не собралась: {e}")
    state["digest_days"] = sorted(done)[-14:]
    state.pop("digest_sent", None)


def send_daily_digest(day, state):
    d = datetime.strptime(day, "%Y-%m-%d")
    missed = state["missed"].get(day, {})
    missed_lines = ""
    if missed:
        lines = [f"📵 Недозвоны (< {MIN_DURATION} сек):"]
        for uid, cnt in sorted(missed.items(), key=lambda x: -x[1]):
            lines.append(f"— {user_name(uid)}: {cnt}")
        missed_lines = "\n".join(lines)
    day_file = os.path.join(BASE_DIR, "daily", day + ".md")
    if os.path.exists(day_file):
        with open(os.path.join(BASE_DIR, "prompt_daily.md")) as f:
            prompt = f.read()
        with open(day_file) as f:
            reports = f.read()
        payload = (f"{prompt}\n\nДата: {d:%d.%m.%Y}\n"
                   f"{missed_lines or 'Недозвонов нет.'}\n\n"
                   f"РАЗБОРЫ ЗВОНКОВ ЗА ДЕНЬ:\n\n{reports}")
        tg_send(run_claude(payload))
        log(f"сводка за {day} отправлена")
    elif missed_lines:
        tg_send(f"📊 ИТОГИ ДНЯ {d:%d.%m} — содержательных звонков не было.\n{missed_lines}")
        log(f"сводка за {day} отправлена (только недозвоны)")


def main():
    # рабочее окно: пн–сб 9:00–21:59; --digest (вечерний агент) окно игнорирует,
    # чтобы отчёт уходил и при позднем пробуждении Мака
    now = datetime.now()
    if "--digest" not in sys.argv and (now.weekday() == 6 or not (9 <= now.hour <= 21)):
        return
    os.makedirs(WORK_DIR, exist_ok=True)
    # лок от наложения прогонов (whisper может работать дольше интервала крона)
    import fcntl
    lock = open(os.path.join(BASE_DIR, ".lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("предыдущий прогон ещё идёт — выходим")
        return
    state = load_state()
    since = (datetime.now() - timedelta(hours=LOOKBACK_HOURS)).strftime("%Y-%m-%dT%H:%M:%S+03:00")
    calls = fetch_calls(since)
    log(f"звонков с {since}: {len(calls)}")

    for c in calls:
        cid = c["CALL_ID"]
        if cid in state["processed"]:
            continue
        dur = int(c["CALL_DURATION"] or 0)
        manager_id = c["PORTAL_USER_ID"]

        if dur < MIN_DURATION:
            day = c["CALL_START_DATE"][:10]
            state["missed"].setdefault(day, {})
            state["missed"][day][manager_id] = state["missed"][day].get(manager_id, 0) + 1
            state["processed"][cid] = datetime.now().isoformat()
            continue

        if not c.get("RECORD_FILE_ID") or dur > MAX_DURATION:
            state["processed"][cid] = datetime.now().isoformat()
            continue

        try:
            log(f"обрабатываю {cid} ({dur} сек)")
            mp3 = download_record(c["RECORD_FILE_ID"], cid)
            transcript = transcribe(mp3)
            # пустой/крошечный транскрипт = гудки, автоответчик, тишина — это недозвон
            if len(transcript.split()) < 12:
                log(f"пустой транскрипт ({len(transcript)} симв.) — считаю недозвоном: {cid}")
                day = c["CALL_START_DATE"][:10]
                state["missed"].setdefault(day, {})
                state["missed"][day][manager_id] = state["missed"][day].get(manager_id, 0) + 1
                state["processed"][cid] = datetime.now().isoformat()
                save_state(state)
                os.remove(mp3)
                continue
            act_id = c.get("CRM_ACTIVITY_ID")
            title, url = deal_info(act_id) if act_id and str(act_id) != "0" else ("", "")
            start_dt = datetime.fromisoformat(c["CALL_START_DATE"])
            meta = {
                "manager": user_name(manager_id),
                "phone": c.get("PHONE_NUMBER", ""),
                "deal_title": title, "deal_url": url,
                "start": f"{start_dt:%d.%m.%Y %H:%M}",
                "duration": dur, "type": c.get("CALL_TYPE", "1"),
            }
            report = analyze(transcript, meta)
            if url:
                report += f"\n\n🔗 {url}"
            tg_send(report)
            # копим разборы дня для вечернего сводного отчёта
            day_file = os.path.join(BASE_DIR, "daily", c["CALL_START_DATE"][:10] + ".md")
            os.makedirs(os.path.dirname(day_file), exist_ok=True)
            with open(day_file, "a") as f:
                f.write(report + "\n\n=====\n\n")
            state["processed"][cid] = datetime.now().isoformat()
            save_state(state)
            os.remove(mp3)
            log(f"готово {cid}")
        except Exception as e:
            log(f"ОШИБКА {cid}: {e}")
            # запись удалена с Диска — ретраить бессмысленно
            if "ERROR_NOT_FOUND" in str(e):
                state["processed"][cid] = datetime.now().isoformat()
                save_state(state)
            # не помечаем processed — попробуем в следующий прогон

    send_digest_if_due(state)
    save_state(state)


if __name__ == "__main__":
    main()
