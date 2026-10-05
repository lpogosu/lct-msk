"""Движок и сессии SQLAlchemy 2 (синхронно, psycopg 3).

Движок создаётся лениво при первом обращении — импорт моделей и генерация OpenAPI
не требуют работающей БД. В тестах `get_db` подменяется (см. tests/conftest.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings


def make_engine(url: str, *, echo: bool = False, pool_size: int = 10, max_overflow: int = 10) -> Engine:
    return create_engine(url, echo=echo, pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    s = get_settings()
    return make_engine(s.database_url, echo=s.db_echo, pool_size=s.db_pool_size, max_overflow=s.db_max_overflow)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    # expire_on_commit=False: после commit в роутере объекты ещё сериализуются в ответ.
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос; при исключении — откат."""
    db = get_sessionmaker()()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
