"""API резюме (экран К7): загрузка PDF → черновик с цитатами → подтверждение полей кандидатом.
Распознанное — «заявлено» и не меняет категорию (DECISIONS п. 1)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.models.candidate import CandidateProfile
from app.models.testing import Qualification

FIX = Path(__file__).parent / "fixtures" / "resumes"


def _upload(client, acc, name="backend_middle.pdf", data: bytes | None = None, ctype="application/pdf"):
    payload = data if data is not None else (FIX / name).read_bytes()
    return client.post("/api/candidates/me/resume", files={"file": (name, payload, ctype)}, headers=acc.headers)


def test_upload_returns_draft_with_quotes_then_confirm_marks_sources(client, make_account, frozen_clock, db_session):
    acc = make_account()
    r = _upload(client, acc)
    assert r.status_code == 201, r.text
    body = r.json()
    text, p = body["text"], body["parse"]
    assert body["status"] == "draft" and "заявлено" in body["note"]
    assert p["full_name"]["value"] == "Кортежев Денис Павлович"
    for key in ("full_name", "email", "phone", "telegram", "city", "declared_grade"):
        q = p[key]["quote"]
        assert text[q["start"] : q["end"]] == q["text"], key
    for item in p["skills"]:
        q = item["quote"]
        assert text[q["start"] : q["end"]] == q["text"]
    assert p["experience"]["value"] == 74

    # Черновик можно перечитать (экран после перезагрузки)
    again = client.get("/api/candidates/me/resume", headers=acc.headers)
    assert again.status_code == 200 and again.json()["sha256"] == body["sha256"]

    r = client.post(
        "/api/candidates/me/resume/confirm",
        json={
            "full_name": "Кортежев Денис Павлович",  # принято как есть
            "email": "d.kortezhev@example.com",
            "phone": "+7 999 000-11-22",  # исправлено вручную
            "telegram": "@kortezhev_dev",
            "city": "moscow",
            "declared_grade": "middle",
            "skills": ["python", "fastapi", "Kafka", "Rust"],  # Rust дописан вручную
            "roles": ["backend_dev"],
            "soft_skills": ["teamwork"],
        },
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    prof = r.json()
    skills = {s["id"]: s for s in prof["skills"]}
    assert skills["python"]["source"] == "resume" and skills["python"]["quote"] == "Python"
    assert skills["kafka"]["source"] == "resume"
    assert skills["rust"]["source"] == "manual" and skills["rust"]["quote"] is None
    assert prof["phone"] == "+79990001122"
    assert prof["region"]["code"] == "77" and prof["city"]["code"] == "moscow"
    assert prof["experience_months"] == 74 and prof["experience_label"] == "6 лет 2 мес."
    assert len(prof["experience"]) == 2
    assert prof["declared_grade"]["code"] == "middle"
    assert prof["resume"]["status"] == "confirmed" and prof["resume"]["has_text"] is True
    assert "не меняет" in prof["note"] or "не влияет" in prof["note"]

    row = db_session.get(CandidateProfile, acc.user_id)
    assert row.resume_fields["fields"]["full_name"]["source"] == "resume"
    assert row.resume_fields["fields"]["phone"]["source"] == "manual"
    assert "Кортежев" in row.resume_text
    # Заявленное не создаёт категорию: квалификации нет
    assert db_session.scalars(select(Qualification).where(Qualification.candidate_id == acc.user_id)).first() is None

    # Черновика больше нет
    assert client.get("/api/candidates/me/resume", headers=acc.headers).json()["code"] == "resume_draft_not_found"


def test_scan_wrong_type_and_size_errors_are_russian(client, make_account, frozen_clock):
    acc = make_account()
    r = _upload(client, acc, "scan_no_text.pdf")
    assert r.status_code == 422 and r.json()["code"] == "resume_no_text"
    assert "вручную" in r.json()["message"]
    r = _upload(client, acc, "cv.docx", data=b"PK\x03\x04 not a pdf", ctype="application/msword")
    assert r.status_code == 415 and r.json()["code"] == "resume_not_pdf"
    r = _upload(client, acc, "big.pdf", data=b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024 + 1))
    assert r.status_code == 413 and r.json()["code"] == "resume_too_large"
    r = _upload(client, acc, "empty.pdf", data=b"")
    assert r.status_code == 422


def test_confirm_without_draft_and_unknown_codes(client, make_account, frozen_clock):
    acc = make_account()
    r = client.post("/api/candidates/me/resume/confirm", json={"full_name": "X"}, headers=acc.headers)
    assert r.status_code == 404 and r.json()["code"] == "resume_draft_not_found"
    assert _upload(client, acc, "frontend_junior.pdf").status_code == 201
    r = client.post("/api/candidates/me/resume/confirm", json={"roles": ["wizard"]}, headers=acc.headers)
    assert r.status_code == 422 and "Ролей нет в справочнике" in r.json()["details"][0]["message"]
    r = client.post("/api/candidates/me/resume/confirm", json={"skills": ["Квантовая телепортация"]}, headers=acc.headers)
    assert r.status_code == 422
    r = client.post("/api/candidates/me/resume/confirm", json={"unknown_field": 1}, headers=acc.headers)
    assert r.status_code == 422


def test_delete_resume_keeps_fields_drops_text_and_quotes(client, make_account, frozen_clock, db_session):
    acc = make_account()
    _upload(client, acc, "frontend_junior.pdf")
    client.post("/api/candidates/me/resume/confirm", json={"skills": ["react", "typescript"]}, headers=acc.headers)
    r = client.delete("/api/candidates/me/resume", headers=acc.headers)
    assert r.status_code == 200
    prof = r.json()
    assert prof["resume"]["status"] == "none" and prof["resume"]["has_text"] is False
    assert {s["id"] for s in prof["skills"]} == {"react", "typescript"}
    assert all(s["quote"] is None for s in prof["skills"])
    assert db_session.get(CandidateProfile, acc.user_id).resume_text is None


def test_resume_is_candidate_only(client, make_account, frozen_clock):
    emp = make_account(role="employer")
    r = _upload(client, emp)
    assert r.status_code == 403 and r.json()["code"] == "role_forbidden"
    r = client.post("/api/candidates/me/resume", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 401
