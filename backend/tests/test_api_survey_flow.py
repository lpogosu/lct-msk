"""API опроса (К1): 4 шага с сохранением и продолжением после обрыва, классификатор и подсказка, версии
анкеты и история, лист ожидания для survey_only, Fullstack, квалификация NONE → SURVEY_DONE
(concept §2.4, spec-testing §2.2 п.2, §3.7)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.candidate import SurveyResponse
from app.models.testing import Qualification
from tests import test_api_testing_support as support
from tests.test_api_testing_support import do_survey

# Фикстуры общего модуля (присваиванием, а не импортом: так их видит pytest и не ругается линтер)
api = support.api

pytestmark = pytest.mark.usefixtures("frozen_clock")


def _quals(db, user_id) -> dict[str, str]:
    return {q.specialization: q.status for q in db.scalars(select(Qualification).where(Qualification.candidate_id == user_id))}


def test_form_lists_steps_and_specializations(api, make_account):
    acc = make_account()
    f = api.get("/api/survey/form", acc)
    assert len(f["actions"]) == 16
    assert [x["code"] for x in f["frequency_levels"]] == ["never", "sometimes", "regularly"]
    specs = {s["code"]: s for s in f["specializations"]}
    assert specs["backend_python"]["testable"] and specs["frontend_js"]["testable"]
    # QA и DevOps — действующие специализации с тестом (модули qa_testing и devops_core); без теста остаётся Data/ML
    assert specs["qa"]["status"] == "active" and specs["qa"]["testable"]
    assert specs["devops"]["status"] == "active" and specs["devops"]["testable"]
    assert specs["data_ml"]["status"] == "survey_only" and not specs["data_ml"]["testable"]
    assert specs["fullstack"]["status"] == "derived" and not specs["fullstack"]["testable"]
    assert {m["code"] for m in f["modules"]} >= {"python_core", "sql", "react", "qa_testing", "devops_core"}
    emp = make_account(role="employer")
    assert api.get("/api/survey/form", emp, expect=403)["code"] == "role_forbidden"
    api.get("/api/survey/current", None, expect=401)


def test_steps_resume_after_disconnect_and_complete(api, make_account, db_session):
    acc = make_account()
    st = api.get("/api/survey/current", acc)
    assert st["survey_id"] is None and st["step"] == 0 and st["next_step"] == 1

    err = api.put("/api/survey/steps/actions", acc, {"actions": {}}, expect=409)
    assert err["code"] == "survey_step_order" and err["details"]["next_step"] == 1
    st = api.put("/api/survey/steps/industries", acc, {"industries": ["fintech", "edtech", "fintech"]})
    assert st["step"] == 1 and st["next_step"] == 2 and st["answers"]["industries"] == ["fintech", "edtech"]
    st = api.put(
        "/api/survey/steps/actions",
        acc,
        {"actions": {"server_api": "regularly", "sql_db": "sometimes", "queues_cache": "never"}},
    )
    assert st["answers"]["actions"] == {"server_api": "regularly", "sql_db": "sometimes"}
    err = api.put("/api/survey/steps/specialization", acc, {"chosen_spec": "backend_python"}, expect=409)
    assert err["details"]["next_step"] == 3

    # «Обрыв связи»: текущая анкета отдаёт сохранённые ответы и следующий шаг
    st = api.get("/api/survey/current", acc)
    assert st["step"] == 2 and st["next_step"] == 3 and not st["completed"]
    assert st["answers"]["industries"] == ["fintech", "edtech"]

    tech = next(iter(api.get("/api/survey/form", acc)["technologies"]))["code"]
    st = api.put(
        "/api/survey/steps/stack",
        acc,
        {
            "technologies": {tech: "now"},
            "experience_months": 30,
            "roles": ["бэкенд", " "],
            "self_assessment": {"python_core": 3, "sql": 2},
        },
    )
    assert st["answers"]["experience_months"] == 30 and st["answers"]["roles"] == ["бэкенд"]
    hint = api.post("/api/survey/hint", acc, {"chosen_spec": "backend_python"})
    assert hint["chosen"] == "backend_python" and abs(sum(p["probability"] for p in hint["probabilities"]) - 1) < 1e-3
    assert db_session.scalars(select(SurveyResponse)).first().completed_at is None  # подсказка ничего не пишет

    st = api.put("/api/survey/steps/specialization", acc, {"chosen_spec": "backend_python"})
    assert st["completed"] and st["step"] == 4 and st["next_step"] is None and st["number"] == 1
    cls = st["classification"]
    assert cls["chosen"] == "backend_python" and cls["testable"] and cls["waitlist_text"] is None
    assert cls["suggested_grade"]["code"] == "middle"  # подсказка по стажу 30 мес.
    assert _quals(db_session, acc.user_id) == {"backend_python": "SURVEY_DONE"}
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "SURVEY_DONE" and q["can_start"]["ok"]
    assert q["modules_declared"]  # серые чипы «заявлено» из технологий опроса

    # Самооценка и технологии в оценку не входят: квалификация без балла
    assert q["score"] is None and q["grade"] is None


def test_hint_when_answers_point_elsewhere(api, make_account):
    """Ответы фронтендера, выбор — QA: подсказка «ближе к Frontend», но выбор остаётся за кандидатом."""
    acc = make_account()
    api.put("/api/survey/steps/industries", acc, {"industries": []})
    actions = {"ui_layout": "regularly", "spa_components": "regularly", "browser_debug": "regularly"}
    api.put("/api/survey/steps/actions", acc, {"actions": actions})
    api.put(
        "/api/survey/steps/stack",
        acc,
        {"technologies": {"react": "now"}, "experience_months": 12, "roles": [], "self_assessment": {}},
    )
    res = api.post("/api/survey/hint", acc, {"chosen_spec": "data_ml"})
    assert res["top"] == "frontend_js" and res["chosen"] == "data_ml"
    assert res["hint"] is True and "Frontend (JS/TS)" in res["hint_text"]
    assert res["waitlist_text"] == "Тест по этому направлению появится позже. Вы в листе ожидания"
    assert [m["code"] for m in res["declared_modules"]] == ["react"]
    same = api.post("/api/survey/hint", acc, {"chosen_spec": "frontend_js"})
    assert same["hint"] is False and same["hint_text"] is None
    st = api.put("/api/survey/steps/specialization", acc, {"chosen_spec": "data_ml"})
    assert st["classification"]["chosen"] == "data_ml" and st["classification"]["hint"] is True


def test_survey_only_goes_to_waitlist_and_fullstack_opens_both(api, make_account, db_session):
    acc = make_account()
    st = do_survey(api, acc, spec="data_ml")
    assert st["completed"] and st["message"] == "Тест по этому направлению появится позже. Вы в листе ожидания"
    assert st["classification"]["chosen_status"] == "survey_only" and not st["classification"]["testable"]
    assert _quals(db_session, acc.user_id) == {}
    brief = api.get("/api/tests/briefing?specialization=data_ml", acc)
    assert brief["testable"] is False and brief["can_start"]["code"] == "specialization_not_testable"

    fs = make_account()
    st = do_survey(api, fs, spec="fullstack")
    assert st["classification"]["derived_text"] and "меньший из двух" in st["classification"]["derived_text"]
    assert _quals(db_session, fs.user_id) == {"backend_python": "SURVEY_DONE", "frontend_js": "SURVEY_DONE"}


@pytest.mark.parametrize("spec,module", [("qa", "qa_testing"), ("devops", "devops_core")])
def test_qa_and_devops_surveys_open_the_test(api, make_account, db_session, spec, module):
    """QA и DevOps больше не лист ожидания: опрос даёт SURVEY_DONE, брифинг теста доступен, модуль теста — в справочнике."""
    acc = make_account()
    st = do_survey(api, acc, spec=spec)
    assert st["completed"] and st["classification"]["chosen_status"] == "active" and st["classification"]["testable"]
    assert st["classification"]["waitlist_text"] is None
    assert _quals(db_session, acc.user_id) == {spec: "SURVEY_DONE"}
    brief = api.get(f"/api/tests/briefing?specialization={spec}", acc)
    assert brief["testable"] is True and brief["can_start"]["code"] != "specialization_not_testable"
    assert module in {m["code"] for m in api.get("/api/survey/form", acc)["modules"]}


def test_new_version_after_completion_and_history(api, make_account, db_session):
    acc = make_account()
    do_survey(api, acc, months=10)
    # Новая версия начинается с шага 1 и подставляет прошлые ответы
    err = api.put("/api/survey/steps/stack", acc, {"experience_months": 40}, expect=409)
    assert err["code"] == "survey_step_order"
    st = api.put("/api/survey/steps/industries", acc, {"industries": ["gov"]})
    assert st["number"] == 2 and st["step"] == 1 and not st["completed"]
    assert st["answers"]["experience_months"] == 10 and st["answers"]["chosen_spec"] == "backend_python"
    hist = api.get("/api/survey/history", acc)
    assert [h["number"] for h in hist] == [1]
    api.put("/api/survey/steps/actions", acc, {"actions": {"server_api": "regularly"}})
    api.put("/api/survey/steps/stack", acc, {"experience_months": 40})
    st = api.put("/api/survey/steps/specialization", acc, {"chosen_spec": "frontend_js"})
    assert st["number"] == 2 and st["completed"]
    hist = api.get("/api/survey/history", acc)
    assert [(h["number"], h["chosen_spec"], h["experience_months"]) for h in hist] == [
        (2, "frontend_js", 40),
        (1, "backend_python", 10),
    ]
    assert _quals(db_session, acc.user_id) == {"backend_python": "SURVEY_DONE", "frontend_js": "SURVEY_DONE"}
    assert len(db_session.scalars(select(SurveyResponse).where(SurveyResponse.candidate_id == acc.user_id)).all()) == 2


def test_validation_of_codes(api, make_account):
    acc = make_account()
    assert api.put("/api/survey/steps/industries", acc, {"industries": ["moon"]}, expect=400)["code"] == "unknown_industry"
    api.put("/api/survey/steps/industries", acc, {"industries": []})
    assert api.put("/api/survey/steps/actions", acc, {"actions": {"fly": "regularly"}}, expect=400)["code"] == "unknown_action"
    assert api.put("/api/survey/steps/actions", acc, {"actions": {"server_api": "always"}}, expect=422)["code"] == (
        "validation_error"
    )
    api.put("/api/survey/steps/actions", acc, {"actions": {}})
    bad = api.put("/api/survey/steps/stack", acc, {"technologies": {"cobol-x": "now"}, "experience_months": 1}, expect=400)
    assert bad["code"] == "unknown_technology"
    bad = api.put(
        "/api/survey/steps/stack", acc, {"experience_months": 1, "self_assessment": {"python_core": 9}}, expect=400
    )
    assert bad["code"] == "self_assessment_range"
    api.put("/api/survey/steps/stack", acc, {"experience_months": 700}, expect=422)
    api.put("/api/survey/steps/stack", acc, {"experience_months": 5})
    bad = api.put("/api/survey/steps/specialization", acc, {"chosen_spec": "astronaut"}, expect=400)
    assert bad["code"] == "unknown_specialization"
    api.put("/api/survey/steps/specialization", acc, {"chosen_spec": "backend_python", "extra": 1}, expect=422)
