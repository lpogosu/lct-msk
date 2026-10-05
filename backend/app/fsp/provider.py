"""Адаптер к реестру ФСП: протокол FSPProvider, HTTP-клиент, встроенный резервный провайдер,
нормализация ответа в схему платформы и состояния профиля (концепция §6.4–6.5).

    Backend ──client credentials──▶ Реестр ФСП: GET /v1/athletes/{sub}/achievements
            ◀── JSON (app/fsp/models.py) ── normalize_achievements ──▶ FSPProfile ──▶ VSS

HttpFSPProvider ходит в сеть (мок services/fsp-mock или будущий API ФСП). InProcessFSPProvider отдаёт
те же данные из встроенного снимка демо-реестра (app/fsp/data/demo_registry.json) — демо не зависит
от того, поднялся ли мок. FallbackFSPProvider склеивает их: сеть, а при недоступности — снимок.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Generator, Iterable
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from app.core.clock import clock as platform_clock
from app.fsp.config import FSPConfig, get_fsp_config
from app.fsp.models import (
    Achievements,
    Athlete,
    Competition,
    CompetitionProtocol,
    ProtocolStatus,
    RegistrySnapshot,
    ResultStatus,
    Source,
)
from app.fsp.schema import FSPProfile, FSPState, NormalizedResult
from app.fsp.settings import FSPSettings

log = logging.getLogger(__name__)

DEFAULT_SNAPSHOT = Path(__file__).resolve().parent / "data" / "demo_registry.json"


class FSPError(Exception):
    """Ошибка обращения к реестру ФСП."""


class FSPUnavailable(FSPError):
    """Реестр недоступен (сеть, таймаут, 5xx) — можно переключиться на резервный провайдер."""


class FSPAuthError(FSPError):
    """Реестр отказал в доступе (401/403) или не выдан токен сервисного аккаунта."""


@runtime_checkable
class FSPProvider(Protocol):
    """Источник данных реестра. Нет записи → None, недоступность → FSPUnavailable."""

    def get_athlete(self, sub: str) -> Athlete | None: ...

    def get_achievements(self, sub: str) -> Achievements | None: ...

    def get_competition(self, ekp: str) -> CompetitionProtocol | None: ...


# --------------------------------------------------------------------------- аутентификация


class StaticBearerAuth(httpx.Auth):
    """Статический bearer-токен (мок реестра)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class ClientCredentialsAuth(httpx.Auth):
    """OAuth2 client credentials (сервисный аккаунт Keycloak): токен кэшируется до истечения,
    при 401 обновляется один раз. Так же будет работать с настоящим SSO ФСП."""

    requires_response_body = True

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        *,
        leeway_s: float = 30.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.scope = scope
        self.leeway_s = leeway_s
        self._monotonic = monotonic
        self._token: str | None = None
        self._expires_at = 0.0

    def _token_request(self) -> httpx.Request:
        data = {"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret}
        if self.scope:
            data["scope"] = self.scope
        return httpx.Request("POST", self.token_url, data=data)

    def _store(self, response: httpx.Response) -> None:
        if response.status_code >= 500:
            # SSO лежит — это недоступность, а не отказ в доступе: резервный провайдер может подхватить.
            raise FSPUnavailable(f"SSO ФСП недоступен: HTTP {response.status_code}")
        if response.status_code != 200:
            raise FSPAuthError(f"Сервисный аккаунт не получил токен: HTTP {response.status_code}")
        try:
            payload = response.json()
            token = payload["access_token"]
            expires_in = float(payload.get("expires_in", 60))
        except (ValueError, KeyError, TypeError) as exc:
            raise FSPAuthError("SSO ФСП вернул ответ без access_token") from exc
        if not isinstance(token, str) or not token:
            raise FSPAuthError("SSO ФСП вернул пустой access_token")
        self._token = token
        self._expires_at = self._monotonic() + expires_in - self.leeway_s

    def _fresh(self) -> bool:
        return self._token is not None and self._monotonic() < self._expires_at

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if not self._fresh():
            self._store((yield self._token_request()))
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            # Токен могли отозвать раньше срока — одна попытка с новым.
            self._store((yield self._token_request()))
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request


# --------------------------------------------------------------------------- провайдеры


class HttpFSPProvider:
    """Реестр по HTTP (контракт app/fsp/models.py). `client` можно подменить в тестах (TestClient мока)."""

    def __init__(
        self,
        base_url: str = "",
        *,
        auth: httpx.Auth | None = None,
        token: str | None = None,
        client: httpx.Client | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._client = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._auth: httpx.Auth | None = auth or (StaticBearerAuth(token) if token else None)

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str) -> Any | None:
        auth = self._auth if self._auth is not None else httpx.USE_CLIENT_DEFAULT
        try:
            resp = self._client.get(path, auth=auth)
        except httpx.HTTPError as exc:
            raise FSPUnavailable(f"Реестр ФСП недоступен: {exc.__class__.__name__}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code in (401, 403):
            raise FSPAuthError(f"Реестр ФСП отказал в доступе: HTTP {resp.status_code}")
        if resp.status_code >= 500:
            raise FSPUnavailable(f"Реестр ФСП вернул HTTP {resp.status_code}")
        if not resp.is_success:  # 3xx (редиректы не следуем) и прочие 4xx
            raise FSPError(f"Реестр ФСП вернул HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:  # 200 с HTML-страницей прокси и т. п.
            raise FSPError("Ответ реестра ФСП не JSON") from exc

    @staticmethod
    def _parse(model: type, data: Any) -> Any:
        if data is None:
            return None
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise FSPError("Ответ реестра ФСП не соответствует контракту") from exc

    def get_athlete(self, sub: str) -> Athlete | None:
        return self._parse(Athlete, self._get(f"/v1/athletes/{quote(sub, safe='')}"))

    def get_achievements(self, sub: str) -> Achievements | None:
        return self._parse(Achievements, self._get(f"/v1/athletes/{quote(sub, safe='')}/achievements"))

    def get_competition(self, ekp: str) -> CompetitionProtocol | None:
        return self._parse(CompetitionProtocol, self._get(f"/v1/competitions/{quote(ekp, safe='')}"))


@lru_cache(maxsize=4)
def _load_snapshot(path: str) -> RegistrySnapshot:
    return RegistrySnapshot.model_validate_json(Path(path).read_text(encoding="utf-8"))


class InProcessFSPProvider:
    """Встроенный демо-реестр: те же ответы, что у мока, без сети (резерв для демо)."""

    def __init__(self, snapshot: RegistrySnapshot, *, clock: Callable[[], datetime] | None = None) -> None:
        self._snap = snapshot
        self._clock = clock or platform_clock.now  # Clock платформы: DEMO_MODE-сдвиг виден и в as_of
        self._athletes = {a.sub: a for a in snapshot.athletes}
        self._competitions = {c.ekp: c for c in snapshot.competitions}

    @classmethod
    def from_file(
        cls, path: str | Path | None = None, *, clock: Callable[[], datetime] | None = None
    ) -> InProcessFSPProvider:
        return cls(_load_snapshot(str(path or DEFAULT_SNAPSHOT)), clock=clock)

    @property
    def snapshot(self) -> RegistrySnapshot:
        return self._snap

    def get_athlete(self, sub: str) -> Athlete | None:
        return self._athletes.get(sub)

    def get_achievements(self, sub: str) -> Achievements | None:
        if sub not in self._athletes:
            return None
        return assemble_achievements(self._snap, sub, as_of=self._clock(), source=Source.fsp_mock)

    def get_competition(self, ekp: str) -> CompetitionProtocol | None:
        comp = self._competitions.get(ekp)
        if comp is None:
            return None
        results = sorted(
            (r for r in self._snap.results if r.ekp == ekp),
            key=lambda r: (r.place if r.place is not None else 10**9, r.athlete_sub),
        )
        return CompetitionProtocol(competition=comp, results=results)


def assemble_achievements(snapshot: RegistrySnapshot, sub: str, *, as_of: datetime, source: Source) -> Achievements:
    """Сборка ответа /achievements из плоской выгрузки (тот же порядок, что у мока)."""
    comps = {c.ekp: c for c in snapshot.competitions}
    results = [r for r in snapshot.results if r.athlete_sub == sub]
    results.sort(key=lambda r: (-comps[r.ekp].end_date.toordinal(), r.ekp, r.id))
    used: list[Competition] = []
    seen: set[str] = set()
    for r in results:
        if r.ekp not in seen:
            seen.add(r.ekp)
            used.append(comps[r.ekp])
    return Achievements(
        athlete_sub=sub,
        as_of=as_of,
        source=source,
        results=results,
        competitions=used,
        ranks=sorted((r for r in snapshot.ranks if r.athlete_sub == sub), key=lambda r: (r.assigned_at, r.id)),
        roles=sorted((r for r in snapshot.roles if r.athlete_sub == sub), key=lambda r: (r.kind.value, r.id)),
    )


class FallbackFSPProvider:
    """Сначала основной провайдер; при FSPUnavailable — резервный. Ошибки доступа не маскируем.

    Если в резервном снимке записи нет, остаётся FSPUnavailable, а не None: иначе сбой сети у настоящего
    реестра выглядел бы как «результатов нет», и синхронизация обнулила бы F у реального кандидата."""

    def __init__(self, primary: FSPProvider, fallback: FSPProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.last_used: str | None = None

    def _call(self, name: str, arg: str) -> Any:
        try:
            result = getattr(self.primary, name)(arg)
            self.last_used = "primary"
            return result
        except FSPUnavailable as exc:
            result = getattr(self.fallback, name)(arg)
            if result is None:
                self.last_used = None
                raise
            log.warning("Реестр ФСП недоступен (%s), используем встроенный демо-реестр", exc)
            self.last_used = "fallback"
            return result

    def get_athlete(self, sub: str) -> Athlete | None:
        return self._call("get_athlete", sub)

    def get_achievements(self, sub: str) -> Achievements | None:
        return self._call("get_achievements", sub)

    def get_competition(self, ekp: str) -> CompetitionProtocol | None:
        return self._call("get_competition", ekp)


def build_provider(
    settings: FSPSettings | None = None, *, clock: Callable[[], datetime] | None = None
) -> FSPProvider:
    """Провайдер по настройкам VZ_FSP_*: http | inprocess | auto (сеть + резервный снимок)."""
    s = settings or FSPSettings()
    if s.provider == "inprocess":
        return InProcessFSPProvider.from_file(s.snapshot_path, clock=clock)
    auth: httpx.Auth | None
    if s.token_url:
        auth = ClientCredentialsAuth(s.token_url, s.client_id, s.client_secret or "", s.registry_scope)
    elif s.registry_token:
        auth = StaticBearerAuth(s.registry_token)
    else:
        auth = None
    http = HttpFSPProvider(s.registry_url, auth=auth, timeout_s=s.timeout_s)
    if s.provider == "http":
        return http
    return FallbackFSPProvider(http, InProcessFSPProvider.from_file(s.snapshot_path, clock=clock))


# --------------------------------------------------------------------------- нормализация


def normalize_achievements(
    ach: Achievements,
    *,
    hidden_result_ids: Iterable[str] = (),
    fetched_at: datetime | None = None,
    athlete: Athlete | None = None,
    cfg: FSPConfig | None = None,
) -> FSPProfile:
    """Ответ реестра → FSPProfile. Состояние (концепция §6.4):
    linked — есть подтверждённый результат или разряд; awaiting_protocol — есть только результаты,
    ждущие протокола; linked_no_results — ничего. Скрытые результаты остаются в профиле с hidden=True
    (кандидат видит и может вернуть), но в VSS и в выдачу работодателю не попадают."""
    c = cfg or get_fsp_config()
    hidden = set(hidden_result_ids)
    comps = {comp.ekp: comp for comp in ach.competitions}
    owner = ach.athlete_sub

    def own(items: list[Any], kind: str) -> list[Any]:
        # Чужая запись в ответе — ошибка реестра; к кандидату её не приклеиваем (иначе — чужие достижения).
        kept = [x for x in items if x.athlete_sub == owner]
        if len(kept) != len(items):
            log.warning("Реестр ФСП вернул %d чужих записей (%s) для %s — пропущены", len(items) - len(kept), kind, owner)
        return kept

    results: list[NormalizedResult] = []
    for r in own(ach.results, "results"):
        comp = comps.get(r.ekp)
        if comp is None:
            log.warning("Результат %s ссылается на ЕКП %s, которого нет в ответе — пропущен", r.id, r.ekp)
            continue
        disc = c.discipline_by_code(comp.discipline_code)
        confirmed = r.status == ResultStatus.confirmed and comp.protocol_status == ProtocolStatus.published
        results.append(
            NormalizedResult(
                result_id=r.id,
                ekp=r.ekp,
                group_key=comp.parent_ekp or comp.ekp,
                title=comp.title,
                discipline=disc.short if disc else None,
                discipline_code=comp.discipline_code,
                level=comp.level,
                status_type=comp.status_type,
                stage=comp.stage,
                age_group=comp.age_group,
                format=comp.format,
                start_date=comp.start_date,
                end_date=comp.end_date,
                field_size=r.field_size,
                place=r.place,
                place_to=r.place_to,
                score=r.score,
                max_score=comp.max_score,
                role=r.role,
                team_id=r.team.id if r.team else None,
                team_name=r.team.name if r.team else None,
                team_size=len(r.team.members) if r.team and r.team.members else None,
                team_function=r.team_function,
                protocol_url=comp.protocol_url if confirmed else None,
                source=ach.source,
                confirmed=confirmed,
                hidden=r.id in hidden,
            )
        )
    ranks = own(ach.ranks, "ranks")
    if any(r.confirmed for r in results) or ranks:
        state = FSPState.linked
    elif results:
        state = FSPState.awaiting_protocol
    else:
        state = FSPState.linked_no_results
    return FSPProfile(
        sub=ach.athlete_sub,
        state=state,
        athlete=athlete,
        results=results,
        ranks=ranks,
        roles=own(ach.roles, "roles"),
        source=ach.source,
        fetched_at=fetched_at or ach.as_of,
    )


def load_profile(
    provider: FSPProvider,
    sub: str | None,
    *,
    now: datetime,
    hidden_result_ids: Iterable[str] = (),
    cfg: FSPConfig | None = None,
) -> FSPProfile:
    """Профиль ФСП кандидата: sub пуст → not_linked; спортсмена нет в реестре → linked_no_results.
    FSPUnavailable / FSPAuthError пробрасываются: вызывающий оставляет прежние данные, а не обнуляет их."""
    if not sub:
        return FSPProfile.not_linked()
    ach = provider.get_achievements(sub)
    if ach is not None and ach.athlete_sub != sub:
        raise FSPError("Реестр ФСП вернул достижения другого спортсмена")
    athlete = provider.get_athlete(sub)
    if athlete is not None and athlete.sub != sub:
        raise FSPError("Реестр ФСП вернул другого спортсмена")
    if ach is None:
        return FSPProfile(sub=sub, state=FSPState.linked_no_results, athlete=athlete, fetched_at=now)
    return normalize_achievements(ach, hidden_result_ids=hidden_result_ids, fetched_at=now, athlete=athlete, cfg=cfg)
