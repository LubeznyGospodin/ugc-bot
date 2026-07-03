from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    telegram_contact = State()
    instagram = State()
    other_socials = State()
    rate = State()
    portfolio = State()
    age = State()
    city = State()
    phone = State()
    categories = State()
    confirm = State()


class Dedup(StatesGroup):
    """Состояния для сценария подтверждения "это тот же человек?"."""

    waiting_confirmation = State()


class BroadcastFSM(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()
