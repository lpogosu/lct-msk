"""Стандартизированный PDF-профиль (концепция §3.1.2): слепая и полная версии, QR ведёт на /v/{код},
в слепой нет имени и контактов, код сверки связывает содержимое и дату, работодатель получает полную
версию только после раскрытия и только с разрешёнными полями."""

from __future__ import annotations

import io
import uuid
from datetime import timedelta

import pdfplumber
import pytest

from app.models.candidate import ProfileSnapshot
from app.models.contact import ContactDisclosure
from app.models.testing import Qualification
from app.models.users import User
from tests.fixtures.profile_support import confirm_qualification
from tests.fixtures.qr_decode import decode_matrix, matrix_from_rects

NAME = "Дебагова Мария Олеговна"


def _candidate(client, make_account, db_session, now):
    acc = make_account()
    r = client.put(
        "/api/candidates/me",
        json={
            "full_name": NAME,
            "contact_email": "maria.debug@example.com",
            "phone": "+7 915 204-18-73",
            "telegram": "@maria_debug",
            "city": "Казань",
            "current_employer": "ООО «Тихая гавань софт»",
            "education": "Казанский университет информационных технологий",
            "skills": ["FastAPI", "Kafka", "Docker"],
            "roles": ["backend_dev"],
            "declared_grade": "middle",
            "experience": [{"start": "2021-03", "title": "Backend-разработчик", "company": "ООО «Тихая гавань софт»"}],
        },
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    session_id = confirm_qualification(db_session, acc.user_id, now=now)
    return acc, session_id


def _pdf(client, acc, variant: str):
    r = client.get("/api/candidates/me/profile.pdf", params={"variant": variant}, headers=acc.headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    return r


def _text_and_qr(content: bytes) -> tuple[str, str, list]:
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        page = pdf.pages[0]
        # QR — справа внизу первой страницы (под линией футера)
        bbox = (page.width - 40 - 80, page.height - 40 - 82, page.width - 38, page.height - 38)
        matrix = matrix_from_rects(page.rects, bbox=bbox)
        url = decode_matrix(matrix).decode("utf-8")
        links = [h.get("uri") for h in page.hyperlinks]
    return text, url, links


def test_blind_and_full_pdf_qr_verify_and_no_pii_in_blind(client, make_account, db_session, frozen_clock):
    acc, session_id = _candidate(client, make_account, db_session, frozen_clock.now())

    blind = _pdf(client, acc, "blind")
    code = blind.headers["x-verify-code"]
    assert len(code) == 32
    text, qr_url, links = _text_and_qr(blind.content)
    assert qr_url == f"http://localhost:5173/v/{code}"
    assert qr_url in links  # та же ссылка кликабельна под QR
    assert "Backend (Python) · Middle" in text
    assert "Слепая версия" in text and "Республика Татарстан" in text
    for secret in ("Дебагова", "Мария", "maria.debug@example.com", "+79152041873", "maria_debug", "Казань", "Тихая гавань", "университет"):
        assert secret not in text, secret
    assert "542" not in text  # точный балл — только в полной версии
    assert "80%: 510–570" in text  # интервал на линейке
    assert "FastAPI" in text and "Заявлено кандидатом" in text
    assert "Достижения ФСП не привязаны" in text  # без ФСП — нейтрально, не штраф

    full = _pdf(client, acc, "full")
    full_code = full.headers["x-verify-code"]
    assert full_code != code
    text, qr_url, _ = _text_and_qr(full.content)
    assert qr_url.endswith(full_code)
    for shown in (NAME, "maria.debug@example.com", "+79152041873", "@maria_debug", "Казань", "542", "Тихая гавань"):
        assert shown in text, shown
    assert f"#{session_id.hex[:8].upper()}" in text

    # Сверка: публичная страница без ПДн
    r = client.get(f"/api/v/{code}")
    assert r.status_code == 200
    v = r.json()
    assert v["valid"] is True and v["variant"] == "blind" and v["actual"] is True
    assert v["category"]["name"] == "Backend (Python) · Middle"
    assert v["session_ref"] == f"#{session_id.hex[:8].upper()}"
    assert v["confirmed_on"] == (frozen_clock.now() - timedelta(days=21)).date().isoformat()
    assert v["score"] is None and v["interval"] == [510, 570]
    assert v["holder"]["pseudonym"].startswith("К-")
    assert "подтверждена тестом" in v["message"] and "ФСП не привязаны" in v["message"]
    for secret in ("Дебагова", "maria", "Казань", "+7"):
        assert secret not in r.text
    v_full = client.get(f"/api/v/{full_code}").json()
    assert v_full["valid"] and v_full["score"] == 542 and v_full["holder"] == {"initials": "Д. М. О."}


def test_code_is_deterministic_per_day_and_changes_with_content(client, make_account, db_session, frozen_clock):
    acc, _ = _candidate(client, make_account, db_session, frozen_clock.now())
    c1 = _pdf(client, acc, "blind").headers["x-verify-code"]
    c2 = _pdf(client, acc, "blind").headers["x-verify-code"]
    assert c1 == c2
    assert db_session.query(ProfileSnapshot).filter_by(user_id=acc.user_id).count() == 1
    info = client.get("/api/candidates/me/profile-pdf", params={"variant": "blind"}, headers=acc.headers).json()
    assert info["code"] == c1 and info["verify_url"].endswith(c1) and "ФИО" in info["hidden"]
    client.put("/api/candidates/me", json={"skills": ["FastAPI"]}, headers=acc.headers)
    c3 = _pdf(client, acc, "blind").headers["x-verify-code"]
    assert c3 != c1
    frozen_clock.advance(86400)
    c4 = _pdf(client, acc, "blind").headers["x-verify-code"]
    assert c4 != c3  # новая дата выдачи — новый код


def test_verify_unknown_tampered_and_outdated(client, make_account, db_session, frozen_clock):
    acc, _ = _candidate(client, make_account, db_session, frozen_clock.now())
    code = _pdf(client, acc, "blind").headers["x-verify-code"]
    tampered = ("0" if code[0] != "0" else "1") + code[1:]
    for bad in (tampered, "not-a-code", "z" * 32):
        v = client.get(f"/api/v/{bad}").json()
        assert v["valid"] is False and "не выдавала" in v["message"]
    # Категория с тех пор изменилась (квалификации больше нет) — документ подлинный, но устарел
    db_session.query(Qualification).filter_by(candidate_id=acc.user_id).delete()
    db_session.commit()
    v = client.get(f"/api/v/{code}").json()
    assert v["valid"] is True and v["actual"] is False and "изменилась" in v["actual_note"]


def test_pdf_without_category_is_still_issued(client, make_account, frozen_clock):
    acc = make_account()
    r = client.get("/api/candidates/me/profile.pdf", params={"variant": "full"}, headers=acc.headers)
    assert r.status_code == 200
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = pdf.pages[0].extract_text()
    assert "Категория пока не подтверждена тестом" in text
    assert client.get("/api/candidates/me/profile.pdf", params={"variant": "secret"}, headers=acc.headers).status_code == 422


def test_employer_gets_full_pdf_only_after_disclosure_and_only_allowed_fields(client, make_account, db_session, frozen_clock):
    matching = pytest.importorskip("app.services.matching")
    acc, _ = _candidate(client, make_account, db_session, frozen_clock.now())
    emp = make_account(email="hr@severny-kod.ru", role="employer")
    company_id = db_session.get(User, emp.user_id).company_id
    alias = matching.get_alias(db_session, company_id, acc.user_id)
    db_session.commit()
    url = f"/api/candidates/disclosed/{alias.ref}/profile.pdf"
    r = client.get(url, headers=emp.headers)
    assert r.status_code == 404 and r.json()["code"] == "profile_not_disclosed"
    db_session.add(
        ContactDisclosure(
            candidate_id=acc.user_id,
            company_id=company_id,
            basis="invitation",
            basis_id=uuid.uuid4(),
            fields=["full_name", "email", "telegram"],  # телефон, работодатель и вуз не разрешены
            consent_text_ver="contact-consent-v1",
            granted_at=frozen_clock.now(),
        )
    )
    db_session.commit()
    r = client.get(url, headers=emp.headers)
    assert r.status_code == 200, r.text
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert NAME in text and "maria.debug@example.com" in text and "@maria_debug" in text
    assert "+79152041873" not in text and "Тихая гавань" not in text and "университет" not in text
    # Другой работодатель с той же ссылкой — 404; кандидат не может воспользоваться маршрутом работодателя
    other = make_account(email="hr@drugaya.ru", role="employer")
    assert client.get(url, headers=other.headers).status_code == 404
    assert client.get(url, headers=acc.headers).status_code == 403
    # Отзыв раскрытия — снова 404
    row = db_session.query(ContactDisclosure).filter_by(candidate_id=acc.user_id).one()
    row.revoked_at = frozen_clock.now()
    db_session.commit()
    assert client.get(url, headers=emp.headers).status_code == 404


def test_recent_tasks_and_freshness_on_sheet(client, make_account, db_session, frozen_clock):
    from app.models.tasks import EmployerTask, TaskAssignment

    acc, _ = _candidate(client, make_account, db_session, frozen_clock.now())
    task = EmployerTask(kind="catalog", title="Идемпотентность платежей", time_limit_min=15, status="active", is_platform=True)
    db_session.add(task)
    db_session.flush()
    db_session.add(
        TaskAssignment(
            employer_task_id=task.id,
            candidate_id=acc.user_id,
            status="auto_scored",
            kind="catalog",
            score=0.8,
            finished_at=frozen_clock.now() - timedelta(days=6),
        )
    )
    db_session.commit()
    r = _pdf(client, acc, "blind")
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "«Идемпотентность платежей» · 4/5 · 29.09.2026" in text
    assert "Актуальность профиля: 84 из 100 · свежий" in text
    pv = client.get("/api/candidates/me/preview", headers=acc.headers).json()
    assert pv["blind"]["tasks"][0]["title"] == "Идемпотентность платежей" and pv["blind"]["tasks"][0]["result"] == "4/5"
    assert pv["blind"]["freshness"]["band"] == "свежий"
