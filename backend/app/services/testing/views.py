"""Сборка ответов API тестирования из доменных объектов (К3 брифинг, К4 плеер, К5 результат, К6 «Мой грейд»).

Правила показа: ключ и теги дистракторов в плеер не попадают никогда (только whitelisted поля
rendered); разбор ошибок — только своего экземпляра и только после решения; на проверке у методиста
результат скрыт; работодатель флагов не видит, кандидат видит свои без источника «чужого ключа».
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.consents import CONSENT_DOCUMENTS, has_active_consent
from app.models.candidate import CandidateProfile, SurveyResponse
from app.models.enums import ConsentPurpose
from app.models.testing import Flag as FlagRow
from app.models.testing import ItemInstance, TestResponse
from app.models.testing import TestSession as SessionRow
from app.models.users import User
from app.services.testing import labels as L
from app.services.testing.express import apply_express, express_session_fields
from app.services.testing.pool import readiness as pool_readiness
from app.services.testing.qualification import list_rows, load_domain
from app.services.testing.schemas import (
    AttemptOut,
    BlindCardModuleOut,
    BlindCardPreviewOut,
    BriefingOut,
    CandidateFlagOut,
    CertaintyOut,
    CodeLabel,
    ConsentStateOut,
    DecisionOut,
    FreshnessOut,
    GradeCardOut,
    GradeChangeOut,
    GradeRef,
    ItemOut,
    LevelLineOut,
    ModuleTagOut,
    OfferOut,
    OptionOut,
    PoolReadinessOut,
    ProgressOut,
    QualificationOut,
    ResultOut,
    ReviewItemOut,
    ReviewOut,
    RuleCheckOut,
    RulerCutOut,
    RulerOut,
    SessionOut,
)
from app.services.testing.sessions import IssuedItem, Loaded, active_session, instance_of, needs_review
from app.testing.bank.registry import all_families
from app.testing.cat import (
    TAG_CONFIRMED,
    TAG_STRONG,
    level_breakdown,
    load_cat_config,
    module_names,
    module_subscores,
    stop_text_ru,
)
from app.testing.irt import BELOW_INTERN, load_scale
from app.testing.session import (
    CLOSED_STATUSES,
    STATUS_LABELS_RU,
    Qualification,
    QualStatus,
    RuleCheck,
    SessionStatus,
    category_of,
    freshness_of,
    load_rules,
)
from app.testing.survey import load_survey_config, senior_warning, suggest_grade

ANSWER_FORMATS = {
    "text": "строка",
    "choice": "id варианта",
    "line_choice": '{"line": номер строки с 1, "fix": id исправления}',
    "order": "список строк в верном порядке или их индексов (с 0)",
}
NO_BACK_NOTE = "Кнопки «назад» нет: тест адаптивный, следующее задание зависит от ответа на это"
FLAG_STATUS_RU = {"open": "на рассмотрении", "dismissed": "снят", "confirmed": "подтверждён"}


def grade_ref(code: str | None) -> GradeRef | None:
    if code is None:
        return None
    return GradeRef(code=code, name=L.grade_name(code))


def consent_state(db: Session, user_id: uuid.UUID, purpose: ConsentPurpose) -> ConsentStateOut:
    doc = CONSENT_DOCUMENTS[purpose]
    return ConsentStateOut(
        purpose=purpose.value,
        given=has_active_consent(db, user_id, purpose),
        version=doc.version,
        title=doc.title,
        summary=doc.summary,
    )


def rule_out(rc: RuleCheck) -> RuleCheckOut:
    return RuleCheckOut(
        ok=rc.ok, code=rc.code, message=rc.message_ru, available_at=rc.available_at, available_in=L.rel(rc.available_at)
    )


# ---------------------------------------------------------------------------
# Сессия и задание
# ---------------------------------------------------------------------------


def session_out(ld: Loaded) -> SessionOut:
    s, row = ld.sess, ld.row
    prog = s.progress()
    return SessionOut(
        id=row.id,
        kind=s.kind,
        kind_ru=L.KIND_RU.get(s.kind, s.kind),
        specialization=CodeLabel(code=s.spec, label=L.spec_name(s.spec)),
        claimed_grade=GradeRef(code=s.claimed_grade, name=L.grade_name(s.claimed_grade)),
        target_grade=GradeRef(code=s.cat.target_grade, name=L.grade_name(s.cat.target_grade)),
        status=str(s.status),
        status_ru=STATUS_LABELS_RU[s.status],
        started_at=s.started_at,
        progress=ProgressOut(position=prog["position"], text=prog["text_ru"]),
        parent_session_id=row.parent_session_id,
        child_session_id=uuid.UUID(s.extended_by) if s.extended_by else None,
        finished=s.status not in (SessionStatus.CREATED, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED),
        **express_session_fields(s),
    )


_PUBLIC_RENDERED = ("prompt", "code", "language", "options", "lines", "table", "answer_mode", "answer_hint")


def public_rendered(rendered: dict[str, Any]) -> dict[str, Any]:
    """Только поля Rendered.to_public() — защита от того, что в jsonb окажется что-то лишнее."""
    r = {k: rendered.get(k) for k in _PUBLIC_RENDERED}
    r["options"] = [OptionOut(id=str(o["id"]), text=str(o["text"])) for o in r.get("options") or ()]
    r["lines"] = [str(x) for x in r.get("lines") or ()]
    r["answer_mode"] = r.get("answer_mode") or "choice"
    return r


def item_out(ld: Loaded, item: IssuedItem) -> ItemOut:
    resp, inst = item.response, item.instance
    r = public_rendered(inst.rendered or {})
    deadline = resp.deadline_at or ld.sess.deadline() or clock.now()
    left = max(0, int((deadline - clock.now()).total_seconds()))
    sel = ld.sess.pending
    return ItemOut(
        seq=resp.seq,
        variant_code=f"#{inst.variant_code}",
        prompt=r["prompt"] or "",
        code=r["code"],
        language=r["language"],
        options=r["options"],
        lines=r["lines"],
        table=r["table"],
        answer_mode=r["answer_mode"],
        answer_hint=r["answer_hint"],
        answer_format=ANSWER_FORMATS.get(r["answer_mode"], "строка"),
        time_limit_s=sel.time_limit_s if sel else load_cat_config().default_time_limit_s,
        deadline_at=deadline,
        seconds_left=left,
        clip=clip_of(inst.rendered or {}),
    )


def clip_of(rendered: dict[str, Any]) -> dict[str, Any] | None:
    """Данные «двойника в буфере» от хука anti-assist (anticheat_hooks.clipboard_variant) — без изменений.
    Пока хук пустой — None. Ключ двойника хук туда не кладёт (он только на сервере)."""
    clip = rendered.get("clip")
    return dict(clip) if isinstance(clip, dict) else None


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------


def ruler() -> RulerOut:
    scale = load_scale()
    cuts = [RulerCutOut(grade=g, name=scale.grade_name(g), score=round(scale.score(c))) for g, c in scale.cuts]
    lo = round(scale.score(scale.cuts[0][1]))
    hi = round(scale.score(scale.cuts[-1][1]))
    return RulerOut(min=lo, max=hi, cuts=cuts)


def modules_out(subscores: dict[str, Any], only_tagged: bool = False) -> list[ModuleTagOut]:
    out = []
    for m, s in subscores.items():
        if only_tagged and s.get("tag") not in (TAG_CONFIRMED, TAG_STRONG):
            continue
        out.append(
            ModuleTagOut(
                module=m,
                name=str(s.get("name", m)),
                n=int(s.get("n", 0)),
                correct=int(s.get("correct", 0)),
                tag=str(s.get("tag", "none")),
                symbol=str(s.get("symbol", "")),
                tag_ru=str(s.get("tag_ru", "")),
                level_ru=str(s.get("level_ru", "")),
            )
        )
    return out


def blind_card_preview(
    spec: str,
    grade: str | None,
    score: int | None,
    interval: list[int],
    confidence: int | None,
    n_items: int,
    by_level_text: str,
    subscores: dict[str, Any],
    trust: float | None,
    borderline: bool,
    last_activity: datetime | None,
) -> BlindCardPreviewOut:
    scale = load_scale()
    category = category_of(spec, grade)["name"] if grade else None
    trust_ru = L.trust_label(trust)
    parts: list[str] = []
    if grade:
        parts.append(f"{scale.grade_name(grade)} · подтверждён тестом {L.rel(last_activity) or 'только что'}")
    if score is not None and len(interval) == 2:
        parts.append(f"{score} (80%: {interval[0]}–{interval[1]})")
    if confidence is not None:
        parts.append(f"уверенность {confidence}%" + (", на границе" if borderline else ""))
    parts.append(f"{n_items} заданий")
    if trust_ru:
        parts.append(f"доверие {trust_ru}")
    fresh = freshness_of(last_activity, clock.now(), load_rules()) if last_activity else None
    if fresh:
        parts.append(f"актуальность {fresh['relevance']}")
    mods = [
        BlindCardModuleOut(name=str(s.get("name", m)), symbol=str(s.get("symbol", "")))
        for m, s in subscores.items()
        if s.get("tag") in (TAG_CONFIRMED, TAG_STRONG)
    ]
    return BlindCardPreviewOut(
        pseudonym="К-····",
        category=category,
        badge=" · ".join(parts),
        score=score,
        interval80=interval,
        confidence_pct=confidence,
        how_obtained=by_level_text,
        modules=mods,
        trust=trust_ru,
        freshness=fresh["band_ru"] if fresh else None,
        note="Работодатель не видит неудачные попытки, флаги, задания и ваши ответы",
    )


def result_out(db: Session, user: User, ld: Loaded) -> ResultOut:
    s = ld.sess
    scale = load_scale()
    blind = consent_state(db, user.id, ConsentPurpose.blind_card)
    base: dict[str, Any] = {"session": session_out(ld), "n_shown": len(s.cat.responses), "blind_card_consent": blind}
    if s.status == SessionStatus.ABANDONED:
        return ResultOut(
            **base,
            visible=False,
            review_available=False,
            message="Тест прерван: связь пропала больше чем на 30 минут. Попытка засчитана, результат не публикуется",
        )
    if s.status == SessionStatus.UNDER_REVIEW:
        days = load_rules().review_max_days
        return ResultOut(
            **base,
            visible=False,
            review_available=False,
            message=f"Результат скрыт до решения методиста (не дольше {days} дней). Свои флаги видны в «Мой грейд»",
        )
    if s.status == SessionStatus.INVALIDATED:
        return ResultOut(**base, visible=False, review_available=False, message="Результат аннулирован методистом")
    d = s.decision
    assert d is not None
    post = s.cat.posterior(scale)
    granted = s.granted_grade()
    subs = module_subscores(s.cat, granted or d.target_grade)
    by_level, by_level_text = level_breakdown(s.cat, scale)
    score = round(scale.score(post.mean))
    lo, hi = scale.score_interval(post.mean, post.sd)
    interval = [round(lo), round(hi)]
    conf = round(100 * d.p_pass)
    probs = post.class_probs()
    certainty = [
        CertaintyOut(grade=g, name=L.band_name(g), probability=round(p, 4), percent=round(100 * p))
        for g, p in probs.items()
    ]
    if granted and granted == d.target_grade:
        label = "подтверждён на границе" if d.borderline else "подтверждён"
        headline = f"{scale.grade_name(granted)} {label} · {score} · уверенность {conf}%"
    elif granted:  # добор вверх не удался — остаётся грейд основной части
        headline = f"{scale.grade_name(granted)} остаётся подтверждённым · {score}"
    else:
        headline = f"{scale.grade_name(d.target_grade)} не подтверждён · {score}"
    offers = [
        OfferOut(
            direction=o.direction,
            target_grade=GradeRef(code=o.target_grade, name=scale.grade_name(o.target_grade)),
            instant=o.instant,
            max_items=o.max_items,
            confidence_pct=round(100 * o.p_already),
            text=o.text_ru,
        )
        for o in (s.offers if s.status == SessionStatus.EXTENSION_OFFERED else ())
    ]
    message = None
    if s.extended_by:
        # Принят добор: итог попытки решает дочерняя сессия (тот же апостериор)
        message = "Принят добор: итоговый грейд — в результате добора"
    elif not granted and s.kind == "upgrade" and s.base_grade:
        days = load_rules().upgrade_retry_days
        message = (
            f"Грейд не изменился: остаётся {scale.grade_name(s.base_grade)}, принудительно не понижаем. "
            f"Следующая попытка повышения — через {days} дней"
        )
    elif not granted and not offers and s.status in CLOSED_STATUSES:
        days = load_rules().retake_days
        message = f"Грейд не подтверждён. Повторить тест можно через {days} дней — откликаться на вакансии можно и сейчас"
        lower = scale.prev_grade(s.claimed_grade) if s.claimed_grade in scale.grades else None
        if lower is not None:
            message += f". Тест на грейд ниже ({scale.grade_name(lower)}) можно пройти сразу — пересдачу он не расходует"
    if granted and not blind.given and not s.extended_by:
        message = "Чтобы работодатели вас увидели, дайте согласие на показ слепой карточки"
    if s.status == SessionStatus.EXTENSION_OFFERED and needs_review(ld):
        days = load_rules().review_max_days
        message = (
            f"Результат проверит методист (не дольше {days} дней), до решения вас нет в выдаче работодателей. "
            "Свои отметки видны в «Мой грейд». Предложение добора действует"
        )
    preview = None
    if granted:
        preview = blind_card_preview(
            s.spec,
            granted,
            score,
            interval,
            conf,
            len(s.cat.responses),
            by_level_text,
            subs,
            s.trust if s.status == SessionStatus.FINAL else None,
            d.borderline,
            s.decided_at,
        )
    cat = category_of(s.spec, granted) if granted else None
    out = ResultOut(
        **base,
        visible=True,
        message=message,
        headline=headline,
        decision=DecisionOut(
            code=d.code, label=L.DECISION_RU.get(d.code, d.code), passed=d.passed, borderline=d.borderline, reason=d.reason
        ),
        stop_text=stop_text_ru(d, scale, n_shown=len(s.cat.responses)),
        confirmed_grade=grade_ref(granted),
        category=CodeLabel(code=cat["code"], label=cat["name"]) if cat else None,
        score=score,
        interval80=interval,
        ruler=ruler(),
        confidence_pct=conf,
        certainty=certainty,
        by_level=[LevelLineOut(grade=g, name=scale.grade_name(g), correct=c, total=n) for g, (c, n) in by_level.items()],
        by_level_text=by_level_text,
        modules=modules_out(subs),
        offers=offers,
        offers_expire_at=s.offers_expire_at if offers else None,
        offers_expire_in=L.rel(s.offers_expire_at) if offers else None,
        review_available=True,
        blind_card_preview=preview,
    )
    # Экспресс жюри: вместо категории — оценка с погрешностью или «нужен полный тест» (app/services/testing/express.py)
    return apply_express(out, s, d, scale) if s.express else out


# ---------------------------------------------------------------------------
# Разбор ошибок
# ---------------------------------------------------------------------------


def _answer_text(value: Any, rendered: dict[str, Any]) -> str | None:
    if value is None:
        return None
    mode = rendered.get("answer_mode")
    options = {str(o["id"]): str(o["text"]) for o in rendered.get("options") or ()}
    lines = list(rendered.get("lines") or ())
    if mode == "choice" and isinstance(value, str):
        return options.get(value.strip().lower(), options.get(value, value))
    if mode == "line_choice" and isinstance(value, dict):
        fix = options.get(str(value.get("fix")), str(value.get("fix")))
        return f"строка {value.get('line')}: {fix}"
    if mode == "order" and isinstance(value, list):
        out = []
        for x in value:
            if isinstance(x, int) and 0 <= x < len(lines):
                out.append(lines[x])
            else:
                out.append(str(x))
        return "\n".join(out)
    return value if isinstance(value, str) else str(value)


def review_out(db: Session, ld: Loaded) -> ReviewOut:
    s = ld.sess
    names = module_names()
    fams = all_families()
    responses = {
        str(r.instance_id): r
        for r in db.scalars(
            select(TestResponse).where(TestResponse.instance_id.in_([uuid.UUID(x.instance_id) for x in s.cat.responses if x.instance_id]))
        )
    }
    items: list[ReviewItemOut] = []
    for rec in s.cat.responses:
        if rec.correct or not rec.instance_id:
            continue
        inst_row = db.get(ItemInstance, uuid.UUID(rec.instance_id))
        if inst_row is None or inst_row.issued_to_user_id != ld.row.candidate_id:
            continue  # только свой экземпляр
        inst = instance_of(inst_row)
        rendered = inst_row.rendered or {}
        pub = public_rendered(rendered)
        resp = responses.get(rec.instance_id)
        own = resp.answer if resp is not None else None
        tag = resp.distractor_tag if resp is not None else None
        fam = fams.get(inst_row.family_id)
        hints = getattr(fam, "misconceptions", None) or getattr(fam, "misconception_hints", None) or {}
        misconception = None
        if tag:
            for part in str(tag).split("+"):
                if isinstance(hints, dict) and part in hints:
                    misconception = str(hints[part])
                    break
        items.append(
            ReviewItemOut(
                seq=rec.seq,
                variant_code=f"#{inst_row.variant_code}",
                module=CodeLabel(code=rec.module, label=names.get(rec.module, rec.module)),
                prompt=pub["prompt"] or "",
                code=pub["code"],
                language=pub["language"],
                options=pub["options"],
                lines=pub["lines"],
                table=pub["table"],
                answer_mode=pub["answer_mode"],
                your_answer=own,
                your_answer_text=_answer_text(own, rendered),
                correct_answer=inst.key,
                correct_answer_text=_answer_text(inst.key, rendered) or "",
                timed_out=rec.timed_out,
                explanation=inst.explanation or None,
                misconception=misconception,
            )
        )
    return ReviewOut(
        session_id=ld.row.id,
        n_total=len(s.cat.responses),
        n_wrong=sum(1 for r in s.cat.responses if not r.correct),
        items=items,
        note="Этот вариант больше никому не выдаётся, поэтому верный ответ можно показать",
    )


# ---------------------------------------------------------------------------
# «Мой грейд»
# ---------------------------------------------------------------------------


def latest_survey(db: Session, user_id: uuid.UUID) -> SurveyResponse | None:
    return db.scalars(
        select(SurveyResponse)
        .where(SurveyResponse.candidate_id == user_id, SurveyResponse.completed_at.is_not(None))
        .order_by(SurveyResponse.created_at.desc(), SurveyResponse.id.desc())
        .limit(1)
    ).first()


def declared_modules(db: Session, user_id: uuid.UUID) -> list[CodeLabel]:
    sv = latest_survey(db, user_id)
    names = module_names()
    if sv is None:
        return []
    return [CodeLabel(code=m, label=names.get(m, m)) for m in (sv.stack_declared or []) if isinstance(m, str)]


def candidate_flags(db: Session, user_id: uuid.UUID, spec: str | None = None) -> list[CandidateFlagOut]:
    q = (
        select(FlagRow, SessionRow.specialization)
        .join(SessionRow, SessionRow.id == FlagRow.session_id)
        .where(SessionRow.candidate_id == user_id)
        .order_by(FlagRow.created_at.desc())
    )
    if spec is not None:
        q = q.where(SessionRow.specialization == spec)
    out = []
    for f, _spec in db.execute(q):
        out.append(
            CandidateFlagOut(
                id=f.id,
                session_id=f.session_id,
                kind=f.kind,
                severity=f.severity,
                message=str((f.details or {}).get("message_ru") or ""),
                status=f.status,
                status_ru=FLAG_STATUS_RU.get(f.status, f.status),
                created_at=f.created_at,
                created_ago=L.rel(f.created_at),
                dispute_text=f.dispute_text,
                can_dispute=f.status == "open" and f.disputed_at is None,
            )
        )
    return out


def attempts_out(db: Session, user_id: uuid.UUID, spec: str) -> list[AttemptOut]:
    rows = db.scalars(
        select(SessionRow)
        .where(SessionRow.candidate_id == user_id, SessionRow.specialization == spec)
        .order_by(SessionRow.created_at.desc())
    )
    out = []
    for r in rows:
        status = SessionStatus(r.status)
        hide = status in (SessionStatus.UNDER_REVIEW, SessionStatus.ABANDONED, SessionStatus.INVALIDATED)
        out.append(
            AttemptOut(
                session_id=r.id,
                kind=r.kind,
                kind_ru=L.KIND_RU.get(r.kind, r.kind),
                status=r.status,
                status_ru=STATUS_LABELS_RU[status],
                claimed_grade=GradeRef(code=r.claimed_grade, name=L.grade_name(r.claimed_grade)),
                decision=None if hide else r.decision,
                decision_ru=None if hide or not r.decision else L.DECISION_RU.get(r.decision),
                score=None if hide else L.score_of(r.theta),
                started_at=r.started_at,
                started_ago=L.rel(r.started_at),
            )
        )
    return out


def qualification_out(db: Session, user: User, spec: str, q: Qualification) -> QualificationOut:
    scale = load_scale()
    rules = load_rules()
    now = clock.now()
    confirmed = q.status in (QualStatus.CONFIRMED, QualStatus.UPGRADE_TESTING) and q.grade is not None
    se_now = q.se_now(clock, rules)
    score = interval = conf = None
    if confirmed and q.theta is not None:
        score = round(scale.score(q.theta))
        lo, hi = scale.score_interval(q.theta, se_now or 0.0)
        interval = [round(lo), round(hi)]
        conf = round(100 * q.p_pass) if q.p_pass is not None else None
    fresh = freshness_of(q.last_activity_at, now, rules) if q.last_activity_at else None
    cat = category_of(spec, q.grade) if confirmed and q.grade else None
    hist = [
        GradeChangeOut(
            from_grade=grade_ref(c.from_grade),
            to_grade=grade_ref(c.to_grade),
            reason=c.reason,
            reason_ru=L.CHANGE_REASON_RU.get(c.reason, c.reason),
            at=c.at,
            ago=L.rel(c.at),
            counts_as_change=c.counts_as_change,
        )
        for c in q.history  # порядок домена: при равном времени (остановленные часы) строки БД не упорядочить
    ]
    return QualificationOut(
        specialization=CodeLabel(code=spec, label=L.spec_name(spec)),
        status=str(q.status),
        status_ru=L.QUAL_STATUS_RU.get(str(q.status), str(q.status)),
        grade=grade_ref(q.grade) if confirmed else None,
        category=CodeLabel(code=cat["code"], label=cat["name"]) if cat else None,
        score=score,
        interval80=interval or [],
        confidence_pct=conf,
        borderline=bool(q.borderline) if confirmed else False,
        trust=L.trust_label(q.trust, q.hidden) if confirmed else None,
        hidden=bool(q.hidden),
        hidden_message=(
            f"Результат скрыт до решения методиста, не дольше {rules.review_max_days} дней" if q.hidden else None
        ),
        freshness=FreshnessOut(
            relevance=fresh["relevance"], band=fresh["band"], band_ru=fresh["band_ru"], since=fresh["since_ru"]
        )
        if fresh and confirmed
        else None,
        modules_confirmed=modules_out(q.subscores or {}, only_tagged=True) if confirmed else [],
        modules_declared=declared_modules(db, user.id),
        next_change_allowed_at=q.next_change_allowed_at if confirmed else None,
        next_change_in=L.rel(q.next_change_allowed_at) if confirmed and q.next_change_allowed_at and q.next_change_allowed_at > now else None,
        next_attempt_allowed_at=q.next_attempt_allowed_at,
        next_attempt_in=L.rel(q.next_attempt_allowed_at) if q.next_attempt_allowed_at and q.next_attempt_allowed_at > now else None,
        can_start=rule_out(q.can_start_test(clock, rules)),
        lower_grades_open=q.lower_grades_open(clock, rules, scale),
        can_upgrade=rule_out(q.can_start_upgrade(clock, scale, rules)),
        history=hist,
        attempts=attempts_out(db, user.id, spec),
        flags=candidate_flags(db, user.id, spec),
    )


def qualifications_out(db: Session, user: User) -> list[QualificationOut]:
    rows = list_rows(db, user.id)
    out = [qualification_out(db, user, r.specialization, load_domain(r)) for r in rows]
    by_spec = {o.specialization.code: o for o in out}
    b, f = by_spec.get("backend_python"), by_spec.get("frontend_js")
    if b and f and b.grade and f.grade:
        b.is_fullstack = f.is_fullstack = True
    return out


# ---------------------------------------------------------------------------
# Брифинг (К3)
# ---------------------------------------------------------------------------


def experience_months(db: Session, user_id: uuid.UUID) -> int | None:
    sv = latest_survey(db, user_id)
    if sv is not None and sv.experience_months is not None:
        return int(sv.experience_months)
    prof = db.get(CandidateProfile, user_id)
    return int(prof.experience_months) if prof is not None and prof.experience_months is not None else None


def nca_wait(q: Qualification) -> bool:
    return bool(q.next_change_allowed_at and q.next_change_allowed_at > clock.now())


def retake_rules_text(rules: Any) -> list[str]:
    """Правила пересдачи и повышения простым языком; сроки — из config/testing.yaml (rules.*)."""
    return [
        f"Повысить грейд можно отдельным тестом на одну ступень вверх — не чаще раза в {rules.change_interval_days} дней "
        "после последней смены грейда. Перескочить через уровень за один день нельзя.",
        "Если заявленный грейд не подтвердился, вы получаете тот, который тест подтвердил — ниже заявленного. "
        "Попытка при этом не сгорает.",
        "Тест на более низкий грейд можно пройти сразу, не дожидаясь срока пересдачи: он не расходует пересдачу.",
        f"Повторить тот же грейд можно через {rules.retake_days} дней; не больше {rules.max_attempts} попыток за "
        f"{rules.attempts_window_days} дней.",
        f"Неудачное повышение грейд не снижает: остаётся подтверждённый, а новая попытка повышения — через "
        f"{rules.upgrade_retry_days} дней.",
    ]


def briefing_out(db: Session, user: User, spec: str, q: Qualification | None) -> BriefingOut:
    scale = load_scale()
    rules = load_rules()
    cfg = load_cat_config()
    scfg = load_survey_config()
    testable = L.spec_testable(spec)
    months = experience_months(db, user.id)
    hint_grade, hint_text = suggest_grade(months, scfg)
    survey_done = q is not None and q.status != QualStatus.NONE
    confirmed = q is not None and q.status == QualStatus.CONFIRMED and q.grade is not None
    if not testable:
        check = RuleCheckOut(
            ok=False, code="specialization_not_testable", message="Тест по этому направлению появится позже. Вы в листе ожидания"
        )
    elif q is None:
        check = RuleCheckOut(ok=False, code="survey_required", message="Сначала пройдите опрос — это около 3 минут")
    elif confirmed:
        check = rule_out(q.can_start_upgrade(clock, scale, rules))
    else:
        check = rule_out(q.can_start_test(clock, rules))
    pool_state = pool_readiness(db, spec) if testable else None
    if pool_state is not None and not pool_state.ready and check.ok:
        # #14: пул ещё греется — не ошибка, а понятное ожидание (экран опрашивает готовность)
        check = RuleCheckOut(ok=False, code="pool_warming_up", message=pool_state.message)
    mode = "unavailable" if not check.ok else ("upgrade" if confirmed else "test")
    upgrade_target = scale.next_grade(q.grade) if confirmed and q and q.grade else None
    lower_open: list[str] = []
    if testable and q is not None and not confirmed and q.status == QualStatus.NOT_CONFIRMED:
        lower_open = q.lower_grades_open(clock, rules, scale)
        if lower_open and not check.ok:
            # Ожидание пересдачи идёт, но грейды ниже проваленного открыты сразу (DECISIONS п. 29)
            check = rule_out(q.can_start_test(clock, rules, lower_open[0], scale))
            mode = "test"
    waiting = testable and q is not None and not confirmed and q.status == QualStatus.NOT_CONFIRMED and bool(
        q.next_attempt_allowed_at and q.next_attempt_allowed_at > clock.now()
    )
    cards = [
        GradeCardOut(
            code=g,
            name=scale.grade_name(g),
            score=round(scale.score(c)),
            anchor=L.grade_anchor(g),
            suggested=g == hint_grade,
            available=not (waiting and g not in lower_open and not q.can_start_test(clock, rules, g, scale).ok),
            note=(
                f"Пересдача доступна {L.rel(q.next_attempt_allowed_at)}"
                if waiting and g not in lower_open and q.next_attempt_allowed_at and not q.can_start_test(clock, rules, g, scale).ok
                else None
            ),
        )
        for g, c in scale.cuts
    ]
    retake_status = None
    if q is not None and q.status == QualStatus.NOT_CONFIRMED and q.last_failed_grade in scale.grades and waiting:
        failed = scale.grade_name(q.last_failed_grade)
        retake_status = f"Грейд «{failed}» не подтверждён: пересдать его можно {L.rel(q.next_attempt_allowed_at)}"
        if lower_open:
            retake_status += "; тест на грейд ниже — сразу, без ожидания"
    elif confirmed and q is not None and nca_wait(q):
        retake_status = f"Следующий тест на повышение — {L.rel(q.next_change_allowed_at)}"
    rules_text = [
        f"{cfg.n_min}–{cfg.n_max} заданий, обычно 12–15; около 20–25 минут",
        "Одно задание на экран, у каждого задания свой таймер; если время вышло, ответ засчитывается как неверный",
        "Возврата к прошлым заданиям нет: следующее задание зависит от ответа",
        "У каждого кандидата свой вариант — ответы друзей не помогут",
        f"{cfg.pretest_count} пробных задания не влияют на результат и ничем не отличаются от остальных",
        "Грейд принудительно не понижаем",
        f"Смена грейда — не чаще 1 раза в {rules.change_interval_days} дней; повтор без грейда — через {rules.retake_days} дней",
        "Грейд — это уровень навыков, а не стаж: подтверждает его только тест",
        "Журналируем уход со вкладки, вставку текста и время ответа. Камеры и микрофона нет",
    ]
    nca = q.next_change_allowed_at if confirmed and q else None
    act = active_session(db, user.id)
    return BriefingOut(
        specialization=CodeLabel(code=spec, label=L.spec_name(spec)),
        testable=testable,
        survey_done=survey_done,
        mode=mode,
        grades=cards,
        experience_months=months,
        experience_hint=hint_text,
        senior_warning=senior_warning("senior", months, scfg),
        rules=rules_text,
        consent=consent_state(db, user.id, ConsentPurpose.test_logging),
        can_start=check,
        retake_rules=retake_rules_text(rules),
        retake_status=retake_status,
        current_grade=grade_ref(q.grade) if confirmed and q else None,
        upgrade_target=grade_ref(upgrade_target),
        next_change_allowed_at=nca,
        next_change_in=L.rel(nca) if nca and nca > clock.now() else None,
        active_session_id=act.id if act is not None else None,
        pool=PoolReadinessOut.model_validate(pool_state.to_dict()) if pool_state is not None else None,
    )


def certainty_name(code: str) -> str:
    return L.grade_name(None) if code == BELOW_INTERN else L.grade_name(code)
