"""Общие фикстуры тестов.

PostgreSQL: `docker compose up -d db`. Для каждого запуска pytest создаётся отдельная база
`<имя из VZ_TEST_DATABASE_URL>_<pid>_<rand>` (параллельные запуски не мешают друг другу),
схема — Base.metadata.create_all, после запуска база удаляется. Если PostgreSQL недоступен,
тесты, которым нужна БД, пропускаются с понятной причиной; остальные работают.

Каждый тест с БД живёт в транзакции, которая откатывается в конце: commit() внутри приложения
закрывает только SAVEPOINT (join_transaction_mode="create_savepoint").

Фикстуры для других модулей: db_session, client (TestClient), mailer (MemoryMailer),
test_settings, make_account (регистрация + подтверждение через API → токен), read_code, frozen_clock.
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.clock import Clock, clock

TEST_DATABASE_URL = os.environ.get(
    "VZ_TEST_DATABASE_URL", "postgresql+psycopg://vzaimno:vzaimno@localhost:5432/vzaimno_test"
)
FROZEN_AT = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
CODE_RE = re.compile(r"\b(\d{6})\b")


def url_str(url: URL) -> str:
    return url.render_as_string(hide_password=False)


@dataclass
class PgServer:
    """Доступ к серверу PostgreSQL для создания служебных баз (миграции и т.п.)."""

    admin: Engine
    base_url: URL

    def create_database(self, suffix: str) -> URL:
        name = f"{self.base_url.database}_{os.getpid()}_{suffix}_{uuid.uuid4().hex[:6]}"
        with self.admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
        return self.base_url.set(database=name)

    def drop_database(self, url: URL) -> None:
        with self.admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))


@pytest.fixture(scope="session")
def pg_server() -> Iterator[PgServer]:
    base = make_url(TEST_DATABASE_URL)
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with admin.connect() as c:
            c.execute(text("SELECT 1"))
    except OperationalError as e:
        admin.dispose()
        pytest.skip(f"PostgreSQL недоступен ({TEST_DATABASE_URL}): запустите `docker compose up -d db`. {e.orig}")
    yield PgServer(admin, base)
    admin.dispose()


@pytest.fixture(scope="session")
def db_engine(pg_server: PgServer) -> Iterator[Engine]:
    import app.models  # noqa: F401 — регистрирует таблицы
    from app.db.base import Base

    url = pg_server.create_database("run")
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.begin() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(url)


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    conn = db_engine.connect()
    outer = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        conn.close()


@pytest.fixture(autouse=True)
def _reset_clock() -> Iterator[None]:
    """Часы приложения не должны «протекать» между тестами."""
    yield
    clock.reset()


@pytest.fixture
def frozen_clock() -> Iterator[Clock]:
    clock.freeze(FROZEN_AT)
    yield clock
    clock.reset()


@pytest.fixture
def test_settings():
    from app.core.settings import Settings

    # Дешёвый argon2 (параметры всё равно argon2id); почта — только в памяти
    return Settings(
        _env_file=None,
        env="test",
        argon2_time_cost=1,
        argon2_memory_kib=8192,
        argon2_parallelism=1,
        mail_backend="console",
    )


@pytest.fixture
def mailer():
    from app.mail.sender import MemoryMailer

    return MemoryMailer()


@pytest.fixture
def app(test_settings, mailer, db_session: Session):
    from app.db.session import get_db
    from app.main import create_app

    application = create_app(test_settings, mailer)

    def _override_db() -> Iterator[Session]:
        yield db_session

    application.dependency_overrides[get_db] = _override_db
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def code_from_mail(mailer, email: str) -> str:
    mail = mailer.last_to(email.strip().lower())  # письма уходят на нормализованный адрес
    assert mail is not None, f"письмо для {email} не отправлено"
    m = CODE_RE.search(mail.subject)
    assert m, mail.subject
    return m.group(1)


@pytest.fixture
def read_code(mailer) -> Callable[[str], str]:
    """read_code(email) → 6-значный код из последнего письма на этот адрес."""
    return lambda email: code_from_mail(mailer, email)


@dataclass
class Account:
    email: str
    password: str
    role: str
    user_id: uuid.UUID
    access_token: str
    refresh_cookie: str | None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@pytest.fixture
def make_account(client, mailer, test_settings) -> Callable[..., Account]:
    """Зарегистрировать и подтвердить аккаунт через API. make_account("a@example.com", role="employer")."""

    def _make(email: str | None = None, role: str = "candidate", password: str = "Parol-2026") -> Account:
        email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
        r = client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "role": role, "consent_personal_data": True},
        )
        assert r.status_code == 201, r.text
        r = client.post(
            "/api/auth/verify-email",
            json={"email": email, "code": code_from_mail(mailer, email), "password": password},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        cookie = r.cookies.get(test_settings.refresh_cookie_name)
        client.cookies.clear()  # аккаунты в одном тесте не должны делить refresh-cookie
        return Account(email, password, role, uuid.UUID(body["user"]["id"]), body["access_token"], cookie)

    return _make
