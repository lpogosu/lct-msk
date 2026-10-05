"""Словарь навыков (config/skills.yaml) и поиск с цитатами: размер, граф «навык → модули», сленг, границы слов."""

import pytest

from app.core.configs import load_config
from app.matching.skills import PhraseMatcher, get_catalog, normalize_text

CAT = get_catalog()


def test_catalog_valid_and_sized():
    assert CAT.errors == []
    assert 300 <= len(CAT.skills) <= 400
    assert len(CAT.roles) >= 15
    assert len(CAT.soft_skills) >= 40
    assert len(CAT.cities) >= 40
    assert set(CAT.formats) == {"office", "hybrid", "remote"}


def test_modules_exist_and_untestable_flag_consistent():
    modules = set(load_config("taxonomy")["modules"])
    for s in CAT.skills.values():
        assert set(s.modules) <= modules, s.id
        assert s.untestable == (not s.modules), s.id
        if s.untestable:
            assert s.untestable_reason
            assert "интервью" in s.untestable_reason or "портфолио" in s.untestable_reason


def test_skill_module_graph_examples():
    assert {"python_core", "sql"} <= set(CAT.modules_of("django"))
    assert set(CAT.modules_of("query_optimization")) == {"sql", "algo"}
    assert CAT.modules_of("postgresql") == ("sql",)
    assert CAT.modules_of("react") == ("react", "js_core")
    for sid in ("docker", "kafka", "kubernetes", "go", "redis"):
        assert CAT.skill(sid).untestable


@pytest.mark.parametrize(
    "text,skill",
    [
        ("питон", "python"),
        ("на питоне", "python"),
        ("постгрес", "postgresql"),
        ("в постгресе", "postgresql"),
        ("реакт", "react"),
        ("на реакте", "react"),
        ("k8s", "kubernetes"),
        ("кубер", "kubernetes"),
        ("очереди на кафке", "kafka"),
        ("кафкой", "kafka"),
        ("Node.js", "nodejs"),
        ("C#", "csharp"),
        ("C++", "cpp"),
        (".NET", "dotnet"),
        ("1С", "onec"),
        ("джанго", "django"),
        ("вёрстка", "layout"),
        ("оптимизация запросов", "query_optimization"),
        ("Postgress", "postgresql"),
        ("Kuberentes", "kubernetes"),
    ],
)
def test_slang_and_forms(text, skill):
    ids = [m.id for m in CAT.find_skills(text)]
    assert skill in ids, ids


@pytest.mark.parametrize(
    "text,absent",
    [
        ("PostgreSQL", "sql"),
        ("JavaScript", "java"),
        ("React Native", "react"),
        ("GitLab CI", "gitlab"),
        ("растет нагрузка", "rust"),
        ("middleware", "python"),
    ],
)
def test_word_boundaries_and_longest_match(text, absent):
    assert absent not in [m.id for m in CAT.find_skills(text)]


def test_spans_point_into_original_text():
    text = "Опыт с  «Python» и  ПОСТГРЕСОМ,\nа также с Kubernetes (k8s) и Ёлкой"
    matches = CAT.find_skills(text)
    assert [m.id for m in matches] == ["python", "postgresql", "kubernetes", "kubernetes"]
    for m in matches:
        assert text[m.start : m.end] == m.quote
    assert matches[1].quote == "ПОСТГРЕСОМ"


def test_normalize_keeps_offsets():
    nt = normalize_text("  Ёжик  в — тумане\n\n\nПривет")
    assert nt.norm == "ежик в - тумане\n\nпривет"
    for i, ch in enumerate(nt.norm):
        if ch not in " \n":
            assert nt.original[nt.index[i]].lower().replace("ё", "е").replace("—", "-") == ch


def test_other_dictionaries():
    text = "Нужен тимлид. Работа в офисе в Санкт-Петербурге или удалёнка, помощь с переездом. Коммуникабельность и стрессоустойчивость."
    assert "team_lead" in [m.id for m in CAT.find_roles(text)]
    assert [m.id for m in CAT.find_cities(text)] == ["spb"]
    assert [m.id for m in CAT.find_formats(text)] == ["office", "remote"]
    assert CAT.find_relocation(text)
    assert {"communication", "stress_resistance"} <= {m.id for m in CAT.find_soft_skills(text)}
    assert [m.id for m in CAT.find_cities("в Перми и Казани")] == ["perm", "kazan"]
    assert [m.id for m in CAT.find_formats("частично удалённо")] == ["hybrid"]


def test_markers_and_negation():
    ids = [m.id for m in CAT.find_markers("необязательно, но желательно; обязательно знание")]
    assert ids == ["nice", "nice", "must", "must"]
    assert [m.id for m in CAT.find_markers("не обязательно")] == ["nice"]


def test_phrase_matcher_generic():
    pm = PhraseMatcher([("кафка", "k"), ("go", "g")], min_stem_len=5)
    assert [m.id for m in pm.find("на кафке и go")] == ["k", "g"]
    assert pm.find("google") == []


@pytest.mark.parametrize(
    "text,absent",
    [
        ("Гибкий график, офис у метро", "graphs"),
        ("работа по графику 5/2", "graphs"),
        ("компьютерная графика", "graphs"),
    ],
)
def test_everyday_words_are_not_skills(text, absent):
    assert absent not in [m.id for m in CAT.find_skills(text)]


def test_graph_skill_still_found():
    assert "graphs" in [m.id for m in CAT.find_skills("алгоритмы на графах, обход графа")]
    assert "graphs" in [m.id for m in CAT.find_skills("задачи на теорию графов")]
