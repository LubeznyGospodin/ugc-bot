"""
Локальная SQLite — используется как быстрый кэш (чтобы не дёргать Google
Sheets на каждое нажатие кнопки) и хранилище номера строки в таблице
(sheet_row), чтобы doUpdateRow_ знал, какую строку править.

ВАЖНО: это НЕ источник правды для дедупа. Источник правды — Google Sheet
(см. bot/sheets.py, doLookup_). Локальная БД на Railway эфемерна и
обнуляется при каждом передеплое — если её нет, бот просто на следующий
/start заново спросит lookup у таблицы и восстановит sheet_row.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Creator(Base):
    __tablename__ = "creators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    instagram: Mapped[str | None] = mapped_column(Text, nullable=True)
    other_socials: Mapped[str | None] = mapped_column(Text, nullable=True)
    rate: Mapped[str | None] = mapped_column(String(255), nullable=True)
    portfolio: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Text, а не String(16): креаторы вводят «Возраст» свободно (иногда длинный текст) —
    # раньше это роняло sync_creators с value too long for character varying(16).
    age: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    categories: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheet_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CachedBrand(Base):
    """Зеркало вкладки «Бренды» — чтобы список/карточки брендов читались из БД
    мгновенно, а не через webhook на каждый тап. Обновляется фоновым синком."""

    __tablename__ = "cached_brands"

    brand_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(255), default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BotVisit(Base):
    """Каждый уникальный пользователь, нажавший /start (даже если не зарегистрировался).
    Нужно для воронки CJM: заходы → регистрации → уникальные отклики."""

    __tablename__ = "bot_visits"

    tg_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Когда отправили пуш-напоминание «доделай анкету». NULL — ещё не слали.
    # Значение == NUDGE_EPOCH — «зачищено» на старте фичи (бэклог, авто-луп не трогает).
    nudged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CreatorPhoto(Base):
    """file_id фото/файлов, присланных креатором в бота на шаге «фото».

    Храним, чтобы их можно было отправить куда угодно в любой момент (в группу, повторно
    и т.д.). Раньше file_id жили только в памяти на время анкеты и выбрасывались после
    отправки админам — из-за этого фото 28 креаторов невозможно переслать задним числом."""

    __tablename__ = "creator_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="photo")  # photo | doc
    file_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Куда уже отправляли (чтобы не слать в группу дважды).
    sent_to_chat: Mapped[str | None] = mapped_column(Text, nullable=True)


class SinglePipeline(Base):
    """Пайплайн дожима до ролика — ТОЛЬКО проект «Сингл» (br1).

    Этапы (stage):
      offered   — админ поставил «оффер» в таблице, бот отправил условия + «Участвую»
      accepted  — креатор нажал «Участвую», бот ждёт срок
      producing — креатор задал срок, делает ролик (дедлайн активен)
      submitted — креатор прислал ссылки на посты, цикл завершён
    """

    __tablename__ = "single_pipeline"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stage: Mapped[str] = mapped_column(String(16), default="offered", index=True)
    offered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    deadline_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Флаги, чтобы не спамить напоминаниями (за день до / в день дедлайна).
    reminded_before: Mapped[bool] = mapped_column(Boolean, default=False)
    reminded_due: Mapped[bool] = mapped_column(Boolean, default=False)
    links: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Реквизиты для оплаты по СБП (собираем после сдачи ссылок).
    payment_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_bank: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReachRow(Base):
    """Охват по одному ролику (проект «Сингл»). Храним последнее УСПЕШНОЕ значение,
    чтобы при разовом сбое парсинга не терять цифру — показываем прошлую + пометку ⚠️."""

    __tablename__ = "reach_rows"

    url: Mapped[str] = mapped_column(String(512), primary_key=True)
    creator: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str] = mapped_column(String(16), default="")
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)   # последнее успешное
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # когда успешно
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_try_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # ссылка ещё есть в «Отклики»


class AppState(Base):
    """Мелкое key→value хранилище для служебных отметок (напр. дата последнего
    утреннего отчёта — чтобы не слать дважды после рестарта)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class CachedApplication(Base):
    """Зеркало вкладки «Отклики» — «Мои отклики» читаются из БД мгновенно."""

    __tablename__ = "cached_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    brand_title: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(64), default="на рассмотрении")
    reason: Mapped[str] = mapped_column(Text, default="")
    date: Mapped[str] = mapped_column(String(32), default="")
    # Пайплайн «Сингл» (зеркало новых колонок листа) — для воронки/аналитики.
    confirmed: Mapped[str | None] = mapped_column(String(16), nullable=True)  # «Подтвердил участие»
    video: Mapped[str | None] = mapped_column(Text, nullable=True)  # «Ссылка на ролик»
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
