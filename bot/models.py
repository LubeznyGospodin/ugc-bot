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
    # Метка источника из deep-link (?start=МЕТКА). First-touch: пишем при ПЕРВОМ заходе
    # и НЕ перезатираем (иначе последний переход украл бы атрибуцию у первого). NULL —
    # зашёл без метки. Позволяет считать переходы по каждой ссылке ?start=ari и т.п.
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
    first_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # для заморозки
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)   # последнее успешное
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # когда успешно
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_try_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # ссылка ещё есть в «Отклики»
    # Охват вписан руками в таблице → бот больше НЕ парсит и НЕ перезаписывает эту строку
    # (и не тратит на неё юниты API). Ставится по ответу doReachWrite_.
    manual: Mapped[bool] = mapped_column(Boolean, default=False)
    # Ссылка добавлена ЧЕРЕЗ БОТА («➕ Добавить ещё ссылку» / админ), а не найдена в листе
    # «Отклики». Такие строки reach_run НЕ гасит — иначе они выпадали из клиентской таблицы.
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)


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


class FSMRecord(Base):
    """Состояние диалога (FSM) — в БД, а не в памяти процесса.

    Зачем: MemoryStorage терял всё при каждом рестарте/деплое, из-за чего у человека
    посреди анкеты или догрузки ссылки «пропадал» шаг и сообщение улетало в никуда.
    Ключ собирается из StorageKey (бот+чат+юзер+тема+destiny)."""

    __tablename__ = "fsm_records"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    state: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CreatorWork(Base):
    """Лучшие работы креатора — для карточки в канале @ugc_creatory и подборок.

    Источник (source):
      self  — креатор прислал сам в бота (файл уже в Telegram, готов к репосту)
      admin — добавил админ
      ig    — собрано ночным браузерным обходом Instagram (файл скачан отдельно)
    Храним file_id (если файл в Telegram) и/или ссылку на оригинал."""

    __tablename__ = "creator_works"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # Проект, к которому относится ролик: "base" — база @ugc_creatory, "papkids" — клиент
    # PapKids и т.д. Разделяет работы, чтобы у одного креатора проекты не смешивались.
    project: Mapped[str] = mapped_column(String(32), default="base", index=True)
    file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="self")
    position: Mapped[int] = mapped_column(Integer, default=0)  # порядок в карточке
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ProjectMember(Base):
    """Кто участвует в клиентском проекте (PapKids и др.). Проект появляется в «Мои
    проекты» ТОЛЬКО у участников — их вшивает админ по утверждённому списку."""

    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project: Mapped[str] = mapped_column(String(32), index=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
