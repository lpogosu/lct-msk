"""Внутренний HTTP-клиент сборки мира: настоящее приложение FastAPI в процессе (TestClient), без сети.

Сценарий демо (брифы, подборки, приглашения, вакансии, отклики, задачи) строится теми же роутами, что вызывает
фронтенд, — так в демо-мире нет данных, которых нельзя получить через продукт. Токены доступа выпускаются напрямую
(у синтетических кандидатов нет пароля), лимиты запросов отключены (сборка мира — не пользователь), почта не шлётся.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.rate_limit import RateDecision, RateLimiter
from app.core.settings import RateRule, Settings
from app.db.session import get_db
from app.models.users import User
from app.services.demo.accounts import token_for


class NoLimiter(RateLimiter):
    """Токен-корзина, которая всегда разрешает: внутренние вызовы сборки мира не ограничиваются."""

    def hit(self, scope: str, key: str, rule: RateRule, cost: float = 1.0) -> RateDecision:  # noqa: ARG002
        return RateDecision(True, 0.0, rule.capacity)


class ApiError(RuntimeError):
    def __init__(self, method: str, url: str, status: int, body: Any) -> None:
        super().__init__(f"{method} {url}: {status} {body}")
        self.status, self.body = status, body

    @property
    def code(self) -> str | None:
        return self.body.get("code") if isinstance(self.body, dict) else None


class Api:
    def __init__(self, db: Session, settings: Settings) -> None:
        from app.mail.sender import MemoryMailer

        # импорт здесь: app.main подключает роутеры демо, а они импортируют этот пакет
        from app.main import create_app

        self.db = db
        self.settings = settings
        app = create_app(settings, MemoryMailer())
        app.state.rate_limiter = NoLimiter()

        def _db() -> Iterator[Session]:
            yield db

        app.dependency_overrides[get_db] = _db
        self.client = TestClient(app)

    def call(
        self,
        method: str,
        url: str,
        user: User | None = None,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        expect: tuple[int, ...] = (200, 201),
    ) -> Any:
        headers = {"Authorization": f"Bearer {token_for(self.settings, user)}"} if user is not None else {}
        r = self.client.request(method, url, headers=headers, json=json, params=params)
        body = r.json() if r.content and "json" in r.headers.get("content-type", "") else r.content
        if r.status_code not in expect:
            raise ApiError(method, url, r.status_code, body)
        return body

    def get(self, url: str, user: User | None = None, **kw: Any) -> Any:
        return self.call("GET", url, user, **kw)

    def post(self, url: str, user: User | None = None, json: Any = None, **kw: Any) -> Any:
        return self.call("POST", url, user, json=json if json is not None else {}, **kw)

    def put(self, url: str, user: User | None = None, json: Any = None, **kw: Any) -> Any:
        return self.call("PUT", url, user, json=json if json is not None else {}, **kw)

    def close(self) -> None:
        self.client.close()
