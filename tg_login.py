"""
Разовый вход в Telegram под ЮЗЕР-аккаунтом (Telethon) — чтобы читать историю группы
креаторов (Bot API это не умеет). Запусти ОДИН раз:

    source .venv/bin/activate && python tg_login.py

Введёшь номер телефона и код из Telegram (если стоит 2FA — ещё пароль облака).
Сохранится сессия data/creators_user.session — дальше всё делает group_sync.py.
Код нигде не хранится, только сама сессия (её не коммить — уже в .gitignore по маске).
"""
import os
from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv("/Users/nastasyapopova/Desktop/ugc_bot_v2/.env")
os.makedirs("data", exist_ok=True)

api_id = int(os.environ["TG_API_ID"])
api_hash = os.environ["TG_API_HASH"]

with TelegramClient("data/creators_user", api_id, api_hash) as client:
    me = client.get_me()
    print(f"✅ Вошли как: {me.first_name} (@{me.username}, id {me.id})")
    print("Сессия сохранена: data/creators_user.session — можно запускать group_sync.py")
