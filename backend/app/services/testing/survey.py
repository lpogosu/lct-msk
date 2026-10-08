"""Опрос кандидата (К1/К2): 4 шага с сохранением после каждого, классификатор специализации, история.

Источники: concept §2.4, §3.1 шаг 3; spec-testing §2.2 п.2, §3.7. Формулы — в app/testing/survey.py
(classify, suggest_grade); здесь — хранение версий SurveyResponse и перевод опроса в квалификацию.

Версии: пока опрос не завершён, шаги дописываются в ту же анкету (обрыв связи не теряет ответы).
После завершения шаг 1 начинает НОВУЮ анкету (история опросов видна в «Мой грейд»); прошлые ответы
копируются в неё как подстановка, чтобы не заполнять всё заново.

Самооценка и технологии идут только в серые чипы «заявлено» и в оценку уровня не входят (DECISIONS п. 1).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import bad_request, conflict
from app.models.candidate import SurveyResponse
from app.models.users import User
from app.services.testing import labels as L
from app.services.testing.qualification import get_or_create_row, load_domain, save_domain
from app.services.testing.schemas import (
    CodeLabel,
    GradeRef,
    SpecOption,
    SpecProbability,
    SurveyAnswersOut,
    SurveyClassificationOut,
    SurveyFormOut,
    SurveyHistoryItemOut,
    SurveyStateOut,
)
from app.testing.cat import module_names
from app.testing.survey import SurveyAnswers, SurveyResult, classify, load_survey_config, survey_form

STEPS = 4
STEP_INDUSTRIES, STEP_ACTIONS, STEP_STACK, STEP_SPEC = 1, 2, 3, 4
SELF_ASSESSMENT_MAX = 3


# ---------------------------------------------------------------------------
# Форма
# ---------------------------------------------------------------------------


def form_out() -> SurveyFormOut:
    f = survey_form()
    names = module_names()
    return SurveyFormOut(
        version=int(f["version"]),
        industries=[CodeLabel(code=i["code"], label=i["label"]) for i in f["industries"]],
        actions=[CodeLabel(code=a["code"], label=a["label"]) for a in f["actions"]],
        frequency_levels=[CodeLabel(code=x["code"], label=x["label"]) for x in f["frequency_levels"]],
        technologies=[CodeLabel(code=t["code"], label=t["label"]) for t in f["technologies"]],
        recency_levels=[CodeLabel(code=x["code"], label=x["label"]) for x in f["recency_levels"]],
        modules=[CodeLabel(code=c, label=n) for c, n in names.items()],
        specializations=[
            SpecOption(code=s["code"], label=s["label"], status=s["status"], testable=L.spec_testable(s["code"]))
            for s in f["specializations"]
        ],
    )


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------


def _surveys(db: Session, user_id: uuid.UUID) -> list[SurveyResponse]:
    return list(
        db.scalars(
            select(SurveyResponse)
            .where(SurveyResponse.candidate_id == user_id)
            .order_by(SurveyResponse.created_at, SurveyResponse.id)
        )
    )


def latest(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> SurveyResponse | None:
    q = (
        select(SurveyResponse)
        .where(SurveyResponse.candidate_id == user_id)
        .order_by(SurveyResponse.created_at.desc(), SurveyResponse.id.desc())
        .limit(1)
    )
    if lock:
        q = q.with_for_update()
    return db.scalars(q).first()


def _number(db: Session, sv: SurveyResponse) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(SurveyResponse)
            .where(SurveyResponse.candidate_id == sv.candidate_id, SurveyResponse.created_at <= sv.created_at)
        )
        or 1
    )


def answers_out(answers: dict[str, Any]) -> SurveyAnswersOut:
    return SurveyAnswersOut(
        industries=list(answers.get("industries") or []),
        actions=dict(answers.get("actions") or {}),
        technologies=dict(answers.get("technologies") or {}),
        experience_months=answers.get("experience_months"),
        roles=list(answers.get("roles") or []),
        self_assessment=dict(answers.get("self_assessment") or {}),
        chosen_spec=answers.get("chosen_spec"),
    )


def _domain_answers(answers: dict[str, Any], chosen_spec: str) -> SurveyAnswers:
    return SurveyAnswers(
        chosen_spec=chosen_spec,
        actions=dict(answers.get("actions") or {}),
        technologies=dict(answers.get("technologies") or {}),
        experience_months=answers.get("experience_months"),
        industries=tuple(answers.get("industries") or ()),
    )


def classification_out(res: SurveyResult) -> SurveyClassificationOut:
    names = module_names()
    probs = sorted(res.probs.items(), key=lambda kv: (-kv[1], kv[0]))
    return SurveyClassificationOut(
        probabilities=[SpecProbability(code=c, name=L.spec_name(c), probability=round(p, 4)) for c, p in probs],
        top=res.top,
        top_name=L.spec_name(res.top),
        chosen=res.chosen,
        chosen_name=L.spec_name(res.chosen),
        hint=res.hint,
        hint_text=res.hint_text_ru,
        chosen_status=res.chosen_status,
        testable=L.spec_testable(res.chosen),
        waitlist_text=res.waitlist_text_ru,
        derived_text=res.derived_text_ru,
        declared_modules=[CodeLabel(code=m, label=names.get(m, m)) for m in res.declared_modules],
        suggested_grade=GradeRef(code=res.suggested_grade, name=L.grade_name(res.suggested_grade))
        if res.suggested_grade
        else None,
        suggested_grade_text=res.suggested_grade_text_ru,
    )


def _classification_of(sv: SurveyResponse) -> SurveyClassificationOut | None:
    if sv.completed_at is None or not sv.chosen_spec:
        return None
    try:
        res = classify(_domain_answers(sv.answers or {}, sv.chosen_spec))
    except ValueError:  # справочник изменился после заполнения анкеты — итог показываем по сохранённому
        return None
    return classification_out(res)


def state_out(db: Session, sv: SurveyResponse | None) -> SurveyStateOut:
    cfg = load_survey_config()
    if sv is None:
        return SurveyStateOut(
            number=0, version=cfg.version, step=0, completed=False, next_step=1, answers=SurveyAnswersOut()
        )
    completed = sv.completed_at is not None
    cls = _classification_of(sv)
    message = None
    if cls is not None:
        message = cls.waitlist_text or cls.derived_text
    return SurveyStateOut(
        survey_id=sv.id,
        number=_number(db, sv),
        version=sv.version,
        step=sv.step,
        completed=completed,
        completed_at=sv.completed_at,
        next_step=None if completed else min(sv.step + 1, STEPS),
        answers=answers_out(sv.answers or {}),
        classification=cls,
        message=message,
    )


def history_out(db: Session, user: User) -> list[SurveyHistoryItemOut]:
    out: list[SurveyHistoryItemOut] = []
    for i, sv in enumerate(_surveys(db, user.id), start=1):
        if sv.completed_at is None:
            continue
        out.append(
            SurveyHistoryItemOut(
                survey_id=sv.id,
                number=i,
                version=sv.version,
                completed_at=sv.completed_at,
                completed_ago=L.rel(sv.completed_at),
                chosen_spec=sv.chosen_spec,
                chosen_name=L.spec_name(sv.chosen_spec) if sv.chosen_spec else None,
                top=sv.suggested_spec,
                top_name=L.spec_name(sv.suggested_spec) if sv.suggested_spec else None,
                experience_months=sv.experience_months,
            )
        )
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Сохранение шагов
# ---------------------------------------------------------------------------


def _open_for_step(db: Session, user: User, step: int) -> SurveyResponse:
    """Анкета, в которую пишется шаг. Шаги не перескакиваются: можно сохранить уже пройденный шаг
    или следующий за ним. После завершения опроса шаг 1 открывает новую версию анкеты."""
    cfg = load_survey_config()
    sv = latest(db, user.id, lock=True)
    if sv is None or sv.completed_at is not None:
        if step != STEP_INDUSTRIES:
            raise conflict(
                "survey_step_order",
                "Новый опрос начинается с первого шага",
                {"next_step": STEP_INDUSTRIES},
            )
        prev = dict(sv.answers or {}) if sv is not None else {}
        now = clock.now()
        # Порядок версий — по created_at: при остановленных часах (тесты, демо) новая анкета всё равно позже
        created = now if sv is None or now > sv.created_at else sv.created_at + timedelta(microseconds=1)
        sv = SurveyResponse(
            candidate_id=user.id,
            version=cfg.version,
            step=0,
            answers=prev,  # подстановка прошлых ответов: кандидат их правит, а не заполняет заново
            industries=list(prev.get("industries") or []),
            created_at=created,
            updated_at=now,
        )
        db.add(sv)
        db.flush()
        return sv
    if step > sv.step + 1:
        raise conflict(
            "survey_step_order",
            f"Сначала заполните шаг {sv.step + 1}",
            {"next_step": sv.step + 1},
        )
    return sv


def _touch(sv: SurveyResponse, step: int, answers: dict[str, Any]) -> None:
    sv.answers = answers
    sv.step = max(sv.step, step)
    sv.updated_at = clock.now()


def save_industries(db: Session, user: User, industries: list[str]) -> SurveyResponse:
    known = {i["code"] for i in L.taxonomy().get("industries", [])}
    unknown = sorted(set(industries) - known)
    if unknown:
        raise bad_request("unknown_industry", "Неизвестная сфера бизнеса", {"codes": unknown})
    sv = _open_for_step(db, user, STEP_INDUSTRIES)
    uniq = list(dict.fromkeys(industries))
    answers = dict(sv.answers or {})
    answers["industries"] = uniq
    sv.industries = uniq
    _touch(sv, STEP_INDUSTRIES, answers)
    db.flush()
    return sv


def save_actions(db: Session, user: User, actions: dict[str, str]) -> SurveyResponse:
    cfg = load_survey_config()
    unknown = sorted(set(actions) - set(cfg.actions))
    if unknown:
        raise bad_request("unknown_action", "Неизвестное действие", {"codes": unknown})
    sv = _open_for_step(db, user, STEP_ACTIONS)
    answers = dict(sv.answers or {})
    answers["actions"] = {a: f for a, f in actions.items() if f != "never"}  # «не делал» — по умолчанию
    _touch(sv, STEP_ACTIONS, answers)
    db.flush()
    return sv


def save_stack(
    db: Session,
    user: User,
    *,
    technologies: dict[str, str],
    experience_months: int,
    roles: list[str],
    self_assessment: dict[str, int],
) -> SurveyResponse:
    cfg = load_survey_config()
    bad_tech = sorted(set(technologies) - set(cfg.technologies))
    if bad_tech:
        raise bad_request("unknown_technology", "Неизвестная технология", {"codes": bad_tech})
    bad_rec = sorted({r for r in technologies.values() if r not in cfg.recency})
    if bad_rec:
        raise bad_request(
            "unknown_recency", "Неизвестная давность использования", {"codes": bad_rec, "allowed": list(cfg.recency)}
        )
    modules = set(module_names())
    bad_mod = sorted(set(self_assessment) - modules)
    if bad_mod:
        raise bad_request("unknown_module", "Неизвестный модуль самооценки", {"codes": bad_mod})
    if any(not 0 <= v <= SELF_ASSESSMENT_MAX for v in self_assessment.values()):
        raise bad_request("self_assessment_range", f"Самооценка — от 0 до {SELF_ASSESSMENT_MAX}")
    clean_roles = [r.strip()[:64] for r in roles if r and r.strip()]
    sv = _open_for_step(db, user, STEP_STACK)
    answers = dict(sv.answers or {})
    answers.update(
        technologies=dict(technologies),
        experience_months=int(experience_months),
        roles=clean_roles,
        self_assessment=dict(self_assessment),
    )
    sv.experience_months = int(experience_months)
    sv.self_assessment = dict(self_assessment)
    _touch(sv, STEP_STACK, answers)
    db.flush()
    return sv


def _check_spec(code: str) -> dict[str, Any]:
    info = L.spec_info(code)
    if info is None:
        raise bad_request("unknown_specialization", "Неизвестная специализация", {"code": code})
    return info


def hint(db: Session, user: User, chosen_spec: str) -> SurveyClassificationOut:
    """Предпросмотр подсказки на шаге 4 (ничего не сохраняется): решает кандидат."""
    _check_spec(chosen_spec)
    sv = latest(db, user.id)
    answers = dict(sv.answers or {}) if sv is not None else {}
    return classification_out(classify(_domain_answers(answers, chosen_spec)))


def complete(db: Session, user: User, chosen_spec: str) -> tuple[SurveyResponse, SurveyClassificationOut]:
    """Шаг 4: специализация → классификатор → квалификация NONE → SURVEY_DONE (для тестируемых)."""
    info = _check_spec(chosen_spec)
    sv = _open_for_step(db, user, STEP_SPEC)
    answers = dict(sv.answers or {})
    answers["chosen_spec"] = chosen_spec
    try:
        res = classify(_domain_answers(answers, chosen_spec))
    except ValueError as e:  # старая анкета с кодами, которых уже нет в справочнике
        raise bad_request("survey_outdated", "Анкета устарела: пройдите опрос заново", {"reason": str(e)}) from e
    now = clock.now()
    sv.spec_probs = {k: round(v, 4) for k, v in res.probs.items()}
    sv.suggested_spec = res.top
    sv.chosen_spec = chosen_spec
    sv.stack_declared = list(res.declared_modules)
    sv.suggested_grade = res.suggested_grade
    sv.completed_at = sv.completed_at or now
    _touch(sv, STEP_SPEC, answers)
    db.flush()
    # Квалификация заводится только по тестируемым направлениям. Fullstack — производная категория:
    # опрос открывает обе составляющие. survey_only — лист ожидания (считается по анкетам), без квалификации.
    targets: list[str] = []
    if info.get("status") == "derived":
        targets = [s for s in info.get("derived_from", ()) if L.spec_testable(s)]
    elif L.spec_testable(chosen_spec):
        targets = [chosen_spec]
    for spec in targets:
        row = get_or_create_row(db, user.id, spec, lock=True)
        q = load_domain(row)
        q.record_survey(clock)
        save_domain(db, row, q)
    return sv, classification_out(res)
