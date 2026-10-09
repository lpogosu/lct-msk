"""Адаптер простого формата жюри (app/services/demo/jury_simple.py): вакансии, кандидаты, разметка в CSV или JSON →
строки импорта формата eval/pairs_test.jsonl. Ошибки — по-русски, с таблицей, номером строки и полем."""

from __future__ import annotations

import json

import pytest

from app.core.errors import ApiError
from app.services.demo import jury_admin as svc
from app.services.demo import jury_simple as js

VAC = "id,text,salary_from,salary_to,salary_net\nv1,Нужен Middle Python-разработчик: Python и Django. Гибрид.,220000,280000,true\n"
CAND = (
    "id,spec,grade,skills,experience_months,self_description\n"
    "c1,backend_python,middle,python;django,40,Пишу на Django\n"
    "c2,qa,junior,pytest,12,Тестировщик\n"
)
LAB = "vacancy_id,candidate_id,rel\nv1,c1,3\n"


def templates() -> tuple[str, str, str]:
    d = js.TEMPLATES_DIR
    return tuple((d / f).read_text(encoding="utf-8") for f in ("vacancies.csv", "candidates.csv", "labels.csv"))  # type: ignore[return-value]


def err(*args, **kw) -> ApiError:
    with pytest.raises(ApiError) as e:
        js.adapt(*args, **kw)
    assert e.value.status_code == 400
    return e.value


def test_templates_csv_and_json_give_the_same_pairs():
    out = js.adapt(*templates())
    ex = json.loads((js.TEMPLATES_DIR / "example.json").read_text(encoding="utf-8"))
    assert js.adapt(ex["vacancies"], ex["candidates"], ex["labels"])["pairs"] == out["pairs"]
    st = out["stats"]
    assert st == {"vacancies": 3, "candidates": 8, "labels": 9, "labeled_vacancies": 3, "pairs_added": 3 * 8 - 9}
    assert len(out["pairs"]) == 3 * 8  # у размеченных вакансий ранжируются все кандидаты набора
    for row in out["pairs"]:
        svc.validate_pair(row, 0)  # строки — ровно формат eval/pairs_test.jsonl
        cand = row["candidate"]
        assert cand["features"] is None  # теста нет → группа «категория по опросу, не подтверждена»
        f = svc.candidate_features(row["candidate_id"], cand)
        assert f is not None and not f.confirmed
    c01 = next(r["candidate"] for r in out["pairs"] if r["candidate_id"] == "c01")
    assert c01["declared_skills"] == ["python", "django", "postgresql", "celery", "docker"]
    assert c01["vss_by_discipline"] == {"product": 62.0} and c01["city"] == "moscow" and c01["formats"] == ["hybrid", "remote"]
    assert svc.candidate_features("c01", c01).has_fsp  # история ФСП из таблицы попадает в F
    # v3 — вилка только в тексте («до вычета НДФЛ»): форма пустая, разбор текста её находит
    v3 = next(b for b in out["briefs"] if b["brief_id"] == "v3")
    assert v3["form"] is None
    spec, *_ = svc.brief_to_spec("v3", v3)
    assert (spec.salary_from, spec.salary_to, spec.salary_net) == (180000, 230000, False)


def test_semicolon_delimiter_and_russian_names():
    vac = "id;text;salary_from;salary_to;salary_net\nv1;Ищем тестировщика: pytest, SQL;150 000;200 000;нет\n"
    cand = "id;spec;grade;skills\nq1;QA / тестирование;Стажёр;\"pytest;Постгрес;SQL\"\n"
    out = js.adapt(vac, cand, None)
    assert out["pairs"] == [] and out["stats"]["labels"] == 0
    c = out["candidates"][0]["candidate"]
    assert (c["claimed_spec"], c["claimed_grade"]) == ("qa", "intern")
    assert c["declared_skills"] == ["pytest", "postgresql", "sql"]
    assert out["briefs"][0]["form"] == {"salary_from": 150000, "salary_to": 200000, "salary_net": False}


def test_unlabeled_pairs_option():
    on = js.adapt(VAC, CAND, LAB)
    assert {(r["candidate_id"], r["rel"]) for r in on["pairs"]} == {("c1", 3), ("c2", 0)}
    assert "rel = 0" in on["warnings"][0]
    off = js.adapt(VAC, CAND, LAB, unlabeled_as_irrelevant=False)
    assert [(r["candidate_id"], r["rel"]) for r in off["pairs"]] == [("c1", 3)] and off["stats"]["pairs_added"] == 0


def test_unknown_skill_is_a_warning_not_an_error():
    cand = "id,spec,grade,skills\nc1,backend_python,junior,Python 3;квантовая левитация;квантовый-блокчейн\n"
    out = js.adapt(VAC, cand, None)
    assert out["candidates"][0]["candidate"]["declared_skills"] == ["python"]
    for token in ("квантовая левитация", "квантовый-блокчейн"):  # частичное совпадение со справочником — тоже не навык
        assert any("Кандидаты, строка 2, поле «skills»" in w and token in w for w in out["warnings"]), token


@pytest.mark.parametrize(
    ("vac", "cand", "lab", "needle", "table", "line", "field"),
    [
        # Вакансии
        ("id,text\nv1,Python-разработчик\n", "", "", "Вакансии, строка 2, поле «salary_from»", "vacancies", 2, "salary_from"),
        (VAC + "v2,Тестировщик,200000,150000,true\n", "", "", "Вакансии, строка 3, поле «salary_to»", "vacancies", 3,
         "salary_to"),
        (VAC + "v2,Тестировщик,200000,,true\n", "", "", "обе границы вилки", "vacancies", 3, "salary_to"),
        (VAC + "v2,Тестировщик,сто тысяч,200000,true\n", "", "", "поле «salary_from»: ожидается число", "vacancies", 3,
         "salary_from"),
        (VAC + "v1,Ещё раз,100000,150000,\n", "", "", "уже есть в строке 2", "vacancies", 3, "id"),
        (VAC.replace(",true", ",может быть"), "", "", "поле «salary_net»", "vacancies", 2, "salary_net"),
        ("id,salary_from\nv1,1\n", "", "", "в заголовке нет столбцов: text", "vacancies", None, None),
        # Кандидаты
        (VAC, "id,spec,grade\nc1,backend_python,wizard\n", "", "Кандидаты, строка 2, поле «grade»", "candidates", 2, "grade"),
        (VAC, "id,spec,grade\nc1,backend_cobol,middle\n", "", "поле «spec»", "candidates", 2, "spec"),
        (VAC, CAND + "c3,qa,junior,,-5\n", "", "Кандидаты, строка 4, поле «experience_months»", "candidates", 4,
         "experience_months"),
        (VAC, "id,spec,grade,fsp\nc1,qa,junior,chess:50\n", "", "дисциплина «chess»", "candidates", 2, "fsp"),
        (VAC, "id,spec,grade,fsp\nc1,qa,junior,algo:150\n", "", "от 0 до 100", "candidates", 2, "fsp"),
        (VAC, "id,spec,grade,formats\nc1,qa,junior,на луне\n", "", "поле «formats»", "candidates", 2, "formats"),
        (VAC, "id,spec,grade,salary\nc1,qa,junior,1\n", "", "неизвестный столбец", "candidates", 2, "salary"),
        (VAC, "id,spec,grade\n,qa,junior\n", "", "обязательное поле не заполнено", "candidates", 2, "id"),
        # Разметка
        (VAC, CAND, "vacancy_id,candidate_id,rel\nv1,c1,5\n", "Разметка, строка 2, поле «rel»: число от 0 до 3", "labels", 2,
         "rel"),
        (VAC, CAND, "vacancy_id,candidate_id,rel\nv1,c1,2\nv9,c1,1\n", "вакансии «v9» нет", "labels", 3, "vacancy_id"),
        (VAC, CAND, "vacancy_id,candidate_id,rel\nv1,c9,1\n", "кандидата «c9» нет", "labels", 2, "candidate_id"),
        (VAC, CAND, "vacancy_id,candidate_id,rel\nv1,c1,1\nv1,c1,2\n", "уже размечена в строке 2", "labels", 3, None),
        (VAC, CAND, "vacancy_id,candidate_id,rel\nv1,c1,1.5\n", "целое число", "labels", 2, "rel"),
    ],
)
def test_errors_name_table_line_and_field(vac, cand, lab, needle, table, line, field):
    e = err(vac, cand or None, lab or None)
    assert e.code == "import_invalid" and needle in e.message, e.message
    assert e.details == {"table": table, "line": line, "field": field}


def test_line_numbers_count_multiline_quoted_text():
    vac = 'id,text,salary_from,salary_to\nv1,"Первая строка\nвторая строка",100000,150000\nv2,Тест,200000,100000\n'
    e = err(vac, None, None)
    assert "Вакансии, строка 4" in e.message  # v2 начинается на 4-й строке файла


def test_json_records_are_numbered_and_lists_accepted():
    vac = [{"id": "v1", "text": "Python, Django", "salary_from": 200000, "salary_to": 250000, "salary_net": True}]
    cand = [{"id": "c1", "spec": "backend_python", "grade": "middle", "skills": ["python", "Django"], "fsp": 70},
            {"id": "c2", "spec": "backend_python", "grade": "senior", "experience_months": "много"}]
    e = err(vac, cand, None)
    assert e.message.startswith("Кандидаты, запись 2, поле «experience_months»")
    out = js.adapt(vac, cand[:1], [{"vacancy_id": "v1", "candidate_id": "c1", "rel": 2}])
    c = out["pairs"][0]["candidate"]
    assert c["declared_skills"] == ["python", "django"] and c["vss_by_discipline"] == {"product": 70.0}
    e = err(vac, ["не объект"], None)
    assert "Кандидаты, запись 1" in e.message


def test_empty_and_limits(monkeypatch):
    e = err(None, None, None)
    assert e.code == "import_empty"
    from app.services.demo.cfg import demo_cfg

    monkeypatch.setitem(demo_cfg()["admin"]["simple"], "max_labels", 1)
    e = err(VAC, CAND, "vacancy_id,candidate_id,rel\nv1,c1,1\nv1,c2,0\n")
    assert e.code == "import_too_large" and "Разметка" in e.message
    monkeypatch.setitem(demo_cfg()["admin"]["simple"], "max_labels", 100)
    monkeypatch.setitem(demo_cfg()["admin"]["import"], "max_pairs", 1)
    e = err(VAC, CAND, LAB)  # после дополнения нулями пар стало 2
    assert e.code == "import_too_large" and "Пар «вакансия × кандидат» получилось 2" in e.message
