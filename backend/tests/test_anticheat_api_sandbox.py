"""Песочница жюри «Попробуй списать»: только в DEMO_MODE (иначе 404), мини-тест из 4 заданий с двойниками уникален для
проверяющего, ключей до ответа нет, диагноз по каждому заданию и акт для пойманного, сессия demo_assisted видна в ленте
сверок лаборатории."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.core.settings import Settings
from app.services.anticheat import models as _models  # noqa: F401 — регистрирует таблицы
from app.services.anticheat import store
from app.services.anticheat.models import AnticheatSandboxRun
from tests import test_api_testing_support as support
from tests.test_api_testing_support import make_role

api = support.api
bank = support.bank

pytestmark = pytest.mark.usefixtures("frozen_clock")

START = "/api/jury/cheat-lab"


@pytest.fixture
def demo_app(mailer, db_session) -> Iterator:
    from app.db.session import get_db
    from app.main import create_app

    settings = Settings(
        _env_file=None, env="test", demo_mode=True, argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1,
        mail_backend="console",
    )
    application = create_app(settings, mailer)
    application.state.demo_loop = False

    def _override() -> Iterator:
        yield db_session

    application.dependency_overrides[get_db] = _override
    yield application


@pytest.fixture
def demo_client(demo_app):
    from fastapi.testclient import TestClient

    with TestClient(demo_app) as c:
        yield c


def _keys(db, test_id: str) -> list[tuple[str, str]]:
    """(ключ экрана, ключ копии) по заданиям мини-теста — только из БД, как их видит сервер."""
    run = db.scalars(select(AnticheatSandboxRun).where(AnticheatSandboxRun.test_id == test_id)).one()
    out = []
    for j in sorted(run.items, key=lambda x: x["no"]):
        from app.services.testing.keystore import decrypt_key

        secret = decrypt_key(j["twin"]["secret_enc"])
        out.append((secret["key"], secret["twin_key"]))
    return out


def test_sandbox_is_404_when_demo_mode_is_off(client, make_account):
    assert client.get("/api/health").json()["demo_mode"] is False
    acc = make_account()
    for method, path, body in (
        ("POST", START, None),
        ("POST", f"{START}/abc/answers", {"answers": {"1": "1"}}),
        ("GET", f"{START}/abc/result", None),
    ):
        for headers in ({}, acc.headers):
            r = client.request(method, path, json=body, headers=headers)
            assert r.status_code == 404, (method, path, r.text)
            assert r.json() == {"code": "not_found", "message": "Ресурс не найден", "details": None}


def test_sandbox_flow_per_item_diagnosis_and_act(demo_client, db_session, make_account):
    r = demo_client.post(START)
    assert r.status_code == 201, r.text
    t = r.json()
    items = t["items"]
    assert len(items) == 4 and t["demo_assisted"] is True and "Списывайте как угодно" in t["banner_ru"]
    assert len({i["variant_code"] for i in items}) == 4
    for it in items:
        assert set(it["clip"]) == {"render_id", "clipboard_text"} and it["clip"]["clipboard_text"]
        assert it["clip"]["render_id"].startswith(t["test_id"])
    keys = _keys(db_session, t["test_id"])
    assert len(keys) == 4 and all(a != b for a, b in keys)
    # До ответов ни ключей экрана, ни ключей копии клиент не получает
    text = json.dumps(t, ensure_ascii=False)
    for _, twin_key in keys:
        assert f'"{twin_key}"' not in text
    for forbidden in ("twin_key", "secret_enc", "changed_literal", "err_trace", "key_hash"):
        assert forbidden not in text

    # 1 — скопировал в чат-бот (ключ копии), 2 — решил по экрану (скриншот), 3 — ошибся, 4 — пропустил
    body = {"answers": {"1": keys[0][1], "2": keys[1][0], "3": "точно-неверно"}, "copied": [1, 3]}
    res = demo_client.post(f"{START}/{t['test_id']}/answers", json=body)
    assert res.status_code == 200, res.text
    out = res.json()
    by_no = {d["no"]: d for d in out["items"]}
    assert out["total"] == 4 and out["caught"] == 1 and out["correct_uncaught"] == 1 and out["demo_assisted"] is True
    assert by_no[1]["caught"] is True and by_no[1]["channel"] == "clipboard_text" and by_no[1]["copied"] is True
    assert "пойман двойником" in by_no[1]["diagnosis_ru"]
    assert by_no[2]["caught"] is False and by_no[2]["kind"] == "correct" and by_no[2]["act"] is None
    assert "скриншот" in by_no[2]["diagnosis_ru"] and "5 минут" in by_no[2]["diagnosis_ru"]
    assert by_no[3]["kind"] in ("other", "misconception") and not by_no[3]["caught"] and by_no[3]["copied"] is True
    assert by_no[4]["kind"] == "blank" and by_no[4]["diagnosis_ru"]
    assert "5 минут" in out["next_step_ru"]
    # Акт сверки для пойманного: оба ключа, подсветка литерала, четыре числа
    act = by_no[1]["act"]
    assert act["keys"] == {"shown": keys[0][0], "clipboard": keys[0][1]}
    assert act["source"] == "sandbox" and act["demo_assisted"] is True
    ln = next(x for x in act["lines"] if x["changed"])
    assert ln["shown"][slice(*ln["shown_span"])] == act["changed_literal"]["from"]
    assert {x["id"] for x in act["stats"]} == {"probability", "phase", "scored", "weight"}
    # Повторная отправка и чужой тест
    assert demo_client.post(f"{START}/{t['test_id']}/answers", json=body).status_code == 409
    assert demo_client.post(f"{START}/{'0' * 12}/answers", json=body).status_code == 404
    again = demo_client.get(f"{START}/{t['test_id']}/result")
    assert again.status_code == 200 and again.json()["caught"] == 1
    # Ответы на неизвестные задания и лишние поля отбрасываются/запрещены
    assert demo_client.post(f"{START}/{t['test_id']}/answers", json={"answers": {}, "evil": 1}).status_code == 422


def test_sandbox_is_unique_per_visitor_and_shows_up_in_lab(demo_client, db_session, make_account):
    a = demo_client.post(START).json()
    b = demo_client.post(START).json()
    assert a["test_id"] != b["test_id"]
    assert {i["variant_code"] for i in a["items"]} != {i["variant_code"] for i in b["items"]}
    keys = _keys(db_session, a["test_id"])
    demo_client.post(f"{START}/{a['test_id']}/answers", json={"answers": {"1": keys[0][1], "2": keys[1][1]}, "copied": [1, 2]})
    # Не отправленный мини-тест в ленте не появляется; отправленный — как сверка песочницы (demo_assisted)
    methodist = make_account()
    make_role(db_session, methodist.user_id, "methodist")
    lst = demo_client.get("/api/lab/reconciliations", headers=methodist.headers).json()
    sandbox_rows = [r for r in lst["items"] if r["source"] == "sandbox"]
    assert len(sandbox_rows) == 2 and all(r["demo_assisted"] and r["session_id"] is None for r in sandbox_rows)
    assert all(r["id"].startswith(f"sb-{a['test_id']}-") for r in sandbox_rows)
    assert not demo_client.get("/api/lab/reconciliations?include_sandbox=false", headers=methodist.headers).json()["items"]
    act = demo_client.get(f"/api/lab/reconciliations/{sandbox_rows[0]['id']}", headers=methodist.headers)
    assert act.status_code == 200 and act.json()["source"] == "sandbox" and act.json()["auto_ban"] is False
    # Решение методиста по песочнице тоже человеческое и обратимое
    r = demo_client.post(
        f"/api/lab/reconciliations/{sandbox_rows[0]['id']}/review", json={"action": "dismiss"}, headers=methodist.headers
    )
    assert r.status_code == 200 and r.json()["review_status"] == "dismissed"
    counts = demo_client.get("/api/lab/reconciliations", headers=methodist.headers).json()["counts"]
    assert counts["dismissed"] >= 1


def test_sandbox_uses_family_twin_first(db_session):
    """Сборка песочницы идёт через dispatch: Family.twin() первым — семейства QA/DevOps со своим двойником в пуле."""
    from app.testing.anticheat.config import load_anticheat_config
    from app.testing.anticheat.dispatch import overrides_twin
    from app.testing.anticheat.sandbox import sandbox_pool
    from app.testing.bank.registry import all_families

    pool = sandbox_pool(all_families(), load_anticheat_config())
    own = [f for fids in pool.values() for f in fids if overrides_twin(all_families()[f])]
    assert own, "семейства со своим Family.twin() должны попадать в пул песочницы"
    assert store  # модуль подключён


def test_sandbox_assembles_from_warm_bank_without_building(demo_client, db_session, monkeypatch):
    """Запас прогретых заданий: следующий мини-тест собирается из него мгновенно (двойники не считаются заново), а наборы
    у проверяющих разные; ключи в ответе клиенту по-прежнему нет."""
    from app.services.anticheat import sandbox

    first = demo_client.post(START).json()  # холодный старт: собирается на месте и пополняет запас
    assert sandbox.bank_size() >= 4

    def boom(*a, **k):
        raise AssertionError("двойник пересчитан, хотя задание есть в запасе")

    monkeypatch.setattr(sandbox, "_try_family", boom)
    seen = {tuple(i["variant_code"] for i in first["items"])}
    for _ in range(3):
        t = demo_client.post(START)
        assert t.status_code == 201, t.text
        body = t.json()
        assert len(body["items"]) == 4 and len({i["variant_code"] for i in body["items"]}) == 4
        text = json.dumps(body, ensure_ascii=False)
        for forbidden in ("twin_key", "secret_enc", "changed_literal"):
            assert forbidden not in text
        seen.add(tuple(i["variant_code"] for i in body["items"]))
    keys = _keys(db_session, body["test_id"])
    assert len(keys) == 4 and all(a != b for a, b in keys)
