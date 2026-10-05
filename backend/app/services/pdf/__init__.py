"""Стандартизированный PDF-профиль (концепция §3.1.2): лист, отрисовка reportlab, код сверки /v/{код}.

Контракт: ``issue(db, user, variant, now, settings)`` — выдать себе; ``employer_profile_pdf(db, company_id=…,
candidate_id=…, settings=…, now=…)`` — полная версия работодателю только при действующем раскрытии;
``verify(db, code, now)`` — публичная сверка.
"""
