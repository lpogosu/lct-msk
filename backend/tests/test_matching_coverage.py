"""Рекомендация категорий, охват вилки с защитой приватности, «Как мы проверим» и проверочный набор по вакансии."""

import uuid
from types import SimpleNamespace

import pytest

from app.matching.brief_parser import parse_brief, to_brief_spec
from app.matching.coverage import (
    CategoryRecommendation,
    candidate_mins,
    category_cards,
    coarsen,
    family_catalog_from_registry,
    how_we_check,
    module_weights,
    plan_check_set,
    reach_view,
    recommend_categories,
    salary_histogram,
    standard_test_modules,
)
from app.matching.scoring import BriefSpec, CandidateFeatures, TaskResult
from app.services.matching.needs import check_view

DEMO = (
    "Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет. "
    "Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки"
)


@pytest.fixture(scope="module")
def demo():
    return parse_brief(DEMO)


def test_demo_categories(demo):
    recs = recommend_categories(demo.spec_probs.value, demo.grade_probs.value)
    assert 1 <= len(recs) <= 3
    assert (recs[0].spec, recs[0].grade, recs[0].kind) == ("backend_python", "middle", "primary")
    assert (recs[1].spec, recs[1].grade) == ("backend_python", "senior")
    assert all(r.p >= 0.10 for r in recs)
    assert recs[0].name == "Backend (Python) · Middle"
    assert recs[0].p == pytest.approx(
        demo.spec_probs.value["backend_python"] * demo.grade_probs.value["middle"]
    )


def test_adjacent_suggested_when_primary_small(demo):
    counts = {("backend_python", "middle"): 9, ("backend_python", "senior"): 20, ("fullstack", "middle"): 14}
    recs = recommend_categories(demo.spec_probs.value, demo.grade_probs.value, counts=counts)
    adj = [r for r in recs if r.kind == "adjacent"]
    assert len(adj) == 1 and (adj[0].spec, adj[0].grade) == ("fullstack", "middle")
    assert "мало кандидатов (9)" in adj[0].note
    big = recommend_categories(
        demo.spec_probs.value, demo.grade_probs.value, counts={("backend_python", "middle"): 38}
    )
    assert not [r for r in big if r.kind == "adjacent"]


def test_survey_only_category_note():
    recs = recommend_categories(
        {"data_ml": 0.9, "backend_python": 0.1}, {"middle": 1.0}, counts={("data_ml", "middle"): 12}
    )
    assert recs[0].status == "survey_only"
    assert recs[0].note == "Категория пока не тестируется. 12 кандидатов прошли опрос и ждут теста"


def test_devops_and_qa_categories_are_active_without_waitlist_note():
    """DevOps и QA — действующие специализации: в рекомендации нет «пока не тестируется», счётчик — настоящий N."""
    for spec in ("devops", "qa"):
        recs = recommend_categories({spec: 1.0}, {"middle": 1.0}, counts={(spec, "middle"): 12})
        assert recs[0].status == "active" and recs[0].note is None and recs[0].n == 12
    near = recommend_categories({"devops": 1.0}, {"middle": 1.0}, counts={("devops", "middle"): 3, ("devops", "senior"): 20})
    assert [(r.spec, r.grade, r.kind) for r in near if r.kind == "adjacent"] == [("devops", "senior", "adjacent")]


def test_histogram_step_and_reach():
    h = salary_histogram([200_000, 201_000, 210_000, 255_000, 280_000, 281_000], 10_000)
    assert h.bins == ((200_000, 1), (210_000, 2), (260_000, 1), (280_000, 1), (290_000, 1))
    assert h.reach(280_000) == 5 and h.reach(199_000) == 0 and h.n == 6


def test_coarsen_hides_individual_steps():
    h = salary_histogram([200_000, 211_000, 222_000, 233_000, 244_000, 255_000, 266_000], 10_000)
    c = coarsen(h, 3)
    assert all(cnt >= 3 for _, cnt in c.bins)
    assert c.n == h.n


def test_reach_view_privacy_guard():
    few = reach_view([200_000] * 9, 220_000, 280_000)
    assert not few.shown and few.histogram is None and few.points == () and few.p50 is None
    mins = [180_000 + 4_000 * i for i in range(38)]
    rv = reach_view(mins, 220_000, 280_000)
    assert rv.shown and rv.n == 38
    # Прирост охвата между соседними точками ползунка — 0 или ≥ 3 человек.
    deltas = [b.reach - a.reach for a, b in zip(rv.points, rv.points[1:], strict=False)]
    assert all(d == 0 or d >= 3 for d in deltas)
    assert all(p.upper % 10_000 == 0 for p in rv.points)
    assert rv.points[0].upper == 220_000 and rv.points[-1].upper == 440_000
    assert rv.p25 % 10_000 == 0 and rv.p25 <= rv.p50 <= rv.p75


def test_reach_hint_text():
    mins = [250_000 + 2_000 * i for i in range(30)] + [200_000] * 8
    rv = reach_view(mins, 200_000, 240_000)
    assert rv.reach_at_to is not None and rv.reach_at_to < 0.5 * 38
    assert rv.hint.startswith(f"Ваша вилка покрывает {rv.reach_at_to} из 38.")
    assert "При верхе" in rv.hint and "тыс. —" in rv.hint


def test_category_cards_and_net_gross(demo):
    spec = to_brief_spec(demo, "need-c")
    pool = [
        CandidateFeatures(
            f"c{i}",
            "backend_python",
            "middle",
            theta=0.5,
            salary_min=200_000 + 5_000 * i,
            vss_by_discipline={"algo": 50.0} if i % 3 == 0 else {},
            tasks=(TaskResult("t", "B", 0.5, 10),) if i % 2 == 0 else (),
        )
        for i in range(20)
    ]
    rec = CategoryRecommendation(
        "backend_python", "middle", 0.7, "primary", "Backend (Python) · Middle", "active", 20
    )
    (card,) = category_cards([rec], pool, spec)
    assert card.n == 20 and card.reach.shown
    assert card.share_fsp == pytest.approx(7 / 20) and card.share_active == pytest.approx(0.5)
    gross = [CandidateFeatures("g", "backend_python", "middle", salary_min=300_000, salary_net=False)]
    assert candidate_mins(gross, brief_net=True)[0] == pytest.approx(261_000)


def test_how_we_check_demo(demo):
    """Бэкенд-бриф: Python, FastAPI, PostgreSQL — стандартный тест Backend; Docker (nice) в него не входит."""
    cov = how_we_check(demo.must, demo.nice, spec="backend_python")
    by = {i.skill_id: i for i in cov.items}
    assert by["python"].covered and by["python"].modules == (("python_core", "Python"),) and by["python"].via == "standard"
    assert by["postgresql"].text == "PostgreSQL ✓ проверено стандартным тестом категории (модуль «SQL и базы данных»)"
    assert by["fastapi"].via == "standard" and {m for m, _ in by["fastapi"].modules} == {"python_core", "http_api", "fastapi_core"}
    assert by["fastapi"].text == "FastAPI ✓ проверено стандартным тестом категории (модули «Python», «HTTP и API»)"
    assert cov.untestable == ()  # у Kafka есть модуль проверочного набора (mq_core), но nice-навык в набор не попадает
    assert "проверьте на интервью" in by["kafka"].text and by["kafka"].via == "interview"
    # Docker проверяет devops_core, а в blueprint Backend (Python) его нет; nice-навыки в проверочный набор не попадают
    assert "devops_core" not in standard_test_modules("backend_python")
    assert by["docker"].covered and by["docker"].via == "interview"
    assert by["docker"].text == "Docker ✗ в стандартный тест категории не входит — проверьте на интервью"
    assert "через модуль" not in by["docker"].text and "DevOps" not in by["docker"].text
    assert sum(cov.module_weights.values()) == pytest.approx(1.0)


def test_how_we_check_backend_must_docker_goes_to_check_set():
    """Docker в must бэкенд-брифа: стандартный тест его не проверяет, но слот devops_core в проверочном наборе есть."""
    must = ["python", "postgresql", "docker"]
    plan = plan_check_set(must, family_catalog=family_catalog_from_registry(), seed=5)
    assert "devops_core" in plan.modules and "docker" in plan.covered_skills
    by = {i.skill_id: i for i in how_we_check(must, [], spec="backend_python", check_set=plan).items}
    assert by["docker"].via == "check_set"
    assert by["docker"].text == "Docker ✓ проверяется проверочным набором по вакансии (модуль «DevOps и инфраструктура»)"
    assert by["python"].via == "standard"
    # PostgreSQL: стандартный тест видит SQL вообще, особенности PostgreSQL (pg_core) наблюдает слот проверочного набора
    assert by["postgresql"].via == "check_set" and "PostgreSQL (особенности)" in by["postgresql"].text
    # без плана набора честно говорим «на интервью», а не «через модуль»
    alone = {i.skill_id: i for i in how_we_check(must, [], spec="backend_python").items}
    assert alone["docker"].via == "interview" and "проверьте на интервью" in alone["docker"].text


def test_how_we_check_check_set_only_names_skills_that_got_a_slot():
    """Слотов не больше max_families: навык из must, чей модуль в набор не попал, остаётся «на интервью»."""
    must = ["python", "postgresql", "rest", "git", "docker", "react", "html"]
    plan = plan_check_set(must, family_catalog=family_catalog_from_registry(), seed=2)
    by = {i.skill_id: i for i in how_we_check(must, [], spec="backend_python", check_set=plan).items}
    for sid, item in by.items():
        if item.via == "check_set":
            assert sid in plan.covered_skills
        if item.via == "interview":
            assert sid not in plan.covered_skills


def test_standard_test_modules_follow_the_blueprint_and_derived_specs():
    assert standard_test_modules("devops") == {"devops_core", "git_linux", "http_api"}
    assert standard_test_modules("qa") == {"qa_testing", "http_api", "sql", "git_linux"}
    assert standard_test_modules("fullstack") == standard_test_modules("backend_python") | standard_test_modules("frontend_js")
    assert standard_test_modules("mobile") == frozenset()  # опрос есть, теста пока нет
    assert standard_test_modules("no_such_spec") == frozenset()


def test_how_we_check_in_fullstack_brief_uses_both_blueprints():
    by = {i.skill_id: i for i in how_we_check(["python", "react"], ["docker"], spec="fullstack").items}
    assert by["python"].via == by["react"].via == "standard" and by["docker"].via == "interview"


def test_how_we_check_for_a_category_without_test_sends_everything_to_the_interview():
    by = {i.skill_id: i for i in how_we_check(["python"], [], spec="mobile").items}
    assert by["python"].via == "interview" and by["python"].covered


def _need(brief: BriefSpec) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), parsed={"brief": brief.to_dict()})


def test_check_view_is_specialization_aware():
    """Ответ GET /needs/{id}/check: для Backend Docker (nice) — «на интервью», для DevOps — «стандартным тестом»."""
    backend = _need(BriefSpec("n1", "backend_python", "middle", must=("python", "postgresql"), nice=("docker", "kafka")))
    out = check_view(backend)
    via = {c["skill_id"]: c["via"] for c in out["covered"]}
    assert via == {"python": "standard", "postgresql": "check_set", "docker": "interview", "kafka": "interview"}
    assert out["untestable"] == []
    docker = next(c for c in out["covered"] if c["skill_id"] == "docker")
    assert "проверьте на интервью" in docker["text"] and "через модуль" not in docker["text"]

    backend_must = _need(BriefSpec("n2", "backend_python", "middle", must=("python", "docker"), nice=()))
    assert {c["skill_id"]: c["via"] for c in check_view(backend_must)["covered"]} == {"python": "standard", "docker": "check_set"}

    devops = _need(BriefSpec("n3", "devops", "middle", must=("docker", "kubernetes"), nice=("python",)))
    assert {c["skill_id"]: c["via"] for c in check_view(devops)["covered"]} == {
        "docker": "standard",
        "kubernetes": "standard",
        "python": "interview",
    }


def test_module_weights_must_vs_nice():
    w = module_weights(["postgresql"], ["react"])
    assert w["sql"] == pytest.approx(0.5 / 1.4) and w["pg_core"] == pytest.approx(0.5 / 1.4)
    assert w["react"] == pytest.approx(0.2 / 1.4) and w["js_core"] == pytest.approx(0.2 / 1.4)


def test_check_set_plan_modules_only():
    plan = plan_check_set(["python", "fastapi", "postgresql", "kafka"])
    assert 3 <= len(plan.slots) <= 5
    assert plan.modules[0] == "python_core"
    assert set(plan.modules) == {"python_core", "http_api", "sql", "fastapi_core", "pg_core", "mq_core"} or len(plan.slots) == 5
    assert plan.untestable == ()
    assert plan.total_minutes <= 15
    assert all(s.family_id is None for s in plan.slots)


def test_check_set_plan_with_catalog_is_deterministic():
    cat = {
        "python_core": [("py.a", 2.0), ("py.b", 2.5), ("py.c", 1.5)],
        "sql": [("sql.a", 2.0), ("sql.b", 3.0)],
        "http_api": [("http.a", 2.0)],
    }
    p1 = plan_check_set(["python", "postgresql", "rest", "django"], family_catalog=cat, seed=42)
    p2 = plan_check_set(["python", "postgresql", "rest", "django"], family_catalog=cat, seed=42)
    assert p1 == p2
    fams = [s.family_id for s in p1.slots]
    assert len(fams) == len(set(fams)) and None not in fams
    assert 3 <= len(fams) <= 5 and p1.total_minutes <= 15
    for s in p1.slots:
        assert s.family_id in {f for f, _ in cat[s.module]}


def test_check_set_go_vacancy_untestable():
    plan = plan_check_set(["go", "scala"])
    assert plan.slots == () and set(plan.untestable) == {"go", "scala"}
    assert "проверьте на интервью" in plan.note


def test_check_set_kubernetes_is_testable_by_devops_module():
    """Kubernetes проверяет модуль devops_core (семейства ops.k8s.*, ops.res.limits): набор строится, серого списка нет."""
    plan = plan_check_set(["kubernetes", "docker", "nginx"], family_catalog=family_catalog_from_registry(), seed=1)
    assert plan.untestable == () and set(plan.modules) == {"devops_core"}
    assert 3 <= len(plan.slots) <= 5 and all(s.family_id and s.family_id.startswith("ops.") for s in plan.slots)
    mixed = plan_check_set(["go", "kubernetes"])
    assert mixed.untestable == ("go",) and set(mixed.modules) == {"devops_core"}


@pytest.mark.parametrize(
    "n,text",
    [
        (1, "1 кандидат прошёл опрос и ждёт теста"),
        (3, "3 кандидата прошли опрос и ждут теста"),
        (11, "11 кандидатов прошли опрос и ждут теста"),
        (21, "21 кандидат прошёл опрос и ждёт теста"),
    ],
)
def test_survey_only_note_plural(n, text):
    recs = recommend_categories({"data_ml": 1.0}, {"middle": 1.0}, counts={("data_ml", "middle"): n})
    assert recs[0].note.endswith(text)


def test_untestable_line_reads_as_one_sentence(demo):
    by = {i.skill_id: i for i in how_we_check(demo.must, demo.nice, spec="backend_python").items}
    assert by["kafka"].text.startswith("Kafka ✗ ")
    assert by["kafka"].text.count("—") == 1


# --------------------------------------------------------------------------- бриф тестировщика и бриф DevOps

QA_BRIEF = "Ищем тестировщика: pytest, API, SQL, опыт от года, удалёнка, 120–160 тыс."
DEVOPS_BRIEF = "DevOps: Docker, Kubernetes, CI/CD, nginx, Linux, от 3 лет, Москва, 250–320 тыс."


def test_qa_brief_recommends_qa_categories_and_checks_pytest():
    p = parse_brief(QA_BRIEF)
    assert p.top_spec == "qa" and p.spec_probs.value["qa"] > 0.5 and p.question is None
    recs = recommend_categories(p.spec_probs.value, p.grade_probs.value)
    assert (recs[0].spec, recs[0].grade, recs[0].kind, recs[0].status) == ("qa", "junior", "primary", "active")
    assert recs[0].name == "QA / тестирование · Junior" and recs[0].note is None and recs[0].p >= 0.4
    cov = how_we_check(p.must, p.nice, spec="qa")
    by = {i.skill_id: i for i in cov.items}
    assert cov.untestable == ()  # pytest, API и SQL проверяются тестом QA
    assert {m for m, _ in by["pytest"].modules} == {"python_core", "qa_testing"}
    # blueprint QA содержит qa_testing, но не python_core: в тексте — только модуль, который реально входит в тест QA
    assert by["pytest"].via == "standard"
    assert by["pytest"].text == "pytest ✓ проверено стандартным тестом категории (модуль «Тестирование ПО»)"
    assert {i.via for i in cov.items} == {"standard"}
    assert {m for m, _ in by["api_design"].modules} == {"http_api"} and {m for m, _ in by["sql"].modules} == {"sql"}
    assert cov.module_weights["qa_testing"] > 0 and sum(cov.module_weights.values()) == pytest.approx(1.0)
    plan = plan_check_set(p.must, family_catalog=family_catalog_from_registry(), seed=3)
    assert "qa_testing" in plan.modules and plan.untestable == () and plan.total_minutes <= 15
    # чистый Python в must QA-брифа: модуля python_core в тесте QA нет — его закрывает проверочный набор
    py_plan = plan_check_set(["python", "pytest"], family_catalog=family_catalog_from_registry(), seed=3)
    py = {i.skill_id: i for i in how_we_check(["python", "pytest"], [], spec="qa", check_set=py_plan).items}
    assert py["python"].via == "check_set" and py["pytest"].via == "standard"
    assert any(s.family_id and s.family_id.startswith("qa.") for s in plan.slots)


def test_devops_brief_recommends_devops_categories_and_checks_infrastructure():
    p = parse_brief(DEVOPS_BRIEF)
    assert p.top_spec == "devops" and p.spec_probs.value["devops"] > 0.9 and p.question is None
    assert p.must == ("docker", "kubernetes", "ci_cd", "nginx", "linux") and p.top_grade == "middle"
    recs = recommend_categories(p.spec_probs.value, p.grade_probs.value)
    assert (recs[0].spec, recs[0].grade, recs[0].kind, recs[0].status) == ("devops", "middle", "primary", "active")
    assert recs[0].name == "DevOps / SRE · Middle" and recs[0].note is None
    cov = how_we_check(p.must, p.nice, spec="devops")
    assert cov.untestable == () and len(cov.covered) == 5
    by = {i.skill_id: i for i in cov.items}
    for sid in ("docker", "kubernetes", "nginx", "ci_cd"):
        assert by[sid].via == "standard", sid
        assert by[sid].text.endswith("✓ проверено стандартным тестом категории (модуль «DevOps и инфраструктура»)"), sid
    assert {m for m, _ in by["linux"].modules} == {"git_linux", "devops_core"}
    assert by["linux"].text == "Linux ✓ проверено стандартным тестом категории (модули «Git и Linux», «DevOps и инфраструктура»)"
    assert list(cov.module_weights)[0] == "devops_core"
    # cron — тоже проверяется (ops.cron.next_runs); Terraform и Ansible — по-прежнему только на интервью
    extra = how_we_check(["cron", "docker"], ["terraform", "ansible"], spec="devops")
    assert {i.skill_id for i in extra.covered} == {"cron", "docker"}
    assert {i.skill_id for i in extra.untestable} == {"terraform", "ansible"}
    plan = plan_check_set(p.must, family_catalog=family_catalog_from_registry(), seed=3)
    assert "devops_core" in plan.modules and plan.untestable == () and plan.total_minutes <= 15
    assert any(s.family_id and s.family_id.startswith("ops.") for s in plan.slots)
