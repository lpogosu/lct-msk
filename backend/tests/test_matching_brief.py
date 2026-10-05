"""Разбор брифа: демо-бриф §11 (1:35), таблица вилок, must/nice, грейд, уточняющий вопрос, ручные правки."""

import time

import pytest

from app.matching.brief_parser import (
    SOURCE_MANUAL,
    SOURCE_RULE,
    BriefEditError,
    SalaryRange,
    answer_question,
    check_salary,
    parse_brief,
    parse_salary,
    remove_skill,
    set_salary,
    set_skill,
    to_brief_spec,
)

DEMO = (
    "Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет. "
    "Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки"
)


@pytest.fixture(scope="module")
def demo():
    return parse_brief(DEMO)


def test_demo_brief_chips(demo):
    assert demo.must == ("python", "fastapi", "postgresql")
    assert demo.nice == ("kafka", "docker")
    assert demo.top_spec == "backend_python"
    assert demo.spec_probs.value["backend_python"] > 0.9
    assert demo.top_grade == "middle"
    assert demo.grade_probs.value["senior"] > 0.2
    assert [f.value for f in demo.formats] == ["hybrid"]
    assert demo.city.value == "moscow"
    s = demo.salary.value
    assert (s.from_, s.to, s.net) == (220_000, 280_000, True)
    assert demo.salary_check.status == "ok"
    assert demo.question is None
    assert demo.experience_years.value == 2


def test_demo_brief_every_field_has_source_and_quote(demo):
    for chip in demo.skills:
        assert chip.source == SOURCE_RULE
        assert DEMO[chip.quote.start : chip.quote.end] == chip.quote.text
    kafka = demo.chip("kafka")
    assert kafka.marker.text == "будут плюсом"
    assert DEMO[kafka.marker.start : kafka.marker.end] == "будут плюсом"
    assert demo.chip("python").marker.text == "Уверенный"
    for field in (
        demo.salary,
        demo.city,
        *demo.formats,
        demo.spec_probs,
        demo.grade_probs,
        demo.experience_years,
    ):
        assert field.source == SOURCE_RULE
        assert field.quotes
        for q in field.quotes:
            assert DEMO[q.start : q.end] == q.text
    assert [q.text for q in demo.salary.quotes] == ["220–280 тыс.", "на руки"]
    assert demo.city.quote == "Москва"
    assert any(q.text == "опыт от 2 лет" for q in demo.grade_probs.quotes)
    assert any(q.text == "Бэкенд" for q in demo.spec_probs.quotes)


def test_demo_brief_untestable_flags(demo):
    assert demo.chip("kafka").untestable and demo.chip("docker").untestable
    assert not demo.chip("fastapi").untestable


def test_demo_brief_to_spec(demo):
    spec = to_brief_spec(demo, "need-42")
    assert spec.spec == "backend_python" and spec.grade == "middle"
    assert spec.must == ("python", "fastapi", "postgresql") and spec.nice == ("kafka", "docker")
    assert spec.salary_from == 220_000 and spec.salary_to == 280_000 and spec.salary_net
    assert spec.formats == frozenset({"hybrid"}) and spec.city == "moscow"
    assert demo.to_dict()["skills"][0]["quote"]["text"] == "Python"


SALARY_CASES = [
    ("220–280 тыс. на руки", 220_000, 280_000, True),
    ("от 200к", 200_000, None, None),
    ("150 000 – 200 000 ₽ gross", 150_000, 200_000, False),
    ("до 300 тыс. руб.", None, 300_000, None),
    ("200-250k", 200_000, 250_000, None),
    ("от 180 до 250 тысяч рублей до вычета НДФЛ", 180_000, 250_000, False),
    ("1,5–2 млн в год", 125_000, 167_000, None),
    ("зп 120-150", 120_000, 150_000, None),
    ("120.000-150.000 руб", 120_000, 150_000, None),
    ("Вилка: 90 000–130 000 рублей на руки", 90_000, 130_000, True),
    ("оклад 250 000 ₽", 250_000, 250_000, None),
    ("от 150 т.р.", 150_000, None, None),
    ("200к–260к net", 200_000, 260_000, True),
    ("Опыт от 2 лет, зарплата от 200 до 240 тыс.", 200_000, 240_000, None),
    ("300 тысяч до налогов", 300_000, 300_000, False),
    ("з/п 180 — 220 тыс. чистыми", 180_000, 220_000, True),
    ("200к+", 200_000, None, None),
    ("Предлагаем 160 000 – 210 000 руб. в месяц после вычета НДФЛ", 160_000, 210_000, True),
    ("ЗП от 250 000 до 320 000 ₽ (gross)", 250_000, 320_000, False),
]


@pytest.mark.parametrize("text,lo,hi,net", SALARY_CASES, ids=[c[0] for c in SALARY_CASES])
def test_salary_parsing_table(text, lo, hi, net):
    found = parse_salary(text)
    assert found is not None
    rng, quotes = found
    assert (rng.from_, rng.to, rng.net) == (lo, hi, net)
    for q in quotes:
        assert text[q.start : q.end] == q.text


@pytest.mark.parametrize(
    "text",
    [
        "Python 3.11, 5 лет опыта",
        "$5000",
        "сервис на 200 тыс. пользователей",
        "команда из 12 человек",
        "опыт от 3 лет",
    ],
)
def test_salary_not_money(text):
    assert parse_salary(text) is None


@pytest.mark.parametrize(
    "lo,hi,status",
    [
        (220_000, 280_000, "ok"),
        (200_000, 320_000, "ok"),
        (150_000, 270_000, "warn"),
        (150_000, 300_000, "warn"),
        (100_000, 250_000, "blocked"),
        (25_000, 35_000, "invalid"),
        (200_000, 150_000, "invalid"),
        (200_000, None, "incomplete"),
    ],
)
def test_salary_check(lo, hi, status):
    assert check_salary(SalaryRange(lo, hi, True)).status == status


def test_salary_missing_asks_question():
    p = parse_brief("Ищем бэкенд-разработчика на Python и Django, middle.")
    assert p.salary is None and p.salary_check.status == "missing"
    assert p.question is not None and p.question.field == "salary"


def test_salary_form_fields_override_text_as_manual():
    p = parse_brief(DEMO, salary_from=230_000, salary_to=290_000, salary_net=True)
    assert p.salary.source == SOURCE_MANUAL
    assert (p.salary.value.from_, p.salary.value.to) == (230_000, 290_000)


def test_must_overflow_keeps_first_four():
    p = parse_brief("Стек: Python, Django, PostgreSQL, Redis, Celery, Docker, Kafka. Вилка 200–260 тыс.")
    assert p.must == ("python", "django", "postgresql", "redis")
    assert set(p.nice) == {"celery", "docker", "kafka"}
    assert all(p.chip(s).reason == "overflow" for s in ("celery", "docker", "kafka"))


def test_nice_marker_before_list_and_section_header():
    p = parse_brief("Обязательно: Python, SQL. Желательно знание Kafka, Docker, Redis.\nВилка 200–260 тыс.")
    assert p.must == ("python", "sql")
    assert p.nice == ("kafka", "docker", "redis")
    sec = parse_brief("Требования:\nPython\nDjango\n\nБудет плюсом:\nKubernetes\nClickHouse\n\n200–250 тыс.")
    assert sec.must == ("python", "django")
    assert sec.nice == ("kubernetes", "clickhouse")
    assert sec.chip("kubernetes").reason == "section"


def test_post_marker_applies_to_list_and_mixed_clauses():
    p = parse_brief("Kafka, RabbitMQ — будет плюсом. Python обязателен, Django желательно. 200–250 тыс.")
    assert set(p.nice) == {"kafka", "rabbitmq", "django"}
    assert p.must == ("python",)


def test_window_limits_marker_reach():
    p = parse_brief("Будет плюсом опыт в большом и сложном проекте с высокой нагрузкой на Go. 200–250 тыс.")
    assert p.chip("go").kind == "must"  # маркер дальше 6 токенов


def test_slang_inflections_and_typos():
    p = parse_brief(
        "Пишем на питоне, храним в постгресе, деплоим в кубер, фронт на реакте. Знание Kuberentes и Postgress. 200–250 тыс."
    )
    ids = {c.skill_id for c in p.skills}
    assert {"python", "postgresql", "kubernetes", "react"} <= ids
    assert p.chip("kubernetes").method == "dict"


@pytest.mark.parametrize(
    "text,grade",
    [
        ("Ищем Senior Python-разработчика. 350–450 тыс.", "senior"),
        ("Нужен джун на фронтенд, React. 80–110 тыс.", "junior"),
        ("Стажёр в команду аналитики. 50–70 тыс.", "intern"),
        ("Python-разработчик, опыт от 5 лет. 300–400 тыс.", "senior"),
        ("Python-разработчик, опыт от 1 года. 100–140 тыс.", "junior"),
        ("Python-разработчик, 3+ года опыта. 220–280 тыс.", "middle"),
    ],
)
def test_grade_markers_and_experience(text, grade):
    assert parse_brief(text).top_grade == grade


def test_middle_plus_shifts_to_senior():
    p = parse_brief("Ищем Middle+ Python-разработчика. 250–320 тыс.")
    assert p.top_grade == "middle"
    assert p.grade_probs.value["senior"] > 0.3
    assert any(q.text == "Middle+" for q in p.grade_probs.quotes)


@pytest.mark.parametrize(
    "text,spec",
    [
        ("Frontend-разработчик: React, TypeScript, Redux, вёрстка. 180–240 тыс.", "frontend_js"),
        ("Senior Go-разработчик: Go, gRPC, PostgreSQL, Kubernetes. 400–500 тыс.", "backend_go"),
        ("Java-разработчик: Spring Boot, Hibernate, Kafka. 250–320 тыс.", "backend_java"),
        ("DevOps-инженер: Kubernetes, Terraform, Ansible, Prometheus. 250–320 тыс.", "devops"),
        ("Data Scientist: pandas, scikit-learn, PyTorch, SQL. 250–320 тыс.", "data_ml"),
        ("Fullstack-разработчик: React и Node.js, PostgreSQL. 220–280 тыс.", "fullstack"),
    ],
)
def test_specialization_top1(text, spec):
    assert parse_brief(text).top_spec == spec


def test_ambiguous_spec_asks_one_question():
    p = parse_brief("Нужен разработчик: Python и React. 200–260 тыс.")
    assert p.question is not None and p.question.field == "spec"
    codes = [c for c, _ in p.question.options]
    assert "backend_python" in codes and "frontend_js" in codes and "fullstack" in codes
    assert "θ" not in p.question.text
    answered = answer_question(p, "fullstack")
    assert answered.top_spec == "fullstack" and answered.spec_probs.source == SOURCE_MANUAL
    assert answered.question is None or answered.question.field != "spec"


def test_embedding_hook_default_zero_and_pluggable():
    text = "Нужен разработчик: Python и React. 200–260 тыс."
    base = parse_brief(text)
    hooked = parse_brief(text, embed_similarity=lambda _: {"fullstack": 1.0})
    assert hooked.spec_probs.value["fullstack"] > base.spec_probs.value["fullstack"]
    zero = parse_brief(text, embed_similarity=lambda _: {})
    assert zero.spec_probs.value == base.spec_probs.value


def test_manual_edits_mark_source(demo):
    p = set_skill(demo, "kafka", "must")
    assert p.chip("kafka").kind == "must" and p.chip("kafka").source == SOURCE_MANUAL
    assert p.chip("kafka").quote.text == "Kafka"  # цитата сохраняется
    p = set_skill(p, "redis", "nice")
    assert p.chip("redis").source == SOURCE_MANUAL and p.chip("redis").quote is None
    p = remove_skill(p, "docker")
    assert p.chip("docker") is None
    p = set_salary(p, 230_000, 300_000, True)
    assert p.salary.source == SOURCE_MANUAL and p.salary_check.status == "ok"
    # Исходный объект не изменился — правки создают новый.
    assert demo.chip("kafka").kind == "nice"


def test_manual_must_cap(demo):
    p = set_skill(set_skill(demo, "kafka", "must"), "docker", "must")
    with pytest.raises(BriefEditError):
        set_skill(p, "redis", "must")


def test_blocked_salary_cannot_become_spec():
    p = parse_brief("Python-разработчик, 100–300 тыс.")
    assert p.salary_check.status == "blocked"
    with pytest.raises(BriefEditError):
        to_brief_spec(p, "n")


def test_parse_under_300ms_on_long_brief():
    parse_brief(DEMO)  # прогрев словарей
    long_text = (DEMO + " ") * 12
    long_text = long_text[:3000]
    t0 = time.perf_counter()
    parse_brief(long_text)
    assert (time.perf_counter() - t0) < 0.3


def test_parse_is_deterministic():
    assert parse_brief(DEMO).to_dict() == parse_brief(DEMO).to_dict()


# --------------------------------------------------------------------------- регрессии ревью


@pytest.mark.parametrize(
    "text,nice",
    [
        ("Python, FastAPI. Желателен опыт работы с ClickHouse. 220–280 тыс.", "clickhouse"),
        ("Python, FastAPI. Желательна Kafka. 220–280 тыс.", "kafka"),
        ("Python, Django. Хорошо, если знаете Kafka. 220–280 тыс.", "kafka"),
    ],
)
def test_nice_marker_short_forms_and_comma_inside_marker(text, nice):
    p = parse_brief(text)
    assert p.chip(nice).kind == "nice"
    assert "python" in p.must


def test_header_without_marker_closes_nice_section():
    p = parse_brief("Будет плюсом:\n- Redis\nУсловия:\n- офис, команда пишет на Python\n220–280 тыс.")
    assert p.nice == ("redis",)
    assert p.must == ("python",)


@pytest.mark.parametrize(
    "text,grade",
    [
        ("Ищем Senior Python-разработчика, который будет менторить джунов. 350–450 тыс.", "senior"),
        ("Ищем Middle Python-разработчика в команду из двух сеньоров. 250–300 тыс.", "middle"),
        ("Нужен мидл. В команде есть джуны и сеньоры. Python. 250–300 тыс.", "middle"),
    ],
)
def test_grade_markers_about_the_team_do_not_override_the_hire(text, grade):
    p = parse_brief(text)
    assert p.top_grade == grade
    assert p.grade_probs.value[grade] > 0.7


@pytest.mark.parametrize(
    "text,lo,hi",
    [
        ("3 000 000 в год", 250_000, 250_000),
        ("200–250 000 ₽ на руки", 200_000, 250_000),
        ("Вилка 200-250 000", 200_000, 250_000),
    ],
)
def test_salary_yearly_bare_and_dropped_thousands(text, lo, hi):
    rng, quotes = parse_salary(text)
    assert (rng.from_, rng.to) == (lo, hi)
    for q in quotes:
        assert text[q.start : q.end] == q.text


@pytest.mark.parametrize("text", ["10-15 000 пользователей", "1-250 000", "команда 5-7 человек"])
def test_salary_dropped_thousands_not_overreaching(text):
    assert parse_salary(text) is None


def test_salary_step_1000():
    assert check_salary(SalaryRange(220_500, 280_000, True)).status == "invalid"
    assert "1 000" in check_salary(SalaryRange(220_000, 280_500, True)).message
