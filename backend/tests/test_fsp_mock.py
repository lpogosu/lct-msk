"""Мок реестра ФСП (services/fsp-mock): эндпоинты, токен, OpenAPI, генератор данных и синхронность
производных файлов (контракт, снимок для бэкенда, realm Keycloak, таблица демо-пользователей)."""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
MOCK_DIR = ROOT / "services" / "fsp-mock"
BACKEND_CONTRACT = ROOT / "backend" / "app" / "fsp" / "models.py"
SNAPSHOT = ROOT / "backend" / "app" / "fsp" / "data" / "demo_registry.json"
REALM = ROOT / "deploy" / "keycloak" / "realm-fsp.json"
KC_README = ROOT / "deploy" / "keycloak" / "README.md"


def _mock(name: str) -> ModuleType:
    if str(MOCK_DIR) not in sys.path:
        sys.path.insert(0, str(MOCK_DIR))
    return importlib.import_module(f"fsp_mock.{name}")


main = _mock("main")
generator = _mock("generator")
export = _mock("export")
keycloak = _mock("keycloak")
contract = _mock("contract")

client = TestClient(main.app)
AUTH = {"Authorization": f"Bearer {main.DEFAULT_TOKEN}"}
SNAP = main.registry().snapshot
COMPS = {c.ekp: c for c in SNAP.competitions}
SUBS_WITH_RESULTS = {r.athlete_sub for r in SNAP.results}


def _age(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


# ---------------------------------------------------------------- эндпоинты


def test_health_is_public() -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong-token"}, {"Authorization": "Basic abc"}])
def test_token_required(headers: dict[str, str]) -> None:
    sub = SNAP.athletes[0].sub
    r = client.get(f"/v1/athletes/{sub}", headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert "токен" in r.json()["detail"]


def test_tokens_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSP_MOCK_TOKENS", "first, second")
    sub = SNAP.athletes[0].sub
    assert client.get(f"/v1/athletes/{sub}", headers={"Authorization": "Bearer second"}).status_code == 200
    assert client.get(f"/v1/athletes/{sub}", headers=AUTH).status_code == 401


def test_get_athlete() -> None:
    a = SNAP.athletes[0]
    r = client.get(f"/v1/athletes/{a.sub}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == a.sub and body["last_name"] == a.last_name
    assert body["birth_date"] == a.birth_date.isoformat()
    missing = client.get("/v1/athletes/нет-такого", headers=AUTH)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Спортсмен с таким sub не найден в реестре"


def test_get_achievements_shape() -> None:
    for a in SNAP.athletes:
        r = client.get(f"/v1/athletes/{a.sub}/achievements", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["athlete_sub"] == a.sub and body["source"] == "fsp_mock"
        result_ekps = {x["ekp"] for x in body["results"]}
        assert {c["ekp"] for c in body["competitions"]} == result_ekps
        assert all(x["athlete_sub"] == a.sub for x in body["results"] + body["ranks"] + body["roles"])
        end = {c["ekp"]: c["end_date"] for c in body["competitions"]}
        dates = [end[x["ekp"]] for x in body["results"]]
        assert dates == sorted(dates, reverse=True)
    assert client.get("/v1/athletes/нет-такого/achievements", headers=AUTH).status_code == 404


def test_achievements_as_of_can_be_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FSP_MOCK_AS_OF", "2026-10-05T09:00:00+00:00")
    r = client.get(f"/v1/athletes/{SNAP.athletes[0].sub}/achievements", headers=AUTH)
    assert r.json()["as_of"].startswith("2026-10-05T09:00:00")


def test_athlete_without_results_returns_empty_lists() -> None:
    sub = next(a.sub for a in SNAP.athletes if a.sub not in SUBS_WITH_RESULTS)
    body = client.get(f"/v1/athletes/{sub}/achievements", headers=AUTH).json()
    assert body["results"] == [] and body["competitions"] == []


def test_get_competition() -> None:
    comp = max(SNAP.competitions, key=lambda c: sum(1 for r in SNAP.results if r.ekp == c.ekp))
    r = client.get(f"/v1/competitions/{comp.ekp}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["competition"]["ekp"] == comp.ekp
    places = [x["place"] for x in body["results"]]
    assert places == sorted(places) and len(places) > 3
    assert client.get("/v1/competitions/2026990000000000", headers=AUTH).status_code == 404
    bad = client.get("/v1/competitions/12345", headers=AUTH)
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("Некорректный запрос") and "16 цифр" in bad.json()["detail"]


def test_protocol_page() -> None:
    published = next(c for c in SNAP.competitions if c.protocol_url)
    r = client.get(f"/protocols/{published.ekp}")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert published.ekp in r.text and "Демо-реестр ФСП" in r.text
    assert published.protocol_url.endswith(f"/protocols/{published.ekp}")
    pending = next(c for c in SNAP.competitions if c.protocol_url is None)
    assert client.get(f"/protocols/{pending.ekp}").status_code == 404


def test_openapi_in_russian_with_bearer() -> None:
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "Реестр ФСП — демо"
    assert "Федерации спортивного программирования" in spec["info"]["description"]
    for path in ("/v1/athletes/{sub}", "/v1/athletes/{sub}/achievements", "/v1/competitions/{ekp}"):
        op = spec["paths"][path]["get"]
        assert re.search("[а-яё]", op["summary"], re.I)
        assert op["security"]
    schemes = spec["components"]["securitySchemes"]
    assert any(s.get("scheme") == "bearer" for s in schemes.values())
    assert "Номер соревнования в Едином календарном плане" in json.dumps(spec, ensure_ascii=False)


# ---------------------------------------------------------------- данные генератора


def test_generator_is_deterministic() -> None:
    a = generator.build_dataset().model_dump_json()
    assert a == generator.build_dataset().model_dump_json()
    assert a != generator.build_dataset(seed=generator.SEED + 1).model_dump_json()


def test_dataset_size_and_coverage() -> None:
    assert len(SNAP.athletes) == 60
    assert len(SNAP.competitions) == 25
    per_disc = Counter(c.discipline_code for c in SNAP.competitions)
    assert per_disc == {code: 5 for code in generator.DISCIPLINES.values()}
    assert {c.level for c in SNAP.competitions} == set(contract.Level)
    assert {c.status_type for c in SNAP.competitions} == set(contract.StatusType)
    assert {c.stage for c in SNAP.competitions} == set(contract.Stage)
    assert {c.format for c in SNAP.competitions} == set(contract.CompetitionFormat)
    assert all(any(r.ekp == c.ekp for r in SNAP.results) for c in SNAP.competitions)


def test_ekp_ids_are_synthetic_16_digits() -> None:
    ekps = [c.ekp for c in SNAP.competitions]
    assert len(set(ekps)) == len(ekps)
    for c in SNAP.competitions:
        assert re.fullmatch(r"\d{16}", c.ekp)
        assert c.ekp[:4] == str(c.start_date.year) and c.ekp[4:6] == "99"


def test_qualifiers_point_to_finals() -> None:
    quals = [c for c in SNAP.competitions if c.parent_ekp]
    assert len(quals) >= 2
    for q in quals:
        final = COMPS[q.parent_ekp]
        assert q.stage == contract.Stage.qualifier and final.stage == contract.Stage.final
        assert final.discipline_code == q.discipline_code and q.end_date <= final.start_date
    both = {r.athlete_sub for r in SNAP.results if COMPS[r.ekp].parent_ekp} & {
        r.athlete_sub for r in SNAP.results if r.ekp in {q.parent_ekp for q in quals}
    }
    assert both, "кто-то должен пройти и отбор, и финал"


def test_special_states_present() -> None:
    confirmed = {r.athlete_sub for r in SNAP.results if r.status == contract.ResultStatus.confirmed}
    pending = {r.athlete_sub for r in SNAP.results if r.status == contract.ResultStatus.awaiting_protocol}
    ranked = {r.athlete_sub for r in SNAP.ranks}
    subs = {a.sub for a in SNAP.athletes}
    assert len(subs - SUBS_WITH_RESULTS - ranked) >= 5  # ничего нет
    assert len(pending - confirmed - ranked) >= 2  # только ждут протокола
    assert len(ranked - SUBS_WITH_RESULTS) >= 2  # только разряд
    assert any(r.status == contract.RankStatus.expired for r in SNAP.ranks)
    assert any(r.code == contract.RankCode.MS for r in SNAP.ranks)
    assert {r.kind for r in SNAP.roles} == set(contract.RoleKind)
    assert any(r.place_to for r in SNAP.results)


def test_pending_competitions_consistent() -> None:
    for c in SNAP.competitions:
        results = [r for r in SNAP.results if r.ekp == c.ekp]
        if c.protocol_status == contract.ProtocolStatus.pending:
            assert c.protocol_url is None
            assert all(r.status == contract.ResultStatus.awaiting_protocol for r in results)
        else:
            assert c.protocol_url
            assert all(r.status == contract.ResultStatus.confirmed for r in results)


def test_team_compositions_consistent() -> None:
    teams: dict[str, list] = defaultdict(list)
    for r in SNAP.results:
        c = COMPS[r.ekp]
        assert 1 <= (r.place_to or r.place) <= r.field_size == c.field_size
        if c.format == contract.CompetitionFormat.team:
            assert r.team is not None and r.role != contract.ResultRole.personal
            teams[r.team.id].append(r)
        else:
            assert r.team is None and r.role == contract.ResultRole.personal
    assert teams
    for rows in teams.values():
        team = rows[0].team
        assert {m.athlete_sub for m in team.members} == {r.athlete_sub for r in rows}
        assert sum(1 for m in team.members if m.role == contract.TeamMemberRole.captain) == 1
        assert len({(r.place, r.place_to) for r in rows}) == 1
        roles = {m.athlete_sub: m.role.value for m in team.members}
        assert all(roles[r.athlete_sub] == r.role.value for r in rows)


def test_rank_validity_table_matches_config() -> None:
    # Положение о ЕВСК, п. 76: КМС — 3 года, I–III и юношеские — 2 года, МС — бессрочно. Мок и config/fsp.yaml
    # (по нему бэкенд досчитывает срок, если реестр его не прислал) не должны расходиться.
    from app.fsp.config import get_fsp_config

    cfg_years = {c.value: y for c, y in get_fsp_config().vss.rank_validity_years.items()}
    mock_years = {c.value: y for c, y in generator.RANK_VALID_YEARS.items()}
    assert mock_years == cfg_years
    assert cfg_years == {"KMS": 3, "I": 2, "II": 2, "III": 2, "I_y": 2, "II_y": 2, "III_y": 2}
    assert "MS" not in cfg_years


def test_generator_never_issues_msmk() -> None:
    # Нормы ЕВСК по спортивному программированию (Приложение № 24 к приказу № 450): МСМК в них нет.
    assert all(r.code != contract.RankCode.MSMK for r in SNAP.ranks)
    assert contract.RankCode.MSMK not in generator._RANK_ORDER
    assert contract.RankCode.MSMK not in generator.RANK_VALID_YEARS


def test_ranks_follow_evsk_validity() -> None:
    athletes = {a.sub: a for a in SNAP.athletes}
    for r in SNAP.ranks:
        years = generator.RANK_VALID_YEARS.get(r.code)
        if years is None:
            assert r.valid_until is None  # МС — бессрочно
        else:
            assert r.valid_until == generator._add_years(r.assigned_at, years)
        active = r.valid_until is None or r.valid_until >= generator.DATA_AS_OF
        assert r.status == (contract.RankStatus.active if active else contract.RankStatus.expired)
        assert r.assigned_at <= generator.DATA_AS_OF
        if r.basis_ekp:
            comp = COMPS[r.basis_ekp]
            min_age = {"MS": 16, "KMS": 15}.get(r.code.value, 14)
            assert _age(athletes[r.athlete_sub].birth_date, comp.end_date) >= min_age
            assert r.assigned_at > comp.end_date


def test_names_are_synthetic() -> None:
    roots = set(generator._SURNAMES)
    for a in SNAP.athletes:
        root = a.last_name[:-1] if a.last_name.endswith("а") else a.last_name
        assert root in roots, a.last_name
    assert len({(a.last_name, a.first_name) for a in SNAP.athletes}) == len(SNAP.athletes)
    assert sum(1 for a in SNAP.athletes if _age(a.birth_date, generator.DATA_AS_OF) < 18) >= 3


# ---------------------------------------------------------------- синхронность производных файлов


def test_contract_copy_in_sync_with_backend() -> None:
    vendored = (MOCK_DIR / "fsp_mock" / "contract.py").read_text(encoding="utf-8")
    assert vendored == BACKEND_CONTRACT.read_text(encoding="utf-8"), "python -m fsp_mock sync-contract"


def test_backend_snapshot_in_sync_with_generator() -> None:
    assert SNAPSHOT.read_text(encoding="utf-8") == export.render_snapshot(), "python -m fsp_mock export"


def test_realm_in_sync_with_generator() -> None:
    assert REALM.read_text(encoding="utf-8") == export.render_realm(), "python -m fsp_mock export"
    readme = KC_README.read_text(encoding="utf-8")
    assert export.render_readme(readme) == readme, "python -m fsp_mock export"


def test_realm_contents() -> None:
    realm = json.loads(REALM.read_text(encoding="utf-8"))
    assert realm["realm"] == "fsp" and realm["defaultLocale"] == "ru"
    (cl,) = realm["clients"]
    assert cl["clientId"] == "vzaimno"  # = VZ_FSP_OIDC_CLIENT_ID по умолчанию (app/core/settings.py)
    assert cl["publicClient"] is False and cl["clientAuthenticatorType"] == "client-secret"
    assert cl["standardFlowEnabled"] and not cl["implicitFlowEnabled"] and not cl["directAccessGrantsEnabled"]
    assert cl["serviceAccountsEnabled"]
    assert cl["attributes"]["pkce.code.challenge.method"] == "S256"
    # Адреса возврата согласованы сквозь всю цепочку: страница фронтенда /candidate/fsp/callback
    # (VZ_FSP_OIDC_REDIRECT_URI по умолчанию, она есть в routes.tsx) и реальный GET /api/fsp/callback бэкенда
    from app.fsp.settings import FSPSettings

    assert FSPSettings().redirect_uri in cl["redirectUris"]
    assert "http://localhost:5173/candidate/fsp/callback" in cl["redirectUris"]
    assert "http://localhost:8000/api/fsp/callback" in cl["redirectUris"]
    assert any(u.startswith("https://") and u.endswith("/candidate/fsp/callback") for u in cl["redirectUris"])
    assert any(u.startswith("https://") and u.endswith("/api/fsp/callback") for u in cl["redirectUris"])
    assert not any("/auth/fsp/callback" in u for u in cl["redirectUris"])  # старых путей (/auth/…, /api/auth/…) нет
    assert not any("*" in u for u in cl["redirectUris"])  # без шаблонов: open redirect не нужен
    users = realm["users"]
    assert len(users) == 30
    athletes = {a.sub: a for a in SNAP.athletes}
    assert len({u["id"] for u in users}) == 30
    for i, u in enumerate(users, start=1):
        a = athletes[u["id"]]  # id пользователя = sub спортсмена в реестре
        assert u["username"] == f"fsp-demo-{i:02d}"
        assert (u["firstName"], u["lastName"]) == (a.first_name, a.last_name)
        assert u["email"].endswith("@demo.example.org") and u["emailVerified"]


def test_mock_port_matches_compose_and_backend_settings() -> None:
    # Контракт docker-compose.yml: fsp-mock слушает 8001 в контейнере, бэкенд ходит на VZ_FSP_REGISTRY_URL.
    from urllib.parse import urlsplit

    import yaml

    from app.fsp.settings import FSPSettings

    dockerfile = (MOCK_DIR / "Dockerfile").read_text(encoding="utf-8")
    cmd_port = int(re.search(r'"--port", "(\d+)"', dockerfile).group(1))
    assert f"EXPOSE {cmd_port}" in dockerfile and f"127.0.0.1:{cmd_port}/health" in dockerfile
    assert urlsplit(generator.DEFAULT_PUBLIC_URL).port == cmd_port
    assert urlsplit(FSPSettings.model_fields["registry_url"].default).port == cmd_port
    compose_path = ROOT / "docker-compose.yml"
    if compose_path.exists():
        services = yaml.safe_load(compose_path.read_text(encoding="utf-8")).get("services", {})
        url = services.get("backend", {}).get("environment", {}).get("VZ_FSP_REGISTRY_URL")
        if "fsp-mock" in services and url:
            assert urlsplit(url).port == cmd_port, "порт fsp-mock в compose и в Dockerfile мока разошлись"


def test_demo_users_cover_scenarios() -> None:
    users = keycloak.demo_users(SNAP)
    subs = {u.sub for u in users}
    ranked = {r.athlete_sub for r in SNAP.ranks}
    pending_only = {r.athlete_sub for r in SNAP.results} - {
        r.athlete_sub for r in SNAP.results if r.status == contract.ResultStatus.confirmed
    }
    assert subs & (ranked - SUBS_WITH_RESULTS), "только разряд"
    assert subs & (pending_only - ranked), "только ждут протокола"
    assert subs - SUBS_WITH_RESULTS - ranked, "без результатов"
    assert subs & {r.athlete_sub for r in SNAP.ranks if r.code == contract.RankCode.MS}
    assert subs & {r.athlete_sub for r in SNAP.roles if r.kind == contract.RoleKind.judge}
    assert all(u.scenario for u in users)
