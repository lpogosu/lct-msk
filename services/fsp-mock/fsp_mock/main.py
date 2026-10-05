"""Демо-реестр ФСП: FastAPI-приложение по контракту fsp_mock/contract.py (= backend/app/fsp/models.py).

Запуск: `uvicorn fsp_mock.main:app --port 8001` из каталога services/fsp-mock (порт как в docker-compose.yml).
Переменные окружения:
  FSP_MOCK_TOKENS      — допустимые bearer-токены через запятую (по умолчанию demo-fsp-registry-token);
  FSP_MOCK_PUBLIC_URL  — внешний адрес мока для ссылок на протоколы (по умолчанию http://localhost:8001);
  FSP_MOCK_SEED        — seed генератора (по умолчанию 20261011);
  FSP_MOCK_AS_OF       — зафиксировать поле as_of (ISO 8601), иначе текущее время UTC.
"""

from __future__ import annotations

import html
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .contract import (
    CONTRACT_VERSION,
    Achievements,
    Athlete,
    Competition,
    CompetitionProtocol,
    ErrorResponse,
    RegistrySnapshot,
    Result,
    Source,
)
from .generator import DEFAULT_PUBLIC_URL, SEED, build_dataset

DEFAULT_TOKEN = "demo-fsp-registry-token"

DESCRIPTION = """
**Демо-реестр достижений Федерации спортивного программирования.**

Это предлагаемый ФСП контракт API реестра (OpenAPI) и его мок с синтетическими данными: все спортсмены,
команды и номера ЕКП вымышлены (в ЕКП после года стоит «99» — признак синтетики).

* Ключ спортсмена — `sub` из ФСП ID (Keycloak, realm `fsp`).
* Соревнование — номер ЕКП из 16 цифр; отбор и финал связаны полем `parent_ekp`.
* Результат — место (или диапазон мест) из N, балл, роль: личный зачёт / капитан / участник / запасной,
  команда и её состав.
* Разряды — с датой присвоения, сроком действия и номером приказа. Судьи и тренеры — отдельные роли.
* Результаты, по которым протокол ещё не сдан, приходят со статусом `awaiting_protocol`.

Доступ — по bearer-токену (в демо статический; в проде — client credentials сервисного аккаунта).
"""

TAGS = [
    {"name": "спортсмены", "description": "Профиль спортсмена и его достижения по `sub` ФСП ID"},
    {"name": "соревнования", "description": "Соревнования ЕКП и выписка результатов"},
    {"name": "протоколы", "description": "Публичные страницы протоколов (демо)"},
    {"name": "служебное", "description": "Проверка работоспособности"},
]

app = FastAPI(
    title="Реестр ФСП — демо",
    version=CONTRACT_VERSION,
    description=DESCRIPTION,
    openapi_tags=TAGS,
    swagger_ui_parameters={"defaultModelsExpandDepth": 0},
)

_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="Токен реестра",
    description="Bearer-токен. В демо: `demo-fsp-registry-token` (переменная FSP_MOCK_TOKENS).",
)

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Нет или неверный токен доступа"},
    404: {"model": ErrorResponse, "description": "Не найдено в реестре"},
}


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """422 с понятным текстом по-русски (по умолчанию FastAPI отвечает по-английски)."""
    fields = sorted({str(e.get("loc", ("", ""))[-1]) for e in exc.errors()})
    hint = " Номер ЕКП — ровно 16 цифр." if "ekp" in fields else ""
    return JSONResponse(
        status_code=422,
        content={"detail": f"Некорректный запрос: проверьте параметры {', '.join(fields)}.{hint}"},
    )


@dataclass(frozen=True)
class _Registry:
    snapshot: RegistrySnapshot
    athletes: dict[str, Athlete]
    competitions: dict[str, Competition]
    results_by_sub: dict[str, list[Result]]
    results_by_ekp: dict[str, list[Result]]


@lru_cache(maxsize=1)
def registry() -> _Registry:
    seed = int(os.environ.get("FSP_MOCK_SEED", SEED))
    snap = build_dataset(seed=seed, public_url=os.environ.get("FSP_MOCK_PUBLIC_URL", DEFAULT_PUBLIC_URL))
    by_sub: dict[str, list[Result]] = {}
    by_ekp: dict[str, list[Result]] = {}
    for r in snap.results:
        by_sub.setdefault(r.athlete_sub, []).append(r)
        by_ekp.setdefault(r.ekp, []).append(r)
    return _Registry(
        snapshot=snap,
        athletes={a.sub: a for a in snap.athletes},
        competitions={c.ekp: c for c in snap.competitions},
        results_by_sub=by_sub,
        results_by_ekp=by_ekp,
    )


def _tokens() -> set[str]:
    raw = os.environ.get("FSP_MOCK_TOKENS", DEFAULT_TOKEN)
    return {t.strip() for t in raw.split(",") if t.strip()}


def _token_ok(presented: str) -> bool:
    # Сравнение за постоянное время: по времени ответа токен не подобрать.
    given = presented.encode()
    return any(secrets.compare_digest(given, t.encode()) for t in _tokens())


def require_token(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> None:
    if creds is None or creds.scheme.lower() != "bearer" or not _token_ok(creds.credentials):
        raise HTTPException(
            status_code=401,
            detail="Нужен действительный токен доступа к реестру (Authorization: Bearer …)",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _as_of() -> datetime:
    frozen = os.environ.get("FSP_MOCK_AS_OF")
    return datetime.fromisoformat(frozen) if frozen else datetime.now(UTC)


SubParam = Annotated[str, PathParam(description="`sub` спортсмена из ФСП ID")]
EkpParam = Annotated[str, PathParam(pattern=r"^\d{16}$", description="Номер ЕКП, 16 цифр")]


@app.get("/health", tags=["служебное"], summary="Проверка работоспособности")
def health() -> dict[str, str]:
    return {"status": "ok", "contract_version": CONTRACT_VERSION}


@app.get(
    "/v1/athletes/{sub}",
    response_model=Athlete,
    tags=["спортсмены"],
    summary="Спортсмен по ФСП ID",
    description="Профиль спортсмена: ФИО, дата рождения, регион. Ключ — `sub` из ФСП ID.",
    responses=_ERRORS,
    dependencies=[Depends(require_token)],
)
def get_athlete(sub: SubParam) -> Athlete:
    athlete = registry().athletes.get(sub)
    if athlete is None:
        raise HTTPException(status_code=404, detail="Спортсмен с таким sub не найден в реестре")
    return athlete


@app.get(
    "/v1/athletes/{sub}/achievements",
    response_model=Achievements,
    tags=["спортсмены"],
    summary="Достижения спортсмена",
    description=(
        "Результаты (вместе с соревнованиями, на которые они ссылаются), разряды и судейские/тренерские роли. "
        "Пустые списки — спортсмен есть в реестре, но результатов нет."
    ),
    responses=_ERRORS,
    dependencies=[Depends(require_token)],
)
def get_achievements(sub: SubParam) -> Achievements:
    reg = registry()
    if sub not in reg.athletes:
        raise HTTPException(status_code=404, detail="Спортсмен с таким sub не найден в реестре")
    results = sorted(
        reg.results_by_sub.get(sub, []),
        key=lambda r: (-reg.competitions[r.ekp].end_date.toordinal(), r.ekp, r.id),
    )
    used: list[Competition] = []
    seen: set[str] = set()
    for r in results:
        if r.ekp not in seen:
            seen.add(r.ekp)
            used.append(reg.competitions[r.ekp])
    snap = reg.snapshot
    return Achievements(
        athlete_sub=sub,
        as_of=_as_of(),
        source=Source.fsp_mock,
        results=results,
        competitions=used,
        ranks=sorted((r for r in snap.ranks if r.athlete_sub == sub), key=lambda r: (r.assigned_at, r.id)),
        roles=sorted((r for r in snap.roles if r.athlete_sub == sub), key=lambda r: (r.kind.value, r.id)),
    )


@app.get(
    "/v1/competitions/{ekp}",
    response_model=CompetitionProtocol,
    tags=["соревнования"],
    summary="Соревнование по номеру ЕКП",
    description="Карточка соревнования и результаты спортсменов, которые есть в реестре, по возрастанию места.",
    responses=_ERRORS,
    dependencies=[Depends(require_token)],
)
def get_competition(ekp: EkpParam) -> CompetitionProtocol:
    reg = registry()
    comp = reg.competitions.get(ekp)
    if comp is None:
        raise HTTPException(status_code=404, detail="Соревнование с таким номером ЕКП не найдено")
    results = sorted(
        reg.results_by_ekp.get(ekp, []),
        key=lambda r: (r.place if r.place is not None else 10**9, r.athlete_sub),
    )
    return CompetitionProtocol(competition=comp, results=results)


@app.get(
    "/protocols/{ekp}",
    response_class=HTMLResponse,
    tags=["протоколы"],
    summary="Страница протокола (демо)",
    responses={404: {"description": "Протокол не найден или ещё не опубликован"}},
)
def protocol_page(ekp: EkpParam) -> HTMLResponse:
    reg = registry()
    comp = reg.competitions.get(ekp)
    if comp is None or comp.protocol_url is None:
        raise HTTPException(status_code=404, detail="Протокол не найден или ещё не опубликован")
    rows = []
    for r in sorted(reg.results_by_ekp.get(ekp, []), key=lambda x: (x.place or 10**9, x.athlete_sub)):
        a = reg.athletes[r.athlete_sub]
        place = f"{r.place}–{r.place_to}" if r.place_to else (str(r.place) if r.place else "—")
        team = r.team.name if r.team else ""
        score = "" if r.score is None else f"{r.score:g}"
        cells = [place, f"{a.last_name} {a.first_name}", team, a.region_name, score]
        rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
    dates = f"{comp.start_date:%d.%m.%Y}–{comp.end_date:%d.%m.%Y}"
    page = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Протокол {ekp}</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#1B1C21}}table{{border-collapse:collapse}}
td,th{{border:1px solid #C8C9CA;padding:4px 8px;text-align:left}}.demo{{color:#402FFF;font-weight:600}}</style>
</head><body><p class="demo">Демо-реестр ФСП · синтетические данные</p>
<h1>{html.escape(comp.title)}</h1><p>ЕКП {ekp} · {dates} · {html.escape(comp.city or "онлайн")}
· N = {comp.field_size}</p><p>Выписка: только спортсмены, которые есть в демо-реестре.</p>
<table><tr><th>Место</th><th>Спортсмен</th><th>Команда</th><th>Регион</th><th>Балл</th></tr>
{"".join(rows)}</table></body></html>"""
    return HTMLResponse(page)
