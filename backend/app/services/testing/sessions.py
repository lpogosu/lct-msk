"""Тестовые сессии: создание, выдача задания, ответ, события, доборы, таймеры (spec-testing §2.2–2.5, §3.4–3.5).

Все решения принимает доменная машина состояний app.testing.session.TestSession; здесь — загрузка и
сохранение её полного снимка (jsonb), выдача экземпляра (FOR UPDATE SKIP LOCKED), строки TestResponse
с причиной выбора, детекторы и применение итогов к квалификации. Каждое изменяющее действие берёт
блокировку строки сессии (SELECT … FOR UPDATE): параллельные ответы/next одной сессии сериализуются.

Время — только app.core.clock.clock (тесты замораживают, DEMO_MODE сдвигает). Случайность — только
random.Random(f"{seed сессии}:{позиция}:{попытка}"): трасса воспроизводима по seed из строки сессии.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.consents import grant_consent, has_active_consent
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.models.enums import ACTIVE_SESSION_STATUSES, EXCLUSIVE_SESSION_KINDS, ConsentPurpose
from app.models.testing import CalibrationRun, ItemInstance, TestResponse
from app.models.testing import Qualification as QualificationRow
from app.models.testing import SessionEvent as SessionEventRow
from app.models.testing import TestSession as SessionRow
from app.models.users import User
from app.services.testing import anticheat_hooks
from app.services.testing.catalog import family_metas, seen_history
from app.services.testing.integrity import (
    behavioral_flags,
    chain_session_ids,
    check_foreign_key,
    flag_from_row,
    maybe_quarantine_family,
    persist_flag,
    session_flag_rows,
)
from app.services.testing.keystore import decrypt_key
from app.services.testing.labels import spec_info, spec_name, spec_testable
from app.services.testing.pool import issue_instance
from app.services.testing.qualification import get_or_create_row, get_row, load_domain, save_domain
from app.services.testing.snapshot import session_from_state, session_to_state
from app.testing.bank.base import Instance, Option, Rendered
from app.testing.bank.registry import all_families
from app.testing.cat import (
    KIND_EXT_DOWN,
    KIND_EXT_UP,
    KIND_UPGRADE,
    TAG_CONFIRMED,
    TAG_STRONG,
    load_cat_config,
    module_subscores,
)
from app.testing.detectors import HARD, SOFT, Flag, SessionEvent, trust_from_flags
from app.testing.irt import load_scale
from app.testing.session import (
    CLOSED_STATUSES,
    STATUS_LABELS_RU,
    QualStatus,
    SessionError,
    SessionStatus,
    TestSession,
)

log = logging.getLogger("vzaimno.testing.sessions")

OPEN_STATUSES = (SessionStatus.CREATED, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED)
# Статусы, при которых сессию ещё могут сдвинуть таймеры (обрыв > 30 мин, предложение добора > 24 ч)
TICKABLE = tuple(str(s) for s in (*OPEN_STATUSES, SessionStatus.DECIDED, SessionStatus.EXTENSION_OFFERED))


def session_error(e: SessionError) -> ApiError:
    """Ошибка правил домена → 409 с машинным кодом и сроком, когда станет доступно."""
    details: dict[str, Any] = {}
    if e.available_at is not None:
        details["available_at"] = e.available_at.isoformat()
    return conflict(e.code, e.message_ru, details or None)


def seed_of(session_id: uuid.UUID) -> int:
    """Seed выбора заданий — из id сессии (пишется в строку сессии, трасса воспроизводима)."""
    return int(session_id.hex[:15], 16)


def rng_for(row: SessionRow, *parts: Any) -> random.Random:
    return random.Random(":".join([str(row.seed), *map(str, parts)]))


def rendered_from_public(d: dict[str, Any]) -> Rendered:
    """Обратное к Rendered.to_public() — нужно check() некоторых семейств (упорядочивание по индексам)."""
    return Rendered(
        prompt=d.get("prompt", ""),
        code=d.get("code"),
        language=d.get("language"),
        options=tuple(Option(id=o["id"], text=o["text"]) for o in d.get("options") or ()),
        lines=tuple(d.get("lines") or ()),
        table=d.get("table"),
        answer_mode=d.get("answer_mode", "choice"),
        answer_hint=d.get("answer_hint"),
    )


def instance_of(row: ItemInstance) -> Instance:
    """Экземпляр банка из строки пула (ключ расшифровывается — только на сервере)."""
    return Instance(
        family_id=row.family_id,
        seed=int(row.seed),
        rendered=rendered_from_public(row.rendered or {}),
        key=decrypt_key(row.key_enc),
        distractors=dict(row.distractors or {}),
        explanation=row.explanation or "",
    )


# ---------------------------------------------------------------------------
# Загрузка и сохранение
# ---------------------------------------------------------------------------


@dataclass
class Loaded:
    row: SessionRow
    sess: TestSession
    n_flags: int
    svc: dict[str, Any] = field(default_factory=dict)  # служебное: детекторы уже прогнаны и т. п.
    flag_details: dict[int, dict[str, Any]] = field(default_factory=dict)  # индекс флага → доп. details


def _get_owned(db: Session, session_id: uuid.UUID | str, user: User, *, lock: bool) -> SessionRow:
    try:
        sid = session_id if isinstance(session_id, uuid.UUID) else uuid.UUID(str(session_id))
    except ValueError:
        raise not_found("session_not_found", "Сессия не найдена") from None
    q = select(SessionRow).where(SessionRow.id == sid)
    if lock:
        q = q.with_for_update()
    row = db.scalars(q).first()
    # Чужая сессия неотличима от несуществующей (IDOR)
    if row is None or row.candidate_id != user.id:
        raise not_found("session_not_found", "Сессия не найдена")
    return row


def wrap(db: Session, row: SessionRow) -> Loaded:
    if not row.state:
        raise conflict("session_state_missing", "Состояние теста не сохранено — начните тест заново")
    flags = [flag_from_row(f) for f in session_flag_rows(db, row.id)]
    state = row.state or {}
    return Loaded(row=row, sess=session_from_state(state, flags), n_flags=len(flags), svc=dict(state.get("svc") or {}))


def load(db: Session, session_id: uuid.UUID | str, user: User, *, lock: bool = True) -> Loaded:
    return wrap(db, _get_owned(db, session_id, user, lock=lock))


def save(db: Session, ld: Loaded) -> None:
    sess, row = ld.sess, ld.row
    now = clock.now()
    for i, flag in enumerate(sess.flags[ld.n_flags :], start=ld.n_flags):
        persist_flag(db, row.id, flag, now, ld.flag_details.get(i))
        if flag.kind == "foreign_key" and flag.family_id:
            maybe_quarantine_family(db, flag.family_id, now)
    ld.n_flags = len(sess.flags)
    ld.flag_details.clear()
    state = session_to_state(sess)
    state["svc"] = ld.svc
    row.state = state
    row.status = str(sess.status)
    row.started_at = sess.started_at
    row.paused_at = sess.paused_at
    row.last_activity_at = sess.last_activity_at
    row.finished_at = sess.finished_at
    row.trust = sess.trust
    row.n_items = len(sess.cat.responses)
    scale = load_scale()
    row.target_cut = scale.cut(sess.cat.target_grade)
    d = sess.decision
    if d is not None:
        row.decision = d.code
        row.theta, row.se, row.p_pass = d.theta, d.se, d.p_pass
        row.class_probs = {k: round(v, 4) for k, v in sess.cat.posterior(scale).class_probs().items()}
    db.flush()


def chain_flags(db: Session, ld: Loaded) -> list[Flag]:
    """Флаги предков в цепочке доборов (для «не дублировать» в детекторах)."""
    ids = chain_session_ids(db, ld.row)[1:]
    out: list[Flag] = []
    for sid in ids:
        out.extend(flag_from_row(f) for f in session_flag_rows(db, sid))
    return out


# ---------------------------------------------------------------------------
# Точки anti-assist (anticheat_hooks): вызов с изоляцией сбоев, сигналы → флаги
# ---------------------------------------------------------------------------


def call_hook(name: str, *args: Any, **kwargs: Any) -> list[anticheat_hooks.AntiAssistSignal]:
    """Хук отдельной функции не должен ронять тест кандидата: исключение → журнал, сигналов нет."""
    try:
        return list(getattr(anticheat_hooks, name)(*args, **kwargs) or [])
    except Exception:  # noqa: BLE001 — сбой расширения не мешает ответить на задание
        log.exception("Хук anti-assist %s завершился ошибкой — сигналы пропущены", name)
        return []


def add_signals(ld: Loaded, signals: list[anticheat_hooks.AntiAssistSignal]) -> None:
    """Сигналы хуков → флаги сессии (учитываются в доверии при закрытии, как остальные детекторы)."""
    for sig in signals:
        severity = HARD if sig.severity == HARD else SOFT
        ld.sess.add_flags(
            [Flag(kind=str(sig.kind)[:16], severity=severity, message_ru=str(sig.message_ru), value=sig.value)]
        )
        if sig.details:
            ld.flag_details[len(ld.sess.flags) - 1] = {"signal": dict(sig.details)}


# ---------------------------------------------------------------------------
# Квалификация: применение итогов
# ---------------------------------------------------------------------------


def needs_review(ld: Loaded) -> bool:
    """Флаги сессии уже требуют проверки методистом: «чужой ключ» или ≥ 3 мягких (§3.10)."""
    return trust_from_flags(ld.sess.flags).under_review


def apply_outcome(db: Session, ld: Loaded) -> None:
    """Итог сессии → квалификация (домен: «вызывать при решении и при закрытии; повтор безопасен»).

    Решённая сессия с предложением добора закрывается только через ≤ 24 ч. Если её флаги уже требуют
    проверки, кандидат скрывается из витрины сразу (concept §5.2: «чужой ключ» → сессия на проверку), а не
    после закрытия; предложение добора при этом остаётся (решение методиста — после закрытия сессии)."""
    qrow = get_or_create_row(db, ld.row.candidate_id, ld.row.specialization, lock=True)
    q = load_domain(qrow)
    outcome = ld.sess.outcome()
    if ld.sess.status == SessionStatus.EXTENSION_OFFERED and needs_review(ld):
        outcome = dataclasses.replace(outcome, under_review=True)
    q.apply(outcome, clock)
    save_domain(db, qrow, q)


def finalize(db: Session, ld: Loaded) -> None:
    """После решения: поведенческие детекторы (один раз), закрытие решённой без предложений сессии,
    применение итога к квалификации. Для ABANDONED/FINAL/UNDER_REVIEW — только применение итога."""
    sess = ld.sess
    if sess.status in (SessionStatus.DECIDED, SessionStatus.EXTENSION_OFFERED) and not ld.svc.get("detectors_done"):
        chain_ids = chain_session_ids(db, ld.row)
        flags = behavioral_flags(
            db,
            ld.row,
            sess,
            end=sess.decided_at or clock.now(),
            chain_ids=chain_ids,
            already=[*chain_flags(db, ld), *sess.flags],
        )
        sess.add_flags(flags)
        ld.svc["detectors_done"] = True
    if sess.status == SessionStatus.DECIDED:
        sess.close(clock)
    if sess.status not in OPEN_STATUSES:
        save(db, ld)
        apply_outcome(db, ld)


def enforce_shown_cap(ld: Loaded) -> None:
    """DECISIONS п. 17: показанных заданий ≤ 20 вместе с pretest. Движок считает только зачётные,
    поэтому в основной части стоп по числу показанных делает сервис (решение — тем же decide())."""
    sess = ld.sess
    cfg = load_cat_config()
    if (
        sess.status == SessionStatus.IN_PROGRESS
        and sess.pending is None
        and sess.cat.phase == 0
        and len(sess.cat.responses) >= cfg.n_max
    ):
        sess._decide("max_items", clock)  # noqa: SLF001 — тот же путь решения, что у стоп-правила движка


def after_change(db: Session, ld: Loaded, before: SessionStatus) -> None:
    """Сессия вышла из открытого состояния за этот запрос → детекторы, закрытие, итог в квалификацию."""
    if ld.sess.status != before and ld.sess.status not in OPEN_STATUSES:
        finalize(db, ld)


def tick(db: Session, ld: Loaded) -> bool:
    """Таймеры домена. True — статус изменился (итог применён к квалификации)."""
    before = ld.sess.status
    ld.sess.tick(clock)
    if ld.sess.status != before:
        finalize(db, ld)
        return True
    return False


def refresh_user_sessions(db: Session, user_id: uuid.UUID) -> None:
    """Сдвинуть таймеры всех незакрытых сессий кандидата (обрыв > 30 мин → ABANDONED, добор > 24 ч → FINAL).
    Вызывается на чтении квалификации и перед созданием новой сессии."""
    rows = db.scalars(
        select(SessionRow)
        .where(SessionRow.candidate_id == user_id, SessionRow.status.in_(TICKABLE))
        .order_by(SessionRow.created_at)
        .with_for_update()
    ).all()
    for row in rows:
        if not row.state:
            continue  # строка без снимка движка (не из API теста) — таймерам нечего сдвигать
        ld = wrap(db, row)
        tick(db, ld)


# ---------------------------------------------------------------------------
# Создание
# ---------------------------------------------------------------------------


def bank_params_version(db: Session) -> int:
    return 1 + int(db.scalar(select(func.count()).select_from(CalibrationRun)) or 0)


def active_session(db: Session, user_id: uuid.UUID) -> SessionRow | None:
    return db.scalars(
        select(SessionRow).where(
            SessionRow.candidate_id == user_id,
            SessionRow.status.in_(ACTIVE_SESSION_STATUSES),
            SessionRow.kind.in_(EXCLUSIVE_SESSION_KINDS),
        )
    ).first()


def create_session(
    db: Session,
    user: User,
    *,
    spec: str,
    grade: str | None,
    upgrade: bool,
    consent_test_logging: bool,
    ip: str | None = None,
    user_agent: str | None = None,
) -> Loaded:
    info = spec_info(spec)
    if info is None:
        raise bad_request("unknown_specialization", "Неизвестная специализация")
    if info.get("status") == "derived":
        raise bad_request(
            "derived_specialization",
            f"Отдельного теста «{info['name']}» нет: категория присваивается, когда подтверждены обе квалификации",
        )
    if not spec_testable(spec):
        raise conflict(
            "specialization_not_testable",
            "Тест по этому направлению появится позже. Вы в листе ожидания",
            {"specialization": spec},
        )
    scale = load_scale()
    if not upgrade and grade not in scale.grades:
        raise bad_request("grade_required", "Выберите грейд: Стажёр, Junior, Middle или Senior")

    refresh_user_sessions(db, user.id)
    current = active_session(db, user.id)
    if current is not None:
        raise conflict(
            "active_session_exists",
            "У вас уже идёт тест — продолжите его",
            {"session_id": str(current.id)},
        )

    offered = db.scalars(
        select(SessionRow.id).where(
            SessionRow.candidate_id == user.id,
            SessionRow.specialization == spec,
            SessionRow.status == SessionStatus.EXTENSION_OFFERED.value,
        )
    ).first()
    if offered is not None:
        raise conflict(
            "extension_pending",
            "Сначала ответьте на предложение добора по прошлому тесту",
            {"session_id": str(offered)},
        )

    qrow = get_row(db, user.id, spec, lock=True)
    if qrow is None:
        raise conflict("survey_required", "Сначала пройдите опрос — это около 3 минут")
    q = load_domain(qrow)
    session_id = uuid.uuid4()
    base_grade: str | None = None
    try:
        if upgrade:
            target_next = scale.next_grade(q.grade) if q.grade else None
            if grade is not None and target_next is not None and grade != target_next:
                raise bad_request(
                    "upgrade_one_step",
                    f"Тест на повышение — только на одну ступень: {scale.grade_name(target_next)}",
                )
            base_grade = q.grade
            kind, claimed = KIND_UPGRADE, q.start_upgrade(str(session_id), clock)
        else:
            kind = q.start_test(str(session_id), clock)
            claimed = str(grade)
    except SessionError as e:
        raise session_error(e) from e

    # Отдельное согласие на журналирование событий теста (К3) — без него тест не начать. Записывается
    # последним, когда все правила старта пройдены: отказ в старте не оставляет согласия в журнале
    if not has_active_consent(db, user.id, ConsentPurpose.test_logging):
        if not consent_test_logging:
            raise conflict(
                "consent_required",
                "Чтобы начать тест, отметьте согласие на журналирование событий теста",
                {"purpose": ConsentPurpose.test_logging.value},
            )
        grant_consent(db, user.id, ConsentPurpose.test_logging, ip=ip, user_agent=user_agent)

    row = SessionRow(
        id=session_id,
        candidate_id=user.id,
        specialization=spec,
        kind=kind,
        claimed_grade=claimed,
        seed=seed_of(session_id),
        status=SessionStatus.CREATED.value,
        logging_consent=True,
        params_version=bank_params_version(db),
        state={},
    )
    sess = TestSession.create(
        session_id=str(session_id),
        account_id=str(user.id),
        spec=spec,
        claimed_grade=claimed,
        clock=clock,
        rng=rng_for(row, "create"),
        kind=kind,
        base_grade=base_grade,
    )
    sess.start(clock)
    ld = Loaded(row=row, sess=sess, n_flags=0)
    db.add(row)
    try:
        save(db, ld)
    except IntegrityError as e:  # гонка двух вкладок: уникальный индекс «одна активная сессия»
        raise conflict("active_session_exists", "У вас уже идёт тест — продолжите его") from e
    save_domain(db, qrow, q)
    return ld


# ---------------------------------------------------------------------------
# Выдача задания
# ---------------------------------------------------------------------------


@dataclass
class IssuedItem:
    response: TestResponse
    instance: ItemInstance
    fresh: bool = False  # выдано этим запросом (а не повтор GET того же задания)


def pending_item(db: Session, ld: Loaded) -> IssuedItem | None:
    if ld.sess.pending is None:
        return None
    resp = db.scalars(
        select(TestResponse).where(TestResponse.session_id == ld.row.id, TestResponse.seq == ld.sess.pending.seq)
    ).first()
    if resp is None:
        return None
    inst = db.get(ItemInstance, resp.instance_id)
    return IssuedItem(resp, inst) if inst is not None else None


def _record_response(resp: TestResponse, rec: Any, now: datetime, answer: Any, tag: str | None) -> None:
    resp.answered_at = now
    resp.rt_ms = rec.rt_ms
    resp.answer = answer
    resp.is_correct = bool(rec.correct)
    resp.distractor_tag = (tag or None) and str(tag)[:64]
    resp.theta_after = rec.theta_after
    resp.se_after = rec.se_after
    resp.p_pass_after = rec.p_after


def _timeout_pending(db: Session, ld: Loaded) -> None:
    """Серверный дедлайн прошёл без ответа: таймаут = неверно (§2.5)."""
    item = pending_item(db, ld)
    sel = ld.sess.pending
    assert sel is not None
    rec = ld.sess.answer(sel.seq, False, clock, instance_id=str(item.instance.id) if item else None)
    if item is not None:
        _record_response(item.response, rec, clock.now(), None, None)


def _select_and_issue(db: Session, ld: Loaded, user: User) -> IssuedItem | None:
    existing = pending_item(db, ld)
    if existing is not None:
        return existing
    sess, row = ld.sess, ld.row
    now = clock.now()
    metas = family_metas(db, sess.spec, now)
    history = seen_history(db, user.id, now)
    for attempt in range(3):
        sel = sess.next_item(metas, rng_for(row, sess.cat.next_seq, attempt), clock, seen_history=history)
        if sel is None:
            return None  # банк исчерпан — движок уже принял решение
        inst = issue_instance(db, sel.family_id, session_id=row.id, user_id=user.id, now=now)
        if inst is not None:
            resp = TestResponse(
                session_id=row.id,
                seq=sel.seq,
                instance_id=inst.id,
                family_id=sel.family_id,
                module=sel.module,
                is_pretest=sel.is_pretest,
                shown_at=sess.pending_shown_at or now,
                deadline_at=sess.deadline(),
                selection_reason=dict(sel.reason),
            )
            db.add(resp)
            db.flush()
            return IssuedItem(resp, inst, fresh=True)
        # Свободные экземпляры кончились между подсчётом и выдачей (параллельные сессии) — выбрать заново
        sess.pending = None
        sess.pending_shown_at = None
        metas = [dataclasses.replace(m, free_instances=0) if m.id == sel.family_id else m for m in metas]
    sess._decide("bank_exhausted", clock)  # noqa: SLF001
    return None


def next_item(db: Session, user: User, session_id: uuid.UUID | str) -> tuple[Loaded, IssuedItem | None]:
    """GET next: идемпотентно — пока на задание не ответили, возвращается оно же (с тем же дедлайном)."""
    ld = load(db, session_id, user)
    tick(db, ld)
    sess = ld.sess
    before = sess.status
    item: IssuedItem | None = None
    if sess.status == SessionStatus.IN_PROGRESS:
        dl = sess.deadline()
        if sess.pending is not None and dl is not None and clock.now() > dl:
            _timeout_pending(db, ld)
        enforce_shown_cap(ld)
    if sess.status == SessionStatus.IN_PROGRESS:
        item = _select_and_issue(db, ld, user)
        if item is not None and item.fresh:
            # Рендер задания — один раз, при выдаче: повторный GET отдаёт сохранённый вариант
            original = item.instance.rendered
            try:
                anticheat_hooks.clipboard_variant(item.instance)
            except Exception:  # noqa: BLE001 — сбой расширения: показываем задание как есть
                log.exception("Хук anti-assist clipboard_variant завершился ошибкой — задание без изменений")
                item.instance.rendered = original
            db.flush()
    after_change(db, ld, before)
    save(db, ld)
    return ld, item


# ---------------------------------------------------------------------------
# Ответ
# ---------------------------------------------------------------------------


@dataclass
class AnswerResult:
    ld: Loaded
    seq: int
    duplicate: bool
    timed_out: bool


def _answer_json(answer: Any) -> Any:
    if answer is None:
        return None
    if len(json.dumps(answer, ensure_ascii=False)) > 5000:
        raise bad_request("answer_too_long", "Слишком длинный ответ")
    return answer


def submit_answer(db: Session, user: User, session_id: uuid.UUID | str, seq: int, answer: Any) -> AnswerResult:
    """POST answer: идемпотентно по seq. Повтор возвращает уже записанный ответ, второй ответ не меняет первый."""
    ld = load(db, session_id, user)
    sess, row = ld.sess, ld.row
    resp = db.scalars(select(TestResponse).where(TestResponse.session_id == row.id, TestResponse.seq == seq)).first()
    if (resp is not None and resp.answered_at is not None) or any(r.seq == seq for r in sess.cat.responses):
        timed = bool(resp and resp.deadline_at and resp.answered_at and resp.answered_at > resp.deadline_at)
        return AnswerResult(ld, seq, True, timed)
    answer = _answer_json(answer)
    tick(db, ld)
    before = sess.status
    if sess.status != SessionStatus.IN_PROGRESS:
        save(db, ld)
        raise conflict("session_not_in_progress", f"Ответ не принят: тест {STATUS_LABELS_RU[sess.status]}")
    if sess.pending is None or sess.pending.seq != seq or resp is None:
        raise conflict("no_pending_item", "Это задание сейчас не выдано: запросите текущее задание")
    inst_row = db.get(ItemInstance, resp.instance_id)
    if inst_row is None:  # pragma: no cover — FK RESTRICT
        raise conflict("no_pending_item", "Это задание сейчас не выдано")
    fam = all_families().get(inst_row.family_id)
    inst = instance_of(inst_row)
    correct, tag = False, None
    normalized: Any = None
    if answer is not None and fam is not None:
        try:
            correct, tag = fam.check(inst, answer)
            normalized = fam.normalize(answer)
        except Exception:  # noqa: BLE001 — ответ необычной формы считается неверным, а не ошибкой сервера
            correct, tag, normalized = False, None, None
    now = clock.now()
    dl = sess.deadline()
    timed_out = dl is not None and now > dl
    rec = sess.answer(seq, correct, clock, instance_id=str(inst_row.id))
    _record_response(resp, rec, now, answer, tag)
    if answer is not None and not correct and normalized is not None:
        flag = check_foreign_key(
            db, family_id=inst_row.family_id, normalized_answer=normalized, own_key_hash=inst_row.key_hash, user_id=user.id
        )
        if flag is not None:
            sess.add_flags([flag])
    add_signals(ld, call_hook("on_answer", sess, inst_row, answer, normalized=normalized, correct=rec.correct))
    enforce_shown_cap(ld)
    after_change(db, ld, before)
    save(db, ld)
    return AnswerResult(ld, seq, False, timed_out)


# ---------------------------------------------------------------------------
# События клиента
# ---------------------------------------------------------------------------


@dataclass
class EventsResult:
    accepted: int
    ignored: int
    status: str


def add_events(db: Session, user: User, session_id: uuid.UUID | str, events: list[dict[str, Any]]) -> EventsResult:
    """Журнал событий фронта (только с действующим согласием test_logging и только для открытой сессии).
    Время события — клиентское, если оно в пределах сессии (пачка может прийти с задержкой), иначе серверное."""
    ld = load(db, session_id, user)
    sess, row = ld.sess, ld.row
    # Сначала таймеры: в брошенную (> 30 мин тишины) сессию события не пишутся и её не «оживляют»
    tick(db, ld)
    if not sess.is_open or not has_active_consent(db, user.id, ConsentPurpose.test_logging):
        save(db, ld)
        return EventsResult(0, len(events), str(sess.status))
    now = clock.now()
    lo = (sess.started_at or sess.created_at) - timedelta(seconds=5)
    accepted = 0
    for e in events:
        at = e.get("at")
        if isinstance(at, datetime) and at.tzinfo is None:
            at = at.replace(tzinfo=UTC)  # время без пояса считаем UTC (иначе сравнение с aware падает)
        ts = at if isinstance(at, datetime) and lo <= at <= now + timedelta(seconds=5) else now
        payload = dict(e.get("payload") or {})
        if sess.pending is not None:
            payload.setdefault("seq", sess.pending.seq)
        db.add(SessionEventRow(session_id=row.id, ts=ts, type=e["type"], payload=payload))
        accepted += 1
        add_signals(ld, call_hook("on_client_event", sess, SessionEvent(ts=ts, type=e["type"], payload=payload)))
    sess.last_activity_at = now if sess.status == SessionStatus.IN_PROGRESS else sess.last_activity_at
    save(db, ld)
    return EventsResult(accepted, len(events) - accepted, str(sess.status))


# ---------------------------------------------------------------------------
# Доборы
# ---------------------------------------------------------------------------


def resolve_extension(
    db: Session, user: User, session_id: uuid.UUID | str, *, direction: str | None, accept: bool
) -> tuple[Loaded, Loaded | None]:
    """Принять добор (дочерняя сессия, тот же апостериор) или отказаться (сессия закрывается)."""
    ld = load(db, session_id, user)
    tick(db, ld)
    sess = ld.sess
    if sess.status != SessionStatus.EXTENSION_OFFERED:
        save(db, ld)
        raise conflict("no_extension_offer", f"Добор недоступен: тест {STATUS_LABELS_RU[sess.status]}")
    if not accept:
        try:
            sess.decline_extension(clock)
        except SessionError as e:
            raise session_error(e) from e
        finalize(db, ld)
        return ld, None
    if direction not in ("up", "down"):
        raise bad_request("direction_required", "Укажите направление добора: вверх или вниз")
    child_id = uuid.uuid4()
    try:
        child = sess.accept_extension(direction, clock, child_id=str(child_id))
    except SessionError as e:
        save(db, ld)
        if e.code == "offer_expired":
            finalize(db, ld)
            db.flush()
        raise session_error(e) from e
    finalize(db, ld)  # родитель закрыт (FINAL | UNDER_REVIEW) — итог в квалификацию
    scale = load_scale()
    crow = SessionRow(
        id=child_id,
        candidate_id=user.id,
        specialization=ld.row.specialization,
        kind=KIND_EXT_UP if direction == "up" else KIND_EXT_DOWN,
        parent_session_id=ld.row.id,
        claimed_grade=child.claimed_grade,
        target_cut=scale.cut(child.cat.target_grade),
        seed=seed_of(child_id),
        status=str(child.status),
        logging_consent=ld.row.logging_consent,
        params_version=ld.row.params_version,
        state={},
    )
    db.add(crow)
    cld = Loaded(row=crow, sess=child, n_flags=0)
    try:
        save(db, cld)
    except IntegrityError as e:
        raise conflict("active_session_exists", "У вас уже идёт тест — продолжите его") from e
    if child.status not in OPEN_STATUSES:  # «подтвердить одним нажатием» — добор на 0 заданий
        finalize(db, cld)
    return ld, cld


# ---------------------------------------------------------------------------
# Добровольное понижение
# ---------------------------------------------------------------------------


def voluntary_downgrade(db: Session, user: User, spec: str) -> QualificationRow:
    qrow = get_row(db, user.id, spec, lock=True)
    if qrow is None:
        raise not_found("qualification_not_found", "Квалификация по этой специализации не найдена")
    q = load_domain(qrow)
    if q.status != QualStatus.CONFIRMED:
        raise conflict("not_confirmed", "Понизить можно только подтверждённый грейд")
    try:
        lower = q.voluntary_downgrade(clock)
    except SessionError as e:
        raise session_error(e) from e
    _retag_for_grade(db, q, lower)
    save_domain(db, qrow, q)
    return qrow


def _retag_for_grade(db: Session, q: Any, grade: str) -> None:
    """После добровольного понижения уверенность и теги ✓/✓✓ считаются для НОВОГО грейда по тому же
    апостериору сессии, на которой держится грейд (build-reviews, cat: иначе они занижены)."""
    if not q.source_session_id:
        return
    try:
        row = db.get(SessionRow, uuid.UUID(str(q.source_session_id)))
    except ValueError:
        return
    if row is None or not row.state:
        return
    cat = session_from_state(row.state).cat
    scale = load_scale()
    q.p_pass = round(cat.posterior(scale).p_at_least(scale.cut(grade)), 4)
    subs = module_subscores(cat, grade)
    q.subscores = {k: dict(v) for k, v in subs.items()}
    q.module_tags = {k: v["tag"] for k, v in subs.items() if v.get("tag") in (TAG_CONFIRMED, TAG_STRONG)}
    q.module_theta = {k: v["theta"] for k, v in subs.items()}


def is_closed(ld: Loaded) -> bool:
    return ld.sess.status in CLOSED_STATUSES


def spec_label(code: str) -> str:
    return spec_name(code)
