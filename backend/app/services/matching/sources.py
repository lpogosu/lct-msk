"""Источники признаков кандидата: пакетная загрузка из БД и адаптеры к контрактам других модулей.

- Квалификация — контракт модуля тестирования app/services/testing/qualification.py:get_qualification_snapshot
  (если его ещё нет — денормализованные колонки таблицы qualifications, те же поля).
- ФСП — FspLink + кэш достижений FspAchievementCache (нормализованные записи app/fsp/schema.py); без кэша —
  встроенный демо-реестр (InProcessFSPProvider, без сети). Скрытые кандидатом результаты в VSS не идут,
  Q_min и матрицы m(d, s) — внутри app/fsp/vss.py. Без согласия fsp_enrichment данные ФСП не используются.
- Регулярные задачи — TaskAssignment + EmployerTask (тип A — catalog, тип B — approach).
- Настройки кандидата (К13) — CandidateProfile; отрасли и заявленный стек — ещё и из опроса.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import lru_cache
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.contact.settings import get_contact_config
from app.core.clock import clock
from app.core.configs import load_config
from app.fsp.models import Rank, RoleBadge
from app.fsp.schema import FSPProfile, FSPState, NormalizedResult
from app.matching.scoring import GRADES
from app.models.candidate import CandidateProfile, SurveyResponse
from app.models.enums import AssignmentStatus, ConsentPurpose, QualificationStatus, TaskKind
from app.models.fsp import FspAchievementCache, FspLink
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.testing import ItemFamilyParams, Qualification
from app.models.users import Consent, User

log = logging.getLogger("vzaimno.matching.sources")

CONFIRMED_STATUSES = (QualificationStatus.confirmed.value, QualificationStatus.upgrade_testing.value)
SCORED_TASK_STATUSES = (AssignmentStatus.auto_scored.value, AssignmentStatus.reviewed.value)


# --------------------------------------------------------------------------- общие помощники


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Поле из dict или атрибут объекта (контракты других модулей могут вернуть и то, и другое)."""
    for n in names:
        if isinstance(obj, Mapping):
            if n in obj and obj[n] is not None:
                return obj[n]
        elif getattr(obj, n, None) is not None:
            return getattr(obj, n)
    return default


def _dt(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- квалификация


@dataclass(frozen=True)
class ModuleSnap:
    theta: float
    se: float
    tag: str


@dataclass(frozen=True)
class QualSnap:
    """Квалификация кандидата по одной специализации — то, что нужно подбору (spec-testing §3.12)."""

    spec: str
    status: str
    grade: str | None
    theta: float | None
    se: float | None
    borderline: bool = False
    trust: float | None = None
    hidden: bool = False  # сессия на проверке у методиста — кандидат скрыт из витрины
    confirmed_at: datetime | None = None
    last_activity_at: datetime | None = None
    source_session_id: str | None = None
    modules: Mapping[str, ModuleSnap] = field(default_factory=dict)

    @property
    def confirmed(self) -> bool:
        return self.status in CONFIRMED_STATUSES and self.grade is not None


def _modules_of(raw: Any, fallback_se: float | None) -> dict[str, ModuleSnap]:
    """Подбаллы модулей: subscores {m: {theta, se, tag}} (SE модуля — из subscores, иначе общий SE)."""
    subs = _get(raw, "subscores", "modules", default={}) or {}
    tags = _get(raw, "module_tags", default={}) or {}
    thetas = _get(raw, "module_theta", default={}) or {}
    out: dict[str, ModuleSnap] = {}
    for m in sorted(set(subs) | set(tags) | set(thetas)):
        s = subs.get(m) if isinstance(subs, Mapping) else None
        th = _f(_get(s, "theta")) if s is not None else None
        if th is None:
            th = _f(thetas.get(m))
        if th is None:
            continue
        se = _f(_get(s, "se")) if s is not None else None
        tag = (_get(s, "tag") if s is not None else None) or tags.get(m) or "none"
        out[m] = ModuleSnap(th, se if se is not None else (fallback_se or 0.5), str(tag))
    return out


def snapshot_from_any(raw: Any, spec: str | None = None) -> QualSnap | None:
    """Снимок квалификации из словаря / объекта / ORM-строки (поля как в Qualification.handoff())."""
    sp = _get(raw, "spec", "specialization", default=spec)
    if sp is None:
        return None
    status = str(_get(raw, "status", default="") or "").upper()
    grade = _get(raw, "grade", "confirmed_grade")
    if not status:
        status = QualificationStatus.confirmed.value if _get(raw, "confirmed", default=False) else ""
    hidden = bool(_get(raw, "hidden", "under_review", default=False))
    visible = _get(raw, "visible")
    if visible is False:
        hidden = True
    se = _f(_get(raw, "se"))
    return QualSnap(
        spec=str(sp),
        status=status,
        grade=grade,
        theta=_f(_get(raw, "theta")),
        se=se,
        borderline=bool(_get(raw, "borderline", default=False)),
        trust=_f(_get(raw, "trust")),
        hidden=hidden,
        confirmed_at=_dt(_get(raw, "confirmed_at", "test_at")),
        last_activity_at=_dt(_get(raw, "last_activity_at")),
        source_session_id=(str(v) if (v := _get(raw, "source_session_id", "session_id")) is not None else None),
        modules=_modules_of(raw, se),
    )


def normalize_snapshots(raw: Any) -> list[QualSnap]:
    """Любая разумная форма ответа get_qualification_snapshot → список снимков по специализациям."""
    if raw is None:
        return []
    inner = _get(raw, "qualifications", "items") if not isinstance(raw, list | tuple) else None
    if inner is not None and not isinstance(inner, str):
        return normalize_snapshots(inner)
    if isinstance(raw, Mapping) and not ({"spec", "specialization"} & set(raw)):
        out = []
        for k, v in raw.items():
            snap = snapshot_from_any(v, spec=str(k)) if isinstance(v, Mapping) or hasattr(v, "__dict__") else None
            if snap is not None:
                out.append(snap)
        return out
    if isinstance(raw, list | tuple):
        return [s for s in (snapshot_from_any(x) for x in raw) if s is not None]
    snap = snapshot_from_any(raw)
    return [snap] if snap is not None else []


def _service_snapshot() -> Callable[..., Any] | None:
    """Снимок квалификации по уже загруженной строке: (db, row, now) → QualificationSnapshot | None.

    Публичный контракт модуля тестирования — get_qualification_snapshot(db, user_id, spec) (запрос на каждую
    специализацию). При пересборке кэша на тысячах кандидатов строки уже загружены пачкой, поэтому, если модуль
    отдаёт свой сборщик снимка по строке (load_domain + _snapshot_of), берём его — результат тот же, без N запросов.
    """
    try:
        from app.services.testing import qualification as tq
    except Exception:  # noqa: BLE001 — модуль тестирования собирается параллельно; без него читаем таблицу
        return None
    load, snap_of = getattr(tq, "load_domain", None), getattr(tq, "_snapshot_of", None)
    if callable(load) and callable(snap_of):
        return lambda db, row, now: snap_of(row.candidate_id, load(row), now)
    public = getattr(tq, "get_qualification_snapshot", None)
    if not callable(public):
        return None
    return lambda db, row, now: public(db, row.candidate_id, row.specialization, now=now)


# Fullstack — производная категория: её строку собирает features.py из двух квалификаций (грейд — меньший).
DERIVED_SPECS = frozenset({"fullstack"})


def snapshots_from_table(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[QualSnap]]:
    """Денормализованные колонки qualifications (их пишет модуль тестирования) — пакетно."""
    out: dict[uuid.UUID, list[QualSnap]] = defaultdict(list)
    if not ids:
        return out
    for q in db.scalars(select(Qualification).where(Qualification.candidate_id.in_(ids))):
        if q.specialization in DERIVED_SPECS:
            continue
        snap = snapshot_from_any(q)
        if snap is not None:
            out[q.candidate_id].append(snap)
    return out


def load_qualifications(
    db: Session, ids: Sequence[uuid.UUID], now: datetime | None = None
) -> dict[uuid.UUID, list[QualSnap]]:
    """Квалификации кандидатов по контракту модуля тестирования:
    get_qualification_snapshot(db, user_id, specialization) — по каждой специализации из таблицы qualifications.

    Подбаллы модулей берутся из снимка вместе с их собственной погрешностью (build-reviews: «adapter has to take
    the SE from subscores»); id попытки теста для ссылки-доказательства — из строки таблицы (в снимке его нет).
    Без модуля тестирования или при сбое контракта — денормализованные колонки той же таблицы.
    """
    out: dict[uuid.UUID, list[QualSnap]] = defaultdict(list)
    if not ids:
        return out
    fn = _service_snapshot()
    rows = db.scalars(
        select(Qualification)
        .where(Qualification.candidate_id.in_(ids))
        .order_by(Qualification.candidate_id, Qualification.specialization)
    )
    for q in rows:
        if q.specialization in DERIVED_SPECS:
            continue
        snap: QualSnap | None = None
        if fn is not None:
            try:
                raw = fn(db, q, now or clock.now())
            except Exception:  # noqa: BLE001 — сбой чужого контракта не должен ронять подборку
                log.exception("get_qualification_snapshot упал для %s/%s — читаем колонки", q.candidate_id, q.specialization)
                raw = q
            if raw is None:
                continue
            snap = snapshot_from_any(raw, spec=q.specialization)
        else:
            snap = snapshot_from_any(q)
        if snap is None:
            continue
        if q.source_session_id is not None:
            snap = replace(snap, source_session_id=str(q.source_session_id))
        out[q.candidate_id].append(snap)
    return out


# --------------------------------------------------------------------------- пользователи, профиль, согласия


@dataclass(frozen=True)
class AccountSnap:
    active: bool
    blind_card: bool
    fsp_enrichment: bool


def load_accounts(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, AccountSnap]:
    if not ids:
        return {}
    users = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_(ids)))
    }
    consents: dict[uuid.UUID, set[str]] = defaultdict(set)
    rows = db.execute(
        select(Consent.user_id, Consent.purpose).where(
            Consent.user_id.in_(ids),
            Consent.revoked_at.is_(None),
            Consent.purpose.in_((ConsentPurpose.blind_card.value, ConsentPurpose.fsp_enrichment.value)),
        )
    )
    for uid, purpose in rows:
        consents[uid].add(purpose)
    out = {}
    for uid in ids:
        u = users.get(uid)
        active = bool(u and u.status == "active" and u.email_verified and u.role == "candidate")
        out[uid] = AccountSnap(
            active,
            ConsentPurpose.blind_card.value in consents[uid],
            ConsentPurpose.fsp_enrichment.value in consents[uid],
        )
    return out


def load_profiles(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, CandidateProfile]:
    if not ids:
        return {}
    return {p.user_id: p for p in db.scalars(select(CandidateProfile).where(CandidateProfile.user_id.in_(ids)))}


def load_surveys(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, SurveyResponse]:
    """Последний заполненный опрос кандидата (отрасли и заявленный стек, если в профиле пусто)."""
    if not ids:
        return {}
    out: dict[uuid.UUID, SurveyResponse] = {}
    rows = db.scalars(
        select(SurveyResponse)
        .where(SurveyResponse.candidate_id.in_(ids))
        .order_by(SurveyResponse.candidate_id, SurveyResponse.updated_at.desc())
    )
    for s in rows:
        out.setdefault(s.candidate_id, s)
    return out


# --------------------------------------------------------------------------- заявленные категории


def load_claims(
    db: Session, ids: Sequence[uuid.UUID], surveys: Mapping[uuid.UUID, SurveyResponse]
) -> dict[uuid.UUID, dict[str, str]]:
    """Заявленные, но не подтверждённые тестом категории: {кандидат: {специализация: грейд}}.

    Организаторы рекомендуют НЕ скрывать кандидата с неподтверждённым грейдом, а показывать со статусом и ниже
    подтверждённых (DECISIONS п. 29). Источники, по приоритету: грейд, который кандидат сам выбрал для последней
    попытки теста по специализации; иначе специализация из опроса (chosen_spec) и подсказка грейда опроса
    (survey_responses.suggested_grade) — «категория по опросу». Резюме и самоописание не используются (CLAUDE.md)."""
    out: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    if not ids:
        return out
    specs = {s["code"] for s in load_config("taxonomy")["specializations"] if s.get("status") != "derived"}
    rows = db.execute(
        text(
            "SELECT DISTINCT ON (candidate_id, specialization) candidate_id, specialization, claimed_grade "
            "FROM test_sessions WHERE candidate_id = ANY(:ids) AND kind IN ('initial', 'retake') "
            "ORDER BY candidate_id, specialization, created_at DESC, id"
        ),
        {"ids": list(ids)},
    )
    for uid, spec, grade in rows:
        if spec in specs and grade in GRADES:
            out[uid][spec] = grade
    for uid, sv in surveys.items():
        spec, grade = sv.chosen_spec, sv.suggested_grade
        if sv.completed_at is not None and spec in specs and grade in GRADES:
            out[uid].setdefault(spec, grade)
    return out


# --------------------------------------------------------------------------- ФСП


@lru_cache(maxsize=1)
def _demo_provider() -> Any:
    from app.fsp.provider import InProcessFSPProvider
    from app.fsp.settings import FSPSettings

    return InProcessFSPProvider.from_file(FSPSettings().snapshot_path)


def _parse(model: type, payload: Any, keys: Sequence[str]) -> Any:
    cands = [payload] + [payload.get(k) for k in keys if isinstance(payload, Mapping)]
    for c in cands:
        if not isinstance(c, Mapping):
            continue
        try:
            return model.model_validate(c)
        except ValidationError:
            continue
    return None


def _profile_from_rows(sub: str, rows: Sequence[FspAchievementCache]) -> FSPProfile | None:
    results: list[NormalizedResult] = []
    ranks: list[Rank] = []
    roles: list[RoleBadge] = []
    for r in rows:
        kind = (r.kind or "").lower()
        if kind == "result":
            nr = _parse(NormalizedResult, r.payload, ("normalized", "result"))
            if nr is not None:
                results.append(nr.model_copy(update={"hidden": bool(r.hidden or nr.hidden)}))
        elif kind == "rank":
            rk = _parse(Rank, r.payload, ("rank",))
            if rk is not None:
                ranks.append(rk)
        elif kind == "role":
            rb = _parse(RoleBadge, r.payload, ("role",))
            if rb is not None:
                roles.append(rb)
    if not (results or ranks or roles):
        return None
    if any(x.confirmed for x in results) or ranks:
        state = FSPState.linked
    elif results:
        state = FSPState.awaiting_protocol
    else:
        state = FSPState.linked_no_results
    fetched = max((r.fetched_at for r in rows), default=None)
    source = results[0].source if results else None
    return FSPProfile(sub=sub, state=state, results=results, ranks=ranks, roles=roles, source=source, fetched_at=fetched)


def _profile_from_link(link: FspLink, hidden: Sequence[str]) -> FSPProfile | None:
    """Сырой ответ реестра, сохранённый при синхронизации (FspLink.achievements) → FSPProfile без сети."""
    if not link.achievements:
        return None
    from app.fsp.models import Achievements
    from app.fsp.provider import normalize_achievements
    from app.services.fsp_link.minimize import athlete_of

    try:
        ach = Achievements.model_validate(link.achievements)
        athlete = athlete_of(link.athlete)  # в привязке — только sub и год рождения: Athlete собирается лишь из старых полных строк
    except ValidationError:
        log.warning("Сохранённый ответ реестра ФСП для %s не прошёл проверку контракта", link.user_id)
        return None
    if ach.athlete_sub != link.fsp_id:
        log.warning("Сохранённые достижения ФСП принадлежат другому sub — пропущены (%s)", link.user_id)
        return None
    return normalize_achievements(ach, hidden_result_ids=hidden, fetched_at=link.last_sync_at or ach.as_of, athlete=athlete)


def load_fsp_profiles(
    db: Session, ids: Sequence[uuid.UUID], accounts: Mapping[uuid.UUID, AccountSnap], now: datetime
) -> dict[uuid.UUID, FSPProfile]:
    """Профиль ФСП кандидата (концепция §6.4–6.5). Без ФСП ID или без согласия на обогащение — «не привязан»
    (F = 0, отсутствие бонуса, а не штраф). Источники по порядку: сохранённый ответ реестра (FspLink),
    нормализованный кэш записей (FspAchievementCache), для «демо-входа ФСП» — встроенный демо-реестр.
    Скрытые кандидатом результаты (FspAchievementCache.hidden) в VSS не идут никогда."""
    out: dict[uuid.UUID, FSPProfile] = {uid: FSPProfile.not_linked() for uid in ids}
    if not ids:
        return out
    links = {
        link.user_id: link
        for link in db.scalars(select(FspLink).where(FspLink.user_id.in_(ids)))
        if accounts.get(link.user_id) and accounts[link.user_id].fsp_enrichment
    }
    if not links:
        return out
    rows: dict[uuid.UUID, list[FspAchievementCache]] = defaultdict(list)
    for a in db.scalars(
        select(FspAchievementCache)
        .where(FspAchievementCache.user_id.in_(list(links)))
        .order_by(FspAchievementCache.user_id, FspAchievementCache.external_id)
    ):
        rows[a.user_id].append(a)
    for uid, link in links.items():
        hidden = [r.external_id for r in rows.get(uid, ()) if r.hidden]
        prof = _profile_from_link(link, hidden) or _profile_from_rows(link.fsp_id, rows.get(uid, ()))
        if prof is None and link.is_demo:
            # «Демо-вход ФСП» без сохранённой синхронизации — встроенный демо-реестр, без сети.
            try:
                from app.fsp.provider import load_profile

                prof = load_profile(_demo_provider(), link.fsp_id, now=now, hidden_result_ids=hidden)
            except Exception:  # noqa: BLE001 — демо-реестр не должен ронять подборку
                log.exception("Не удалось загрузить демо-профиль ФСП для %s", uid)
        if prof is None:
            prof = FSPProfile(sub=link.fsp_id, state=FSPState.linked_no_results, fetched_at=link.last_sync_at or now)
        out[uid] = prof
    return out


# --------------------------------------------------------------------------- регулярные задачи


@dataclass(frozen=True)
class TaskSnap:
    task_id: str  # id назначения (внутренний; работодателю — только непрозрачная ссылка)
    kind: str  # A | B
    q: float  # качество 0..1
    done_at: datetime
    title: str
    skills: tuple[str, ...]
    module: str | None


@lru_cache(maxsize=1)
def _registry_modules() -> dict[str, str]:
    try:
        from app.testing.bank.registry import all_families

        return {fid: fam.module for fid, fam in all_families().items()}
    except Exception:  # noqa: BLE001 — банк может быть недоступен; модуль задачи тогда неизвестен
        return {}


def _quality(score: float, kind: str) -> float:
    """Нормировка результата задачи к 0..1: тип B — сумма рубрики (4 критерия × 0–2), тип A — доля верного."""
    if score <= 1.0:
        return max(0.0, score)
    if kind == "B":
        rub = get_contact_config().tasks.rubric
        total = rub.max_points * len(rub.criteria)
        return min(1.0, score / total) if total else 0.0
    return min(1.0, score / 100.0)


def load_tasks(db: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[TaskSnap]]:
    out: dict[uuid.UUID, list[TaskSnap]] = defaultdict(list)
    if not ids:
        return out
    rows = db.execute(
        select(TaskAssignment, EmployerTask)
        .join(EmployerTask, EmployerTask.id == TaskAssignment.employer_task_id)
        .where(
            TaskAssignment.candidate_id.in_(ids),
            TaskAssignment.status.in_(SCORED_TASK_STATUSES),
            TaskAssignment.score.is_not(None),
        )
        .order_by(TaskAssignment.candidate_id, TaskAssignment.id)
    ).all()
    families = {a.family_id or t.family_id for a, t in rows if (a.family_id or t.family_id)}
    fam_module: dict[str, str] = {}
    if families:
        for fid, mod in db.execute(
            select(ItemFamilyParams.family_id, ItemFamilyParams.module).where(
                ItemFamilyParams.family_id.in_(families)
            )
        ):
            fam_module.setdefault(fid, mod)
    for a, t in rows:
        kind = "A" if (a.kind or t.kind) == TaskKind.catalog.value else "B"
        done = a.finished_at or a.reviewed_at or a.submitted_at or a.created_at
        family = a.family_id or t.family_id
        module = a.module or t.module
        if module is None and family:
            module = fam_module.get(family) or _registry_modules().get(family)
        out[a.candidate_id].append(
            TaskSnap(
                task_id=str(a.id),
                kind=kind,
                q=_quality(float(a.score), kind),
                done_at=done,
                title=t.title,
                skills=tuple(str(s) for s in (t.tags or ())),
                module=module,
            )
        )
    return out


# --------------------------------------------------------------------------- отпечаток источников

# Отпечаток всего, от чего зависят признаки: смена любого значения → строки кэша пересобираются.
# Берём сами значения, а не updated_at: при замороженных часах (тесты, DEMO_MODE) отметка времени правки
# не меняется. format('%s') сохраняет позиции полей (NULL → пустая строка), поэтому сдвигов нет.
# Время-зависимые части (дни с теста, свежесть задач) считаются при чтении, их в отпечатке нет.
#
# По запросу на таблицу-источник и сборка в Python. Проверка свежести перед подборкой читает таблицы целиком
# (последовательный проход + группировка — линейно и не зависит от статистики планировщика: одно большое
# соединение CTE при устаревшей статистике уходило во вложенные циклы — секунды вместо миллисекунд); пересборка
# конкретных кандидатов — с фильтром {key} = ANY(:ids).
_FP_PARTS: tuple[tuple[str, str], ...] = (
    ("id", "SELECT id, format('%s|%s|%s', status, email_verified, role) FROM users WHERE {where}"),
    (
        "user_id",
        """SELECT user_id, format('%s;%s;%s;%s;%s;%s;%s;%s;%s;%s;%s;%s;%s', search_mode, salary_min, salary_type,
                  salary_visibility, formats, region_code, city, relocation, blocked_company_ids, industries,
                  declared_skills, birth_year, is_minor)
           FROM candidate_profiles WHERE {where}""",
    ),
    (
        "candidate_id",
        """SELECT candidate_id, string_agg(format('%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s:%s', specialization, status,
                  grade, theta, se, trust, borderline, under_review, confirmed_at, last_activity_at,
                  source_session_id, md5(subscores::text), md5(module_tags::text)), ',' ORDER BY specialization)
           FROM qualifications WHERE {where} GROUP BY candidate_id""",
    ),
    (
        "user_id",
        """SELECT user_id, string_agg(format('%s:%s', id, revoked_at), ',' ORDER BY id)
           FROM consents WHERE {where} AND purpose IN ('blind_card', 'fsp_enrichment') GROUP BY user_id""",
    ),
    (
        "user_id",
        """SELECT user_id, format('%s:%s:%s:%s:%s', fsp_id, last_sync_at, is_demo, fsp_state,
                  md5(coalesce(achievements::text, '')))
           FROM fsp_links WHERE {where}""",
    ),
    (
        "user_id",
        """SELECT user_id, string_agg(format('%s:%s:%s:%s', id, hidden, status, md5(payload::text)), ',' ORDER BY id)
           FROM fsp_achievements WHERE {where} GROUP BY user_id""",
    ),
    (
        "candidate_id",
        """SELECT candidate_id, string_agg(format('%s:%s:%s:%s:%s', id, status, score, finished_at, reviewed_at),
                  ',' ORDER BY id)
           FROM task_assignments WHERE {where} GROUP BY candidate_id""",
    ),
    (
        "candidate_id",
        """SELECT DISTINCT ON (candidate_id) candidate_id, format('%s;%s;%s;%s;%s', industries,
                  md5(stack_declared::text), chosen_spec, suggested_grade, completed_at IS NOT NULL)
           FROM survey_responses WHERE {where} ORDER BY candidate_id, updated_at DESC, id""",
    ),
    (
        "candidate_id",
        # Заявленные категории без подтверждения берут грейд из последней попытки теста (DECISIONS п. 29)
        """SELECT candidate_id, string_agg(DISTINCT format('%s:%s', specialization, claimed_grade), ',')
           FROM test_sessions WHERE {where} AND kind IN ('initial', 'retake') GROUP BY candidate_id""",
    ),
)
_FP_BY_IDS = tuple(text(q.format(where=f"{key} = ANY(:ids)")) for key, q in _FP_PARTS)
_FP_ALL = tuple(text(q.format(where="TRUE")) for _, q in _FP_PARTS)


def _combine(ids: Iterable[uuid.UUID], parts: Sequence[Mapping[uuid.UUID, str]]) -> dict[uuid.UUID, str]:
    users = parts[0]
    out = {}
    for uid in ids:
        if uid not in users:
            continue
        joined = "|".join(p.get(uid) or "" for p in parts)
        out[uid] = hashlib.md5(joined.encode(), usedforsecurity=False).hexdigest()
    return out


def fingerprints(db: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """md5 значений всех источников признаков кандидата (только существующие пользователи)."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    return _combine(ids, [dict(db.execute(q, {"ids": ids}).all()) for q in _FP_BY_IDS])


_SCOPE_SQL = text(
    "SELECT candidate_id FROM qualifications WHERE status IN ('CONFIRMED', 'UPGRADE_TESTING') "
    "UNION SELECT candidate_id FROM survey_responses WHERE completed_at IS NOT NULL AND chosen_spec IS NOT NULL "
    "UNION SELECT candidate_id FROM matching_features"
)
_CURRENT_SQL = text(
    "SELECT candidate_id, min(fingerprint), max(fingerprint), bool_or(fingerprint IS NULL), min(computed_at) "
    "FROM matching_features GROUP BY candidate_id"
)


def stale_candidates(db: Session, cutoff: datetime) -> dict[uuid.UUID, str]:
    """Кандидаты, чей кэш признаков устарел: нет строк, помечен хуком, отпечаток изменился или старше cutoff."""
    scope = list(db.scalars(_SCOPE_SQL))
    if not scope:
        return {}
    current = {row[0]: row[1:] for row in db.execute(_CURRENT_SQL)}
    out = {}
    for uid, fp in _combine(scope, [dict(db.execute(q).all()) for q in _FP_ALL]).items():
        cur = current.get(uid)
        if cur is None:
            out[uid] = fp
            continue
        fp_min, fp_max, has_null, computed = cur
        if has_null or fp_min != fp or fp_max != fp or computed < cutoff:
            out[uid] = fp
    return out
