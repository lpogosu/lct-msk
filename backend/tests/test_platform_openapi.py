"""Платформа: OpenAPI генерируется без ошибок и полностью документирован по-русски; документация,
заглушки разделов (501), health, CORS, заголовки безопасности."""

from __future__ import annotations

import json
import re
import warnings

import pytest

from app.main import create_app

CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
STANDARD = {"400", "401", "403", "404", "409", "422", "429"}
STUB_SECTIONS = {
    "/api/survey": "Опрос",
    "/api/tests": "Тестирование",
    "/api/needs": "Потребности и подборка",
    "/api/invitations": "Приглашения",
    "/api/applications": "Отклики",
    "/api/vacancies": "Вакансии",
    "/api/tasks": "Регулярные задачи",
    "/api/fsp": "ФСП ID и достижения",
    "/api/lab": "Лаборатория",
    "/api/admin": "Администрирование",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    from app.core.settings import Settings
    from app.mail.sender import MemoryMailer

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # дубли operationId и т.п. FastAPI сообщает предупреждениями
        app = create_app(Settings(_env_file=None, env="test"), MemoryMailer())
        return app.openapi()


def _operations(spec):
    for path, item in spec["paths"].items():
        for method, op in item.items():
            yield path, method, op


def test_openapi_generates_and_is_json_serializable(spec):
    assert spec["info"]["title"] == "Взаимно API"
    assert CYRILLIC.search(spec["info"]["description"])
    json.dumps(spec, ensure_ascii=False)
    paths = set(spec["paths"])
    assert {
        "/api/health",
        "/api/auth/register",
        "/api/auth/verify-email",
        "/api/auth/resend-code",
        "/api/auth/login",
        "/api/auth/refresh",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/password-reset/request",
        "/api/auth/password-reset/confirm",
        "/api/consents",
        "/api/consents/{consent_id}/revoke",
    } <= paths
    assert all(p.startswith("/api/") for p in paths)


def test_every_operation_documented_in_russian(spec):
    op_ids = []
    for path, method, op in _operations(spec):
        where = f"{method.upper()} {path}"
        assert op.get("summary") and CYRILLIC.search(op["summary"]), where
        assert op.get("description") and CYRILLIC.search(op["description"]), where
        assert op.get("tags") and all(CYRILLIC.search(t) for t in op["tags"]), where
        missing = STANDARD - set(op["responses"])
        assert not missing, f"{where}: не описаны ответы {sorted(missing)}"
        for code in STANDARD:
            resp = op["responses"][code]
            assert CYRILLIC.search(resp["description"]), (where, code)
            assert resp["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/ErrorResponse"}, (where, code)
        ok = [c for c in op["responses"] if c.startswith("2")]
        assert ok or "501" in op["responses"], where
        op_ids.append(op["operationId"])
    assert len(op_ids) == len(set(op_ids))


def test_success_responses_have_models(spec):
    for path, method, op in _operations(spec):
        if path.startswith(tuple(STUB_SECTIONS)) or path == "/api/auth/logout":
            continue
        ok = next(c for c in op["responses"] if c.startswith("2"))
        schema = op["responses"][ok]["content"]["application/json"]["schema"]
        assert "$ref" in schema or schema.get("type") == "array", f"{method} {path}: нет модели ответа"


def test_error_schema_and_no_theta_in_docs(spec):
    err = spec["components"]["schemas"]["ErrorResponse"]
    assert set(err["properties"]) == {"code", "message", "details"}
    assert "HTTPValidationError" not in spec["components"]["schemas"]  # 422 — наш единый формат
    # DECISIONS п. 10: никакого «θ» в текстах для людей
    assert "θ" not in json.dumps(spec, ensure_ascii=False)
    tags = {t["name"]: t["description"] for t in spec["tags"]}
    assert {"Аутентификация", "Согласия", "Служебное", "Опрос"} <= set(tags)
    assert "Bearer" in spec["components"]["securitySchemes"]


def test_docs_pages(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200 and r.json()["info"]["title"] == "Взаимно API"
    redoc = client.get("/api/redoc")
    assert redoc.status_code == 200 and "/api/openapi.json" in redoc.text
    assert "fonts.googleapis.com" not in redoc.text  # Google Fonts отключены
    swagger = client.get("/api/docs")
    assert swagger.status_code == 200 and "swagger-ui" in swagger.text.lower()
    # Стандартные /docs и /redoc FastAPI отключены
    assert client.get("/docs").status_code == 404 and client.get("/redoc").status_code == 404


@pytest.mark.parametrize(("prefix", "section"), sorted(STUB_SECTIONS.items()))
def test_stub_sections_return_501_in_russian(client, prefix, section):
    for method, path in (("get", prefix), ("post", f"{prefix}/x/y"), ("delete", f"{prefix}/123")):
        r = client.request(method.upper(), path)
        assert r.status_code == 501, (method, path)
        assert r.json() == {
            "code": "not_implemented",
            "message": f"Раздел «{section}» ещё в разработке",
            "details": {"section": section},
        }


def test_health(client):
    assert client.get("/api/health/live").json() == {"status": "ok"}
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["db"] == "ok" and body["demo_mode"] is False


def test_unknown_route_and_method_errors_in_uniform_format(client):
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json() == {"code": "not_found", "message": "Ресурс не найден", "details": None}
    r = client.get("/api/auth/login")
    assert r.status_code == 405 and r.json()["code"] == "method_not_allowed"
    r = client.post("/api/auth/login", content=b"{bad json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


def test_unhandled_error_is_500_json_with_cors(app):
    """Необработанное исключение → 500 в едином формате, и браузер может его прочитать (CORS-заголовки)."""
    from fastapi.testclient import TestClient

    @app.get("/api/_test/boom")
    def _boom():
        raise RuntimeError("секретная деталь стека")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/_test/boom", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 500
    assert r.json() == {"code": "internal_error", "message": "Внутренняя ошибка сервера. Мы уже разбираемся", "details": None}
    assert "секретная" not in r.text
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_cors_and_security_headers(client):
    r = client.options(
        "/api/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert r.headers["access-control-allow-credentials"] == "true"
    r = client.options(
        "/api/auth/login", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in r.headers
    r = client.get("/api/health/live")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
