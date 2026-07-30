from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    telegram_contact = State()
    instagram = State()
    other_socials = State()
    rate = State()
    portfolio = State()
    photo = State()
    age = State()
    city = State()
    phone = State()
    categories = State()
    confirm = State()


class Dedup(StatesGroup):
    """Состояния для сценария подтверждения "это тот же человек?"."""

    waiting_confirmation = State()


class EditField(StatesGroup):
    """Точечная правка одного поля анкеты (меню «Обновить данные»)."""

    waiting_value = State()


class BroadcastFSM(StatesGroup):
    waiting_text = State()
    waiting_confirm = State()


class AnnounceFSM(StatesGroup):
    """Анонс бренда по базе с инлайн-кнопкой «Откликнуться» (callback brand_apply:{id})."""

    waiting_text = State()
    waiting_confirm = State()


class SingleFSM(StatesGroup):
    """Пайплайн проекта «Сингл»: ввод срока ролика, ссылок на посты и реквизитов оплаты."""

    waiting_deadline = State()
    waiting_links = State()
    waiting_payment = State()
    waiting_extra_links = State()  # «добавить ещё ссылку» из «Мои проекты»
    waiting_wave2_date = State()  # 2-я волна: дата публикации новых постов


class AddVideoFSM(StatesGroup):
    """Ручное добавление ролика в трекинг охватов админом: имя → ссылка(и)."""

    waiting_name = State()
    waiting_link = State()


class Works(StatesGroup):
    """Сбор лучших работ креатора (ролики для карточки в базе)."""

    collecting = State()


class PapKids(StatesGroup):
    """Сбор роликов по клиентскому проекту PapKids."""

    collecting = State()
