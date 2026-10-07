"""Признаки кандидата для подбора (CandidateFeatures) и их кэш.

Публичный контракт (см. app/services/matching/__init__.py):

    build_candidate_features(db, user_id) -> list[CandidateFeatures]
        Строка на каждую подтверждённую специализацию (backend_python, frontend_js) и строка «fullstack»,
        если подтверждены обе (концепция §2.1: производная категория, грейд = меньший из двух; у всех строк
        такого кандидата is_fullstack=True — бейдж даёт adjS = 1,0 для брифов Backend/Frontend).
        Источники: квалификация (тест: уровень theta, SE, подбаллы модулей с собственным SE, «на границе», доверие),
        ФСП (VSS по дисциплинам из app/fsp/vss.py: скрытые результаты не учитываются, Q_min — там же),
        регулярные задачи (TaskAssignment), настройки кандидата (минимум и его видимость, форматы, город,
        релокация, отрасли, заявленные навыки — только серые чипы, DECISIONS п. 1).
        Ничего не пишет в БД; «дни назад» считаются от Clock.

    refresh_candidate_features(db, user_id) / invalidate_features(db, user_id)
        Пересобрать кэш сейчас / пометить устаревшим (пересоберётся при следующей подборке или банке).

Кэш (таблица matching_features) хранит признаки с АБСОЛЮТНЫМИ датами: сдвиг Clock (DEMO_MODE «+3 дня»)
меняет свежесть без пересборки. Пересборка — по хукам и по отпечатку источников (ensure_fresh): изменился
тест, профиль, согласие, ФСП или задачи → строки кандидата пересобираются пакетно перед подборкой.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Text, cast, delete, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.contact.quotas import min_possible_age
from app.contact.settings import get_contact_config
from app.core.clock import clock
from app.core.consents import on_consent_revoked
from app.fsp import vss as fsp_vss
from app.fsp.schema import FSPProfile
from app.matching.scoring import FORMATS, GRADES, CandidateFeatures, ModuleScore, TaskResult
from app.matching.skills import SkillCatalog, get_catalog
from app.models.enums import ConsentPurpose, SearchMode
from app.models.matching import MatchingFeatures
from app.models.users import Consent
from app.services.matching import sources
from app.services.matching.sources import QualSnap

log = logging.getLogger("vzaimno.matching.features")

PAYLOAD_VERSION = 1
FULLSTACK = "fullstack"
FULLSTACK_PARTS = ("backend_python", "frontend_js")
# Инфраструктурный срок жизни кэша (не продуктовое правило): VSS зависит от времени (полураспад 24 мес.),
# разряд истекает, возраст растёт — раз в неделю строки пересобираются, даже если источники не менялись.
CACHE_TTL = timedelta(days=7)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat()


def _days(now: datetime, dt: datetime | None) -> float | None:
    if dt is None:
        return None
    return max(0.0, (now - dt).total_seconds() / 86400.0)


def _dt(v: Any) -> datetime | None:
    return None if v is None else datetime.fromisoformat(v)


# --------------------------------------------------------------------------- сборка payload


def _catalog_city(cat: SkillCatalog, raw: str | None) -> str | None:
    """Город профиля → код справочника (как у разбора брифа), иначе None (сравнение городов не делаем)."""
    if not raw:
        return None
    if raw in cat.cities:
        return raw
    found = cat.find_cities(raw)
    return found[0].id if found else None


def _skill_ids(cat: SkillCatalog, items: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for it in items or ():
        raw = it
        if isinstance(it, Mapping):
            raw = it.get("skill") or it.get("skill_id") or it.get("id") or it.get("name") or it.get("tech")
        if not raw:
            continue
        raw = str(raw)
        if raw in cat.skills:
            out.append(raw)
            continue
        found = cat.find_skills(raw, fuzzy=False)
        if found:
            out.append(found[0].id)
    return sorted(dict.fromkeys(out))


def _lcb(q: QualSnap) -> float:
    return (q.theta if q.theta is not None else -9.0) - (q.se if q.se is not None else 1.0)


def _weaker(a: QualSnap, b: QualSnap) -> QualSnap:
    """Fullstack-строка держится на более слабой квалификации (меньший грейд, затем меньшая нижняя граница)."""
    ka = (GRADES.index(a.grade), _lcb(a)) if a.grade in GRADES else (-1, _lcb(a))
    kb = (GRADES.index(b.grade), _lcb(b)) if b.grade in GRADES else (-1, _lcb(b))
    return a if ka <= kb else b


def _modules_payload(*quals: QualSnap) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for q in quals:
        for m, ms in q.modules.items():
            cur = out.get(m)
            if cur is None or ms.theta - ms.se > cur[0] - cur[1]:
                out[m] = [ms.theta, ms.se, ms.tag]
    return dict(sorted(out.items()))


@dataclass(frozen=True)
class _Ctx:
    """Всё о кандидате, кроме квалификации (общее для его строк)."""

    active: bool
    blind_card: bool
    search_mode: str
    age_ok: bool
    internship_only: bool
    settings: dict[str, Any]
    tasks: list[list[Any]]
    fsp: FSPProfile
    fsp_features: fsp_vss.FSPFeatures


def _row_payload(
    qual: QualSnap,
    *,
    modules: dict[str, list[Any]],
    is_fullstack: bool,
    spec: str,
    ctx: _Ctx,
    now: datetime,
    unconfirmed: bool = False,
) -> tuple[dict[str, Any], bool]:
    """payload строки кэша и признак видимости работодателям.

    unconfirmed — заявленная категория без подтверждения тестом: видна по тем же правилам согласия, но помечена
    и в выдаче идёт ниже подтверждённых (DECISIONS п. 29)."""
    visible = (
        ctx.active
        and ctx.blind_card
        and ctx.search_mode != SearchMode.hidden.value
        and (qual.confirmed or unconfirmed)
        and not qual.hidden
        and ctx.age_ok
    )
    ff = ctx.fsp_features
    payload: dict[str, Any] = {
        "v": PAYLOAD_VERSION,
        # --- скоринг
        "theta": qual.theta,
        "se": qual.se if qual.se is not None else 0.5,
        "theta_at": _iso(qual.last_activity_at or qual.confirmed_at),
        "test_at": _iso(qual.confirmed_at),
        "test_session_id": qual.source_session_id,
        "borderline": bool(qual.borderline),
        "trust": qual.trust if qual.trust is not None else 1.0,
        "is_fullstack": is_fullstack,
        "modules": modules,
        "tasks": ctx.tasks,
        "vss": {k: round(float(v), 4) for k, v in sorted(ff.vss_by_discipline.items())},
        # Обобщённая строка лучшего результата под эту специализацию — только для «за» (§6.2).
        "fsp_summary": fsp_vss.public_summary(ctx.fsp, now, spec) if ff.has_fsp else None,
        **ctx.settings,
        # --- карточка (до раскрытия — только обобщённо)
        "fsp_state": str(ff.state),
        "fsp_line": fsp_vss.employer_line(ctx.fsp, now, spec),
        "fsp_public": [i.model_dump(mode="json") for i in ff.fsp_public],
        "fsp_badges": list(ff.badges),
        "fsp_source_label": ff.source_label,
        "search_mode": ctx.search_mode,
        "internship_only": ctx.internship_only,
        "last_activity_at": _iso(qual.last_activity_at or qual.confirmed_at),
    }
    if unconfirmed:
        payload["unconfirmed"] = True
    return payload, visible


def _settings(cat: SkillCatalog, prof: Any, survey: Any) -> dict[str, Any]:
    formats = [f for f in (getattr(prof, "formats", None) or ()) if f in FORMATS]
    industries = list(getattr(prof, "industries", None) or ()) or list(getattr(survey, "industries", None) or ())
    declared = _skill_ids(cat, list(getattr(prof, "declared_skills", None) or ()))
    declared = sorted(set(declared) | set(_skill_ids(cat, getattr(survey, "stack_declared", None) or ())))
    return {
        "salary_min": getattr(prof, "salary_min", None),
        "salary_net": (getattr(prof, "salary_type", "net") or "net") == "net",
        "salary_visibility": getattr(prof, "salary_visibility", "signal") or "signal",
        "formats": sorted(formats),
        "city": _catalog_city(cat, getattr(prof, "city", None)),
        "relocation": bool(getattr(prof, "relocation", False)),
        "industries": sorted(dict.fromkeys(str(i) for i in industries)),
        "declared": declared,
        "region_code": getattr(prof, "region_code", None),
        "blocked": sorted(str(c) for c in (getattr(prof, "blocked_company_ids", None) or ())),
    }


def build_payloads(
    db: Session, ids: Sequence[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, list[tuple[str, str, bool, bool, dict[str, Any]]]]:
    """Пакетная сборка строк кэша: {кандидат: [(spec, grade, visible, has_fsp, payload)]}."""
    now = now or clock.now()
    ids = list(dict.fromkeys(ids))
    cat = get_catalog()
    ccfg = get_contact_config()
    accounts = sources.load_accounts(db, ids)
    profiles = sources.load_profiles(db, ids)
    surveys = sources.load_surveys(db, ids)
    quals = sources.load_qualifications(db, ids, now)
    fsp = sources.load_fsp_profiles(db, ids, accounts, now)
    tasks = sources.load_tasks(db, ids)
    claims = sources.load_claims(db, ids, surveys)
    out: dict[uuid.UUID, list[tuple[str, str, bool, bool, dict[str, Any]]]] = {}
    for uid in ids:
        confirmed = [q for q in quals.get(uid, ()) if q.confirmed]
        confirmed_specs = {q.spec for q in confirmed}
        claimed = {s: g for s, g in claims.get(uid, {}).items() if s not in confirmed_specs}
        if not confirmed and not claimed:
            out[uid] = []
            continue
        prof = profiles.get(uid)
        acct = accounts.get(uid) or sources.AccountSnap(False, False, False)
        birth_year = getattr(prof, "birth_year", None)
        if birth_year is not None:
            age = min_possible_age(int(birth_year), now, ccfg)
            age_ok = age >= ccfg.minors.hidden_below_age
            internship_only = age < ccfg.minors.internship_only_below_age
        else:
            # Флаг «несовершеннолетний» без года рождения — скрываем (осторожно в пользу защиты ПДн).
            age_ok = not bool(getattr(prof, "is_minor", False))
            internship_only = False
        profile_fsp = fsp[uid]
        ctx = _Ctx(
            active=acct.active,
            blind_card=acct.blind_card,
            search_mode=getattr(prof, "search_mode", None) or SearchMode.open.value,
            age_ok=age_ok,
            internship_only=internship_only,
            settings=_settings(cat, prof, surveys.get(uid)),
            tasks=[
                [t.task_id, t.kind, round(t.q, 4), _iso(t.done_at), t.title, list(t.skills), t.module]
                for t in tasks.get(uid, ())
            ],
            fsp=profile_fsp,
            fsp_features=fsp_vss.build_features(profile_fsp, now),
        )
        by_spec = {q.spec: q for q in confirmed}
        fullstack = all(s in by_spec for s in FULLSTACK_PARTS)
        rows = []
        for q in sorted(confirmed, key=lambda x: x.spec):
            payload, visible = _row_payload(
                q, modules=_modules_payload(q), is_fullstack=fullstack, spec=q.spec, ctx=ctx, now=now
            )
            rows.append((q.spec, str(q.grade), visible, ctx.fsp_features.has_fsp, payload))
        if fullstack:
            a, b = (by_spec[s] for s in FULLSTACK_PARTS)
            weak = _weaker(a, b)
            merged = QualSnap(
                spec=FULLSTACK,
                status=weak.status,
                grade=weak.grade,
                theta=weak.theta,
                se=weak.se,
                borderline=weak.borderline,
                trust=min(x for x in (a.trust if a.trust is not None else 1.0, b.trust if b.trust is not None else 1.0)),
                hidden=a.hidden or b.hidden,
                confirmed_at=weak.confirmed_at,
                last_activity_at=max(
                    (x for x in (a.last_activity_at, b.last_activity_at) if x is not None), default=None
                ),
                source_session_id=weak.source_session_id,
            )
            payload, visible = _row_payload(
                merged, modules=_modules_payload(a, b), is_fullstack=True, spec=FULLSTACK, ctx=ctx, now=now
            )
            rows.append((FULLSTACK, str(merged.grade), visible, ctx.fsp_features.has_fsp, payload))
        for spec, grade in sorted(claimed.items()):
            snap = QualSnap(spec=spec, status="CLAIMED", grade=grade, theta=None, se=None)
            payload, visible = _row_payload(
                snap, modules={}, is_fullstack=False, spec=spec, ctx=ctx, now=now, unconfirmed=True
            )
            rows.append((spec, grade, visible, ctx.fsp_features.has_fsp, payload))
        out[uid] = rows
    return out


# --------------------------------------------------------------------------- payload → CandidateFeatures


def features_from_payload(
    candidate_id: str, spec: str, grade: str, p: Mapping[str, Any], now: datetime
) -> CandidateFeatures:
    theta_at = _dt(p.get("theta_at"))
    test_at = _dt(p.get("test_at"))
    tasks = tuple(
        TaskResult(t[0], t[1], float(t[2]), _days(now, _dt(t[3])) or 0.0, t[4] or "", tuple(t[5] or ()), t[6])
        for t in p.get("tasks") or ()
    )
    return CandidateFeatures(
        candidate_id=candidate_id,
        spec=spec,
        grade=grade,
        theta=p.get("theta"),
        se=float(p.get("se") or 0.5),
        theta_age_days=_days(now, theta_at) or 0.0,
        test_days_ago=_days(now, test_at),
        test_session_id=p.get("test_session_id"),
        borderline=bool(p.get("borderline")),
        trust=float(p.get("trust") if p.get("trust") is not None else 1.0),
        is_fullstack=bool(p.get("is_fullstack")),
        modules={m: ModuleScore(float(v[0]), float(v[1]), str(v[2])) for m, v in (p.get("modules") or {}).items()},
        tasks=tasks,
        vss_by_discipline={k: float(v) for k, v in (p.get("vss") or {}).items()},
        fsp_summary=p.get("fsp_summary"),
        fsp_ref=None,
        declared_skills=frozenset(p.get("declared") or ()),
        salary_min=p.get("salary_min"),
        salary_net=bool(p.get("salary_net", True)),
        salary_visibility=p.get("salary_visibility") or "signal",
        formats=frozenset(p.get("formats") or ()),
        city=p.get("city"),
        relocation=bool(p.get("relocation")),
        industries=frozenset(p.get("industries") or ()),
    )


def build_candidate_features(db: Session, user_id: uuid.UUID) -> list[CandidateFeatures]:
    """Признаки кандидата для скоринга по всем его категориям (без записи в кэш). См. docstring модуля."""
    now = clock.now()
    rows = build_payloads(db, [user_id], now).get(user_id, [])
    return [features_from_payload(str(user_id), spec, grade, p, now) for spec, grade, _, _, p in rows]


# --------------------------------------------------------------------------- кэш


def refresh_features(
    db: Session, ids: Sequence[uuid.UUID], fps: Mapping[uuid.UUID, str] | None = None
) -> int:
    """Пересобрать строки кэша для кандидатов (пакетно). Возвращает число записанных строк."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 0
    now = clock.now()
    fps = dict(fps) if fps is not None else sources.fingerprints(db, ids)
    built = build_payloads(db, ids, now)
    db.execute(delete(MatchingFeatures).where(MatchingFeatures.candidate_id.in_(ids)))
    values = [
        {
            "candidate_id": uid,
            "specialization": spec,
            "grade": grade,
            "visible": visible,
            "has_fsp": has_fsp,
            "payload": payload,
            "fingerprint": fps.get(uid),
            "computed_at": now,
        }
        for uid, rows in built.items()
        for spec, grade, visible, has_fsp, payload in rows
    ]
    if values:
        stmt = pg_insert(MatchingFeatures)
        stmt = stmt.on_conflict_do_update(
            index_elements=[MatchingFeatures.candidate_id, MatchingFeatures.specialization],
            set_={
                k: getattr(stmt.excluded, k)
                for k in ("grade", "visible", "has_fsp", "payload", "fingerprint", "computed_at")
            },
        )
        db.execute(stmt, values)
    db.flush()
    return len(values)


def refresh_candidate_features(db: Session, user_id: uuid.UUID) -> int:
    return refresh_features(db, [user_id])


def invalidate_features(db: Session, user_id: uuid.UUID) -> None:
    """Хук «данные кандидата изменились»: строки пересоберутся при следующей подборке или банке."""
    db.execute(
        update(MatchingFeatures)
        .where(MatchingFeatures.candidate_id == user_id)
        .values(fingerprint=None)
        .execution_options(synchronize_session=False)
    )


def ensure_fresh(db: Session) -> int:
    """Пересобрать устаревшие строки кэша (новые подтверждённые, изменившиеся источники, старше CACHE_TTL)."""
    stale = sources.stale_candidates(db, clock.now() - CACHE_TTL)
    if not stale:
        return 0
    log.info("Пересборка признаков: %d кандидатов", len(stale))
    return refresh_features(db, list(stale), stale)


def refresh_stale(db: Session, ids: Iterable[uuid.UUID]) -> int:
    """Пересобрать устаревшие строки кэша только этих кандидатов (отпечаток — запросами с фильтром по id).

    Для небольших наборов (карточка, закрепление, страница подборки): кандидат мог отозвать согласие, уйти
    в «не ищу» или заблокировать компанию после сборки — это видно сразу, без полной проверки свежести кэша.
    Кандидаты без строк кэша не трогаются: они и так не видны."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 0
    rows = db.execute(
        select(
            MatchingFeatures.candidate_id,
            func.min(MatchingFeatures.fingerprint),
            func.max(MatchingFeatures.fingerprint),
            func.bool_or(MatchingFeatures.fingerprint.is_(None)),
            func.min(MatchingFeatures.computed_at),
        )
        .where(MatchingFeatures.candidate_id.in_(ids))
        .group_by(MatchingFeatures.candidate_id)
    ).all()
    if not rows:
        return 0
    current = {r[0]: r[1:] for r in rows}
    fps = sources.fingerprints(db, list(current))
    cutoff = clock.now() - CACHE_TTL
    stale = {}
    for uid, (fp_min, fp_max, has_null, computed) in current.items():
        fp = fps.get(uid)
        if fp is None or has_null or fp_min != fp or fp_max != fp or computed < cutoff:
            stale[uid] = fp
    if not stale:
        return 0
    return refresh_features(db, list(stale), {k: v for k, v in stale.items() if v is not None})


def refresh_marked(db: Session) -> int:
    """Пересобрать строки, помеченные хуками устаревшими (отзыв согласия, профиль, ФСП, задачи, квалификация)
    или старше CACHE_TTL. Один короткий запрос без списка id: обычно таких строк нет."""
    ids = list(
        db.scalars(
            select(MatchingFeatures.candidate_id)
            .where(
                (MatchingFeatures.fingerprint.is_(None)) | (MatchingFeatures.computed_at < clock.now() - CACHE_TTL)
            )
            .distinct()
        )
    )
    return refresh_features(db, ids) if ids else 0


def visible_for_company(db: Session, company_id: uuid.UUID) -> set[uuid.UUID]:
    """Кого компания вправе видеть СЕЙЧАС: виден работодателям (согласие на слепую карточку, режим не «не ищу»,
    категория подтверждена, не на проверке, возраст) и не заблокировал эту компанию.

    Версии подборки неизменяемы, но отозвавший согласие кандидат пропадает из них сразу (152-ФЗ, spec-contact
    §3.3). Запрос без списка id (по всему кэшу): отправка тысяч id в Postgres дороже самого прохода по таблице."""
    blocked = func.coalesce(MatchingFeatures.payload["blocked"], text("'[]'::jsonb"))
    return set(
        db.scalars(
            select(MatchingFeatures.candidate_id)
            .where(MatchingFeatures.visible.is_(True), ~blocked.bool_op("?")(str(company_id)))
            .distinct()
        )
    )


@on_consent_revoked
def _on_consent_revoked(db: Session, consent: Consent) -> None:
    """Отзыв согласия на слепую карточку или на данные ФСП — кандидат сразу пропадает из банка/ФСП из F."""
    if consent.purpose in (ConsentPurpose.blind_card.value, ConsentPurpose.fsp_enrichment.value):
        invalidate_features(db, consent.user_id)
    if consent.purpose == ConsentPurpose.blind_card.value:
        # Без согласия на показ строки кэша не видны сразу, ещё до пересборки (защита в глубину).
        db.execute(
            update(MatchingFeatures)
            .where(MatchingFeatures.candidate_id == consent.user_id)
            .values(visible=False)
            .execution_options(synchronize_session=False)
        )


def _on_qualification_changed(db: Session, user_id: uuid.UUID, _spec: str) -> None:
    """Хук модуля тестирования: грейд присвоен, повышен, понижен или сессия ушла на проверку методисту."""
    invalidate_features(db, user_id)


try:  # модуль тестирования может собираться параллельно — без него работает отпечаток источников
    from app.services.testing.qualification import register_qualification_listener

    register_qualification_listener(_on_qualification_changed)
except Exception:  # noqa: BLE001
    log.info("Хук квалификаций не подключён: изменения подхватит отпечаток источников")


# --------------------------------------------------------------------------- пул для подборки и банка


@dataclass(frozen=True)
class PoolRow:
    candidate_id: uuid.UUID
    features: CandidateFeatures
    payload: Mapping[str, Any]


# Разобранные payload строк кэша в памяти процесса: ключ — (кандидат, специализация), версия — md5 содержимого,
# которую считает Postgres. Сборка подборки читает из БД только ключи и версии (без JSON), а сами payload —
# лишь для изменившихся строк. Несколько процессов не мешают друг другу: сверка идёт по содержимому.
_PAYLOADS: dict[tuple[uuid.UUID, str], tuple[str, Mapping[str, Any]]] = {}
_PAYLOADS_MAX = 50_000


def _payloads(db: Session, keys: Sequence[tuple[uuid.UUID, str, str]]) -> dict[tuple[uuid.UUID, str], Mapping[str, Any]]:
    """keys: (кандидат, специализация, md5 payload) → разобранные payload (из памяти или из БД)."""
    out: dict[tuple[uuid.UUID, str], Mapping[str, Any]] = {}
    missing = []
    for cid, spec, digest in keys:
        hit = _PAYLOADS.get((cid, spec))
        if hit is not None and hit[0] == digest:
            out[(cid, spec)] = hit[1]
        else:
            missing.append((cid, spec))
    if missing:
        if len(_PAYLOADS) + len(missing) > _PAYLOADS_MAX:
            _PAYLOADS.clear()
        rows = db.execute(
            select(
                MatchingFeatures.candidate_id,
                MatchingFeatures.specialization,
                func.md5(cast(MatchingFeatures.payload, Text)),
                MatchingFeatures.payload,
            ).where(tuple_(MatchingFeatures.candidate_id, MatchingFeatures.specialization).in_(missing))
        )
        for cid, spec, digest, payload in rows:
            _PAYLOADS[(cid, spec)] = (digest, payload)
            out[(cid, spec)] = payload
    return out


def load_pool(
    db: Session,
    categories: Sequence[tuple[str, str]] | None = None,
    *,
    now: datetime | None = None,
    specs: Sequence[str] | None = None,
    include_unconfirmed: bool = False,
) -> list[PoolRow]:
    """Видимые работодателям строки кэша выбранных категорий (или всех) → признаки для движка.

    include_unconfirmed — добавить заявленные категории без подтверждения тестом (подборка и банк показывают их
    со статусом и ниже подтверждённых; охват, рекомендации и перцентили считаются только по подтверждённым)."""
    now = now or clock.now()
    q = select(
        MatchingFeatures.candidate_id,
        MatchingFeatures.specialization,
        MatchingFeatures.grade,
        func.md5(cast(MatchingFeatures.payload, Text)),
    ).where(MatchingFeatures.visible.is_(True))
    if not include_unconfirmed:
        q = q.where(MatchingFeatures.payload["unconfirmed"].astext.is_distinct_from("true"))
    if categories is not None:
        cats = list(dict.fromkeys(tuple(c) for c in categories))
        if not cats:
            return []
        q = q.where(tuple_(MatchingFeatures.specialization, MatchingFeatures.grade).in_(cats))
    if specs is not None:
        q = q.where(MatchingFeatures.specialization.in_(list(specs)))
    keys = db.execute(q.order_by(MatchingFeatures.candidate_id, MatchingFeatures.specialization)).all()
    payloads = _payloads(db, [(cid, spec, digest) for cid, spec, _, digest in keys])
    out = []
    for cid, spec, grade, _ in keys:
        payload = payloads.get((cid, spec))
        if payload is None:  # строку пересобрали между двумя запросами — возьмём в следующий раз
            continue
        out.append(PoolRow(cid, features_from_payload(str(cid), spec, grade, payload, now), payload))
    return out


def category_counts(db: Session) -> dict[tuple[str, str], int]:
    """N видимых кандидатов по категориям (для рекомендаций и охвата)."""
    from sqlalchemy import func

    rows = db.execute(
        select(MatchingFeatures.specialization, MatchingFeatures.grade, func.count())
        .where(
            MatchingFeatures.visible.is_(True),
            MatchingFeatures.payload["unconfirmed"].astext.is_distinct_from("true"),
        )
        .group_by(MatchingFeatures.specialization, MatchingFeatures.grade)
    )
    return {(s, g): int(n) for s, g, n in rows}
