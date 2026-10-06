"""FSPProvider: нормализация и состояния профиля (концепция §6.4), HTTP-клиент против мока реестра,
client credentials, резервный встроенный провайдер."""

from __future__ import annotations

import importlib
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from app.fsp.models import (
    Achievements,
    AgeGroup,
    Competition,
    CompetitionFormat,
    Level,
    ProtocolStatus,
    Rank,
    RankCode,
    Result,
    ResultRole,
    ResultStatus,
    RoleBadge,
    RoleKind,
    Source,
    Stage,
    StatusType,
)
from app.fsp.provider import (
    ClientCredentialsAuth,
    FallbackFSPProvider,
    FSPAuthError,
    FSPError,
    FSPProvider,
    FSPUnavailable,
    HttpFSPProvider,
    InProcessFSPProvider,
    build_provider,
    load_profile,
    normalize_achievements,
)
from app.fsp.schema import FSPState
from app.fsp.settings import FSPSettings
from app.fsp.vss import employer_line, fsp_fit, vss_by_discipline

ROOT = Path(__file__).resolve().parents[2]
MOCK_DIR = ROOT / "services" / "fsp-mock"
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PRODUCT = "1010021811Я"


def _mock(name: str) -> ModuleType:
    """Мок реестра — отдельный сервис; импортируем его по пути, как требует его автономность."""
    if str(MOCK_DIR) not in sys.path:
        sys.path.insert(0, str(MOCK_DIR))
    return importlib.import_module(f"fsp_mock.{name}")


mock_main = _mock("main")
mock_keycloak = _mock("keycloak")


@pytest.fixture(scope="module")
def inproc() -> InProcessFSPProvider:
    return InProcessFSPProvider.from_file(clock=lambda: NOW)


@pytest.fixture(scope="module")
def http_provider() -> HttpFSPProvider:
    return HttpFSPProvider(client=TestClient(mock_main.app), token=mock_main.DEFAULT_TOKEN)


def _comp(ekp: str, *, pending: bool = False, parent: str | None = None, code: str = PRODUCT) -> Competition:
    return Competition(
        ekp=ekp, title="Тестовое соревнование", discipline_code=code, level=Level.all_russian,
        status_type=StatusType.cup, age_group=AgeGroup.adults,
        stage=Stage.qualifier if parent else Stage.final, parent_ekp=parent, format=CompetitionFormat.personal,
        start_date=date(2026, 5, 1), end_date=date(2026, 5, 2), participants_count=50,
        protocol_status=ProtocolStatus.pending if pending else ProtocolStatus.published,
        protocol_url=None if pending else f"http://registry/protocols/{ekp}",
    )


def _result(rid: str, ekp: str, *, pending: bool = False, place: int = 3) -> Result:
    return Result(
        id=rid, ekp=ekp, athlete_sub="s-1", place=place, field_size=50, role=ResultRole.personal,
        status=ResultStatus.awaiting_protocol if pending else ResultStatus.confirmed,
    )


def _ach(results=(), comps=(), ranks=(), roles=()) -> Achievements:
    return Achievements(
        athlete_sub="s-1", as_of=NOW, source=Source.fsp_mock, results=list(results), competitions=list(comps),
        ranks=list(ranks), roles=list(roles),
    )


# ---------------------------------------------------------------- нормализация и состояния §6.4


def test_state_linked_no_results() -> None:
    assert normalize_achievements(_ach()).state == FSPState.linked_no_results


def test_state_awaiting_protocol_only() -> None:
    e = "2026990000000001"
    p = normalize_achievements(_ach([_result("r1", e, pending=True)], [_comp(e, pending=True)]))
    assert p.state == FSPState.awaiting_protocol
    assert p.pending_count == 1
    assert p.results[0].confirmed is False and p.results[0].protocol_url is None
    assert p.counted_results == []


def test_state_linked_with_hidden_and_pending() -> None:
    e1, e2 = "2026990000000001", "2026990000000002"
    ach = _ach([_result("r1", e1), _result("r2", e2, pending=True)], [_comp(e1), _comp(e2, pending=True)])
    p = normalize_achievements(ach, hidden_result_ids={"r1"})
    assert p.state == FSPState.linked
    assert [r.result_id for r in p.results if r.hidden] == ["r1"]
    assert p.counted_results == []  # r1 скрыт, r2 ждёт протокола


def test_pending_competition_overrides_result_status() -> None:
    e = "2026990000000001"
    p = normalize_achievements(_ach([_result("r1", e)], [_comp(e, pending=True)]))
    assert p.results[0].confirmed is False


def test_rank_only_is_linked_and_roles_only_is_not() -> None:
    rank = Rank(id="k", athlete_sub="s-1", code=RankCode.KMS, discipline_code=PRODUCT,
                assigned_at=date(2025, 1, 1), valid_until=date(2028, 1, 1), order_no="1", assigned_by="x")
    assert normalize_achievements(_ach(ranks=[rank])).state == FSPState.linked
    role = RoleBadge(id="j", athlete_sub="s-1", kind=RoleKind.judge)
    assert normalize_achievements(_ach(roles=[role])).state == FSPState.linked_no_results


def test_group_key_links_qualifier_to_final_and_unknown_discipline_is_skipped() -> None:
    final, qual, odd = "2026990000000001", "2026990000000002", "2026990000000003"
    ach = _ach(
        [_result("r1", final), _result("r2", qual), _result("r3", odd), _result("r4", "2026990000000009")],
        [_comp(final), _comp(qual, parent=final), _comp(odd, code="9999999999Я")],
    )
    p = normalize_achievements(ach)
    by_id = {r.result_id: r for r in p.results}
    assert "r4" not in by_id  # соревнования нет в ответе — результат пропущен
    assert by_id["r1"].group_key == by_id["r2"].group_key == final
    assert by_id["r3"].discipline is None
    assert {r.result_id for r in p.counted_results} == {"r1", "r2"}


def test_load_profile_not_linked_and_unknown_sub(inproc: InProcessFSPProvider) -> None:
    assert load_profile(inproc, None, now=NOW).state == FSPState.not_linked
    p = load_profile(inproc, "нет-такого-sub", now=NOW)
    assert p.state == FSPState.linked_no_results
    assert p.athlete is None
    assert vss_by_discipline(p, NOW) == {}


# ---------------------------------------------------------------- встроенный снимок и мок совпадают


def test_providers_satisfy_protocol(inproc: InProcessFSPProvider, http_provider: HttpFSPProvider) -> None:
    assert isinstance(inproc, FSPProvider)
    assert isinstance(http_provider, FSPProvider)


def test_http_provider_against_mock_matches_inprocess(
    inproc: InProcessFSPProvider, http_provider: HttpFSPProvider
) -> None:
    for athlete in inproc.snapshot.athletes:
        assert http_provider.get_athlete(athlete.sub) == athlete
        a_http = http_provider.get_achievements(athlete.sub)
        a_in = inproc.get_achievements(athlete.sub)
        assert a_http.model_dump(exclude={"as_of"}) == a_in.model_dump(exclude={"as_of"})
    for comp in inproc.snapshot.competitions:
        assert http_provider.get_competition(comp.ekp) == inproc.get_competition(comp.ekp)
    assert http_provider.get_athlete("нет-такого-sub") is None
    assert http_provider.get_competition("2026990000000000") is None


def test_demo_registry_covers_all_states(inproc: InProcessFSPProvider) -> None:
    states = [load_profile(inproc, a.sub, now=NOW).state for a in inproc.snapshot.athletes]
    assert states.count(FSPState.linked) >= 40
    assert states.count(FSPState.awaiting_protocol) >= 2
    assert states.count(FSPState.linked_no_results) >= 5


def test_demo_champion_story(inproc: InProcessFSPProvider) -> None:
    """Демо-пользователь из realm fsp → его достижения → строка в карточке и F."""
    users = mock_keycloak.demo_users(inproc.snapshot)
    champions = []
    for u in users:
        p = load_profile(inproc, u.sub, now=NOW)
        won = any(
            r.place == 1 and r.discipline == "product" and r.status_type == StatusType.championship
            and r.level == Level.all_russian
            for r in p.counted_results
        )
        if won:
            champions.append((employer_line(p, NOW, spec="fullstack"), vss_by_discipline(p, NOW)))
    assert champions, "в демо-пользователях должен быть чемпион России по продуктовому"
    line, vss = champions[0]
    # До раскрытия — «Призёр … топ-3», точного места нет (концепция §4.5).
    assert line.startswith("Призёр всероссийских · продуктовое · 2025 · топ-3")
    assert vss["product"] > 80
    assert fsp_fit(vss, "fullstack").value > 0.7


# ---------------------------------------------------------------- ошибки HTTP и резервный провайдер


def _client(handler) -> httpx.Client:
    return httpx.Client(base_url="http://registry", transport=httpx.MockTransport(handler))


def _boom(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("нет сети", request=request)


@pytest.mark.parametrize(
    ("handler", "exc"),
    [
        (lambda r: httpx.Response(503), FSPUnavailable),
        (_boom, FSPUnavailable),
        (lambda r: httpx.Response(401), FSPAuthError),
        (lambda r: httpx.Response(403), FSPAuthError),
        (lambda r: httpx.Response(418), FSPError),
        (lambda r: httpx.Response(200, json={"не": "контракт"}), FSPError),
        (lambda r: httpx.Response(200, text="<html>прокси</html>"), FSPError),  # не JSON
        (lambda r: httpx.Response(302, headers={"Location": "http://sso/login"}), FSPError),
    ],
)
def test_http_errors_are_mapped(handler, exc) -> None:
    with pytest.raises(exc):
        HttpFSPProvider(client=_client(handler)).get_achievements("s-1")


def test_http_404_is_none() -> None:
    # 404 по контракту реестра (ErrorResponse: {"detail": ...}) — спортсмена в реестре нет
    reply = httpx.Response(404, json={"detail": "Спортсмен с таким sub не найден в реестре"})
    assert HttpFSPProvider(client=_client(lambda r: reply)).get_athlete("s-1") is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(404),
        httpx.Response(404, text="<html>Not Found</html>"),
        # дев-бэкенд «Взаимно» на порту реестра: его 404 — {code, message, details}, не контракт реестра
        httpx.Response(404, json={"code": "not_found", "message": "Ресурс не найден", "details": None}),
    ],
)
def test_http_404_from_foreign_service_is_unavailable_not_none(response: httpx.Response) -> None:
    # Раньше такой ответ превращался в None → «результатов нет» (linked_no_results) без единого признака сбоя
    provider = HttpFSPProvider(client=_client(lambda r: response))
    with pytest.raises(FSPUnavailable, match="не распознан"):
        provider.get_athlete("s-1")
    with pytest.raises(FSPUnavailable):
        provider.get_achievements("s-1")


def test_fallback_uses_snapshot_when_registry_down(inproc: InProcessFSPProvider) -> None:
    sub = inproc.snapshot.athletes[0].sub
    fb = FallbackFSPProvider(HttpFSPProvider(client=_client(_boom)), inproc)
    assert fb.get_achievements(sub) == inproc.get_achievements(sub)
    assert fb.last_used == "fallback"
    denied = FallbackFSPProvider(HttpFSPProvider(client=_client(lambda r: httpx.Response(401))), inproc)
    with pytest.raises(FSPAuthError):  # ошибку доступа резервом не маскируем
        denied.get_achievements(sub)


def test_fallback_outage_is_not_reported_as_no_results(inproc: InProcessFSPProvider) -> None:
    # Настоящий реестр лежит, а в демо-снимке такого sub нет: это «недоступно», а не «результатов нет» —
    # иначе синхронизация во время сбоя обнулила бы F у реального кандидата.
    fb = FallbackFSPProvider(HttpFSPProvider(client=_client(_boom)), inproc)
    with pytest.raises(FSPUnavailable):
        fb.get_achievements("настоящий-sub-не-из-демо")
    with pytest.raises(FSPUnavailable):
        load_profile(fb, "настоящий-sub-не-из-демо", now=NOW)
    assert fb.last_used is None


def test_foreign_records_are_never_attached() -> None:
    e = "2026990000000001"
    mine, theirs = _result("r1", e), _result("r2", e).model_copy(update={"athlete_sub": "s-2"})
    rank = Rank(id="k", athlete_sub="s-2", code=RankCode.MS, discipline_code=PRODUCT,
                assigned_at=date(2025, 1, 1), order_no="1", assigned_by="x")
    p = normalize_achievements(_ach([mine, theirs], [_comp(e)], ranks=[rank]))
    assert [r.result_id for r in p.results] == ["r1"] and p.ranks == []

    class WrongSub:
        def get_achievements(self, sub: str) -> Achievements:
            return _ach([mine], [_comp(e)])  # athlete_sub = s-1

        def get_athlete(self, sub: str) -> None:
            return None

        def get_competition(self, ekp: str) -> None:
            return None

    with pytest.raises(FSPError):
        load_profile(WrongSub(), "s-9", now=NOW)


def test_client_credentials_token_cached_and_refreshed_on_401() -> None:
    state = {"issued": 0, "valid": "t1", "now": 0.0}
    athlete = {"sub": "s-1", "last_name": "Байтов", "first_name": "Иван", "birth_date": "2000-01-01",
               "region_code": "77", "region_name": "г. Москва"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["client_credentials"]
            assert form["client_id"] == ["vzaimno"]
            state["issued"] += 1
            return httpx.Response(200, json={"access_token": f"t{state['issued']}", "expires_in": 300})
        if request.headers.get("Authorization") != f"Bearer {state['valid']}":
            return httpx.Response(401)
        return httpx.Response(200, json=athlete)

    auth = ClientCredentialsAuth("http://kc/token", "vzaimno", "secret", monotonic=lambda: state["now"])
    provider = HttpFSPProvider(client=_client(handler), auth=auth)
    assert provider.get_athlete("s-1").last_name == "Байтов"
    provider.get_athlete("s-1")
    assert state["issued"] == 1  # токен закэширован
    state["valid"] = "t2"  # токен отозвали раньше срока → 401 → один перевыпуск
    assert provider.get_athlete("s-1").sub == "s-1"
    assert state["issued"] == 2
    state["now"] = 10_000.0  # срок истёк → новый токен заранее
    state["valid"] = "t3"
    provider.get_athlete("s-1")
    assert state["issued"] == 3


def test_client_credentials_failure_is_auth_error() -> None:
    auth = ClientCredentialsAuth("http://kc/token", "c", "bad")
    provider = HttpFSPProvider(client=_client(lambda r: httpx.Response(401)), auth=auth)
    with pytest.raises(FSPAuthError):
        provider.get_athlete("s-1")
    no_token = ClientCredentialsAuth("http://kc/token", "c", "s")
    with pytest.raises(FSPAuthError):  # 200 без access_token
        HttpFSPProvider(client=_client(lambda r: httpx.Response(200, json={})), auth=no_token).get_athlete("s-1")


def test_sso_outage_is_unavailable_and_falls_back(inproc: InProcessFSPProvider) -> None:
    # SSO отвечает 503 — это недоступность (резерв подхватит), а не отказ в доступе.
    auth = ClientCredentialsAuth("http://kc/token", "c", "s")
    http = HttpFSPProvider(client=_client(lambda r: httpx.Response(503)), auth=auth)
    with pytest.raises(FSPUnavailable):
        http.get_athlete("s-1")
    sub = inproc.snapshot.athletes[0].sub
    auth2 = ClientCredentialsAuth("http://kc/token", "c", "s")
    fb = FallbackFSPProvider(HttpFSPProvider(client=_client(lambda r: httpx.Response(503)), auth=auth2), inproc)
    assert fb.get_athlete(sub) == inproc.get_athlete(sub) and fb.last_used == "fallback"


def test_settings_share_env_and_defaults_with_core(monkeypatch: pytest.MonkeyPatch) -> None:
    # Один client в realm fsp: app/fsp и app/core читают одни и те же VZ_FSP_OIDC_* с одинаковыми умолчаниями.
    from app.core.settings import Settings

    for var in ("ISSUER", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        monkeypatch.delenv(f"VZ_FSP_OIDC_{var}", raising=False)
    monkeypatch.delenv("VZ_FSP_REGISTRY_URL", raising=False)
    core = Settings.model_fields
    s = FSPSettings()
    assert s.issuer == core["fsp_oidc_issuer"].default
    assert s.client_id == core["fsp_oidc_client_id"].default == mock_keycloak.CLIENT_ID
    assert s.redirect_uri == core["fsp_oidc_redirect_uri"].default
    assert s.redirect_uri in mock_keycloak.REDIRECT_URIS
    assert s.registry_url == core["fsp_registry_url"].default
    monkeypatch.setenv("VZ_FSP_OIDC_CLIENT_ID", "client-from-env")
    monkeypatch.setenv("VZ_FSP_OIDC_CLIENT_SECRET", "secret-from-env")
    monkeypatch.setenv("VZ_FSP_REGISTRY_URL", "http://fsp-mock:8001")
    s = FSPSettings()
    assert (s.client_id, s.client_secret, s.registry_url) == ("client-from-env", "secret-from-env", "http://fsp-mock:8001")


def test_build_provider_modes() -> None:
    assert isinstance(build_provider(FSPSettings(provider="inprocess")), InProcessFSPProvider)
    auto = build_provider(FSPSettings(provider="auto"))
    assert isinstance(auto, FallbackFSPProvider)
    assert isinstance(auto.fallback, InProcessFSPProvider)
    http = build_provider(FSPSettings(provider="http", token_url="http://kc/realms/fsp/protocol/openid-connect/token"))
    assert isinstance(http, HttpFSPProvider)
    assert isinstance(http._auth, ClientCredentialsAuth)
