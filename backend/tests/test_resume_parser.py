"""Разбор резюме (концепция §3.1.1): три синтетических резюме (tests/fixtures/resumes, генератор там же),
каждое поле — с цитатой из извлечённого текста; скан без текста — понятная ошибка; навыки не ловятся
в контактах; стаж — объединение периодов без двойного счёта."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.resume import ResumeError, parse_resume
from app.services.resume.dates import find_periods, merged_months
from app.services.resume.names import find_full_name
from app.services.resume.parser import dictionary_text

FIX = Path(__file__).parent / "fixtures" / "resumes"
NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def _parse(name: str):
    return parse_resume((FIX / name).read_bytes(), now=NOW)


def _check_quote(text: str, field) -> None:
    q = field.quote
    assert q is not None
    assert text[q.start : q.end] == q.text, (q, text[q.start : q.end])
    assert q.page is not None and q.page >= 1


def test_backend_middle_all_fields_with_quotes():
    ext, p = _parse("backend_middle.pdf")
    text = ext.text
    assert p.full_name.value == "Кортежев Денис Павлович"
    assert (p.full_name.parts.last, p.full_name.parts.first, p.full_name.parts.middle) == ("Кортежев", "Денис", "Павлович")
    assert p.email.value == "d.kortezhev@example.com"
    assert p.phone.value == "+79152041873" and p.phone.quote.text == "+7 (915) 204-18-73"
    assert p.telegram.value == "@kortezhev_dev"
    assert p.city.value == "moscow" and p.city.label == "Москва"
    assert p.declared_grade.value == "middle"
    assert "Северо-Уральский университет" in p.education.value
    for f in (p.full_name, p.email, p.phone, p.telegram, p.city, p.declared_grade, p.education):
        _check_quote(text, f)
    skills = {s.id for s in p.skills}
    assert {"python", "fastapi", "django", "postgresql", "redis", "kafka", "docker", "pytest", "celery"} <= skills
    for s in p.skills + p.roles + p.soft_skills:
        _check_quote(text, s)
        assert s.source == "rules"
    assert {r.id for r in p.roles} >= {"python_dev", "backend_dev"}
    assert {s.id for s in p.soft_skills} >= {"responsibility", "teamwork", "analytical_thinking", "mentoring"}
    # Сентябрь 2020 — Май 2021 (9) и Март 2021 — н. в. (68): пересечение март–май 2021 считается один раз
    assert p.experience.value == 74 and p.experience.overlap_months == 3
    periods = {(x.start, x.end) for x in p.experience.periods}
    assert periods == {("2020-09", "2021-05"), ("2021-03", None)}
    current = next(x for x in p.experience.periods if x.end is None)
    assert current.company == "ООО «Северный код»" and current.title == "Backend-разработчик"
    assert p.warnings == [] and p.llm.used is False


def test_frontend_junior_name_first_and_contacts_section():
    ext, p = _parse("frontend_junior.pdf")
    assert p.full_name.value == "Компонентова Алиса"  # «Имя Фамилия» → фамилия первой
    assert p.full_name.quote.text == "Алиса Компонентова"
    assert p.email.value == "alisa.komponentova@example.org"
    assert p.phone.value == "+79215550147"  # «8 921 …» → +7
    assert p.telegram.value == "@alisa_komponent" and p.telegram.quote.text == "t.me/alisa_komponent"
    assert p.city.value == "spb"
    assert p.declared_grade.value == "junior"
    assert {"javascript", "typescript", "react", "redux", "html", "css", "jest", "webpack"} <= {s.id for s in p.skills}
    assert {"learning", "attention_to_detail"} <= {s.id for s in p.soft_skills}
    # 06.2022–08.2022 (3) + 03.2023 – по н.в. (44)
    assert p.experience.value == 47 and p.experience.overlap_months == 0
    for f in (p.full_name, p.email, p.phone, p.telegram):
        _check_quote(ext.text, f)


def test_fullstack_two_pages_labels_years_and_no_contact_false_skills():
    ext, p = _parse("fullstack_senior.pdf")
    assert ext.pages == 2
    assert p.full_name.value == "Шаблонов Михаил Андреевич"  # строка «ФИО: …»
    assert p.declared_grade.value == "senior"
    assert p.city.value == "kazan"
    assert p.phone.value == "+79035550199"
    skills = {s.id for s in p.skills}
    assert {"python", "go", "react", "typescript", "kubernetes", "clickhouse", "kafka", "drf"} <= skills
    # «….net» в адресе почты и подпись ссылки «GitHub:» — не навыки
    assert "dotnet" not in skills and "github" not in skills
    # Результаты со второй страницы — с номером страницы в цитате
    tl = next(x for x in p.experience.periods if x.end is None)
    assert tl.quote.page == 2 and tl.title == "Тимлид"
    # Годы без месяцев: 2016–2019, с 2019 по 2021, январь 2021 — сейчас
    assert p.experience.value == 130 and p.experience.overlap_months == 24
    assert {r.id for r in p.roles} >= {"fullstack_dev", "team_lead"}


def test_scan_without_text_is_a_clear_russian_error():
    with pytest.raises(ResumeError) as e:
        _parse("scan_no_text.pdf")
    assert e.value.code == "resume_no_text"
    assert "скан" in e.value.message and "вручную" in e.value.message


def test_not_a_pdf_and_broken_pdf():
    with pytest.raises(ResumeError) as e:
        parse_resume(b"PK\x03\x04 docx archive", now=NOW)
    assert e.value.code == "resume_not_pdf"
    with pytest.raises(ResumeError) as e:
        parse_resume(b"%PDF-1.4\n%garbage without objects", now=NOW)
    assert e.value.code in ("resume_broken", "resume_no_text")


def test_dictionary_text_masks_contacts_and_link_labels_keeping_offsets():
    text = "GitHub: github.com/dev-x\nПочта: a.b@site.net\nPython, Go"
    masked = dictionary_text(text)
    assert len(masked) == len(text)
    assert "GitHub" not in masked and ".net" not in masked
    assert masked.endswith("Python, Go")


def test_periods_overlap_and_present():
    text = "Январь 2020 — Декабрь 2021\n06.2021 – по н.в.\nс 2015 по 2016"
    periods = find_periods(text, NOW)
    assert [(p.start % 12 + 1, p.start // 12) for p in periods] == [(1, 2015), (1, 2020), (6, 2021)]
    total, overlap = merged_months(periods)
    # 2015-01..2016-12 (24) + 2020-01..2026-10 (82) = 106; пересечение 2021-06..2021-12 = 7
    assert (total, overlap) == (106, 7)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Резюме\nБитмапов Пётр Сергеевич\nМосква", "Битмапов Пётр Сергеевич"),
        ("Мария Кэшева\nFrontend", "Кэшева Мария"),
        ("ФИО: Логгерова Анна Игоревна", "Логгерова Анна Игоревна"),
        ("Python-разработчик\nМосква", None),
    ],
)
def test_full_name_heuristics(header, expected):
    m = find_full_name(header, len(header))
    assert (m.value if m else None) == expected
