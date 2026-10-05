"""Рекомендация категорий, охват вилки с защитой приватности, «Как мы проверим» и проверочный набор по вакансии."""

import pytest

from app.matching.brief_parser import parse_brief, to_brief_spec
from app.matching.coverage import (
    CategoryRecommendation,
    candidate_mins,
    category_cards,
    coarsen,
    how_we_check,
    module_weights,
    plan_check_set,
    reach_view,
    recommend_categories,
    salary_histogram,
)
from app.matching.scoring import CandidateFeatures, TaskResult

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
        {"devops": 0.9, "backend_python": 0.1}, {"middle": 1.0}, counts={("devops", "middle"): 12}
    )
    assert recs[0].status == "survey_only"
    assert recs[0].note == "Категория пока не тестируется. 12 кандидатов прошли опрос и ждут теста"


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
    cov = how_we_check(demo.must, demo.nice)
    by = {i.skill_id: i for i in cov.items}
    assert by["python"].covered and by["python"].modules == (("python_core", "Python"),)
    assert by["postgresql"].text == "PostgreSQL ✓ через модуль «SQL и базы данных»"
    assert by["fastapi"].covered and {m for m, _ in by["fastapi"].modules} == {"python_core", "http_api"}
    gray = {i.skill_id for i in cov.untestable}
    assert gray == {"kafka", "docker"}
    assert "проверьте на интервью" in by["kafka"].text
    assert sum(cov.module_weights.values()) == pytest.approx(1.0)


def test_module_weights_must_vs_nice():
    w = module_weights(["postgresql"], ["react"])
    assert w["sql"] == pytest.approx(1.0 / 1.4)
    assert w["react"] == pytest.approx(0.2 / 1.4) and w["js_core"] == pytest.approx(0.2 / 1.4)


def test_check_set_plan_modules_only():
    plan = plan_check_set(["python", "fastapi", "postgresql", "kafka"])
    assert 3 <= len(plan.slots) <= 5
    assert plan.modules[0] == "python_core"
    assert set(plan.modules) == {"python_core", "http_api", "sql"}
    assert plan.untestable == ("kafka",)
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
    plan = plan_check_set(["go", "kubernetes"])
    assert plan.slots == () and set(plan.untestable) == {"go", "kubernetes"}
    assert "проверьте на интервью" in plan.note


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
    recs = recommend_categories({"devops": 1.0}, {"middle": 1.0}, counts={("devops", "middle"): n})
    assert recs[0].note.endswith(text)


def test_untestable_line_reads_as_one_sentence(demo):
    by = {i.skill_id: i for i in how_we_check(demo.must, demo.nice).items}
    assert by["docker"].text.startswith("Docker ✗ ")
    assert by["docker"].text.count("—") == 1
