"""Производительность подбора (spec-contact §6.2): p95 сборки версии подборки на 2000 кандидатов ≤ 300 мс.

Признаки кандидатов предрассчитаны (кэш matching_features пересобирается заранее, вне замера). Замеряется то,
что делает POST /api/needs/{id}/versions: проверка свежести кэша, загрузка пула, Score и объяснения движком,
запись неизменяемой версии и первая страница «Общего топа». Порог в тесте — с запасом на шумный CI
(VZ_PERF_STRICT=1 включает ровно 300 мс), измеренные цифры печатаются.
"""

from __future__ import annotations

import os
import random
import statistics
import time
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import insert

from app.core.clock import clock
from app.core.consents import current_version
from app.models.candidate import CandidateProfile
from app.models.fsp import FspLink
from app.models.testing import Qualification
from app.models.users import Consent, User
from app.services.matching import features as feat
from app.services.matching import needs as svc
from app.services.testing.snapshot import qualification_to_state
from app.testing.session import Qualification as DomainQualification
from app.testing.session import QualStatus
from tests.test_api_needs_support import DEMO_BRIEF, SPEC_MODULES, _demo_provider, fsp_registry_data

N = 2000
pytestmark = pytest.mark.usefixtures("frozen_clock")


def _bulk_pool(db, n: int, seed: int = 7) -> list[uuid.UUID]:
    rng = random.Random(seed)
    now = clock.now()
    grades = ("junior", "middle", "middle", "middle", "senior")
    centers = {"junior": -0.5, "middle": 0.5, "senior": 1.5}
    subs = [a.sub for a in _demo_provider()._snap.athletes]
    users, profiles, consents, quals = [], [], [], []
    fsp_links = []
    ids = []
    for i in range(n):
        uid = uuid.uuid4()
        ids.append(uid)
        grade = grades[i % len(grades)]
        theta = centers[grade] + rng.gauss(0, 0.35)
        users.append(
            {
                "id": uid,
                "email": f"bulk-{uid.hex[:16]}@example.com",
                "email_domain": "example.com",
                "role": "candidate",
                "status": "active",
                "email_verified": True,
                "created_at": now,
                "updated_at": now,
            }
        )
        profiles.append(
            {
                "user_id": uid,
                "search_mode": "open",
                "salary_min": int(rng.lognormvariate(12.3, 0.25)) // 1000 * 1000,
                "salary_type": "net",
                "salary_visibility": "signal",
                "weekly_invite_limit": 5,
                "formats": rng.sample(["office", "hybrid", "remote"], k=rng.randint(1, 3)),
                "region_code": "77",
                "city": "Москва",
                "relocation": False,
                "blocked_company_ids": [],
                "field_visibility": {},
                "industries": ["fintech"],
                "created_at": now,
                "updated_at": now,
            }
        )
        consents.append(
            {
                "user_id": uid,
                "purpose": "blind_card",
                "text_version": current_version("blind_card"),
                "granted_at": now,
            }
        )
        at = now - timedelta(days=rng.randint(1, 300))
        q = DomainQualification(account_id=str(uid), spec="backend_python")
        q.status, q.grade, q.theta, q.se, q.trust = QualStatus.CONFIRMED, grade, round(theta, 3), 0.3, 1.0
        q.confirmed_at = q.first_confirmed_at = q.last_activity_at = at
        q.subscores = {
            m: {"theta": round(theta + rng.gauss(0, 0.3), 3), "se": 0.4, "tag": rng.choice(["strong", "confirmed", "insufficient"]), "n": 3}
            for m in SPEC_MODULES["backend_python"]
        }
        q.module_tags = {m: s["tag"] for m, s in q.subscores.items() if s["tag"] != "insufficient"}
        quals.append(
            {
                "candidate_id": uid,
                "specialization": "backend_python",
                "status": "CONFIRMED",
                "grade": grade,
                "theta": q.theta,
                "se": q.se,
                "trust": 1.0,
                "borderline": False,
                "under_review": False,
                "confirmed_at": at,
                "last_activity_at": at,
                "subscores": q.subscores,
                "module_tags": q.module_tags,
                "state": qualification_to_state(q) | {"subscores": q.subscores},
                "created_at": now,
                "updated_at": now,
            }
        )
        if i < len(subs) and i % 2 == 0:  # ~1,3% с привязанным ФСП из демо-реестра
            ach, ath = fsp_registry_data(subs[i])
            consents.append(
                {"user_id": uid, "purpose": "fsp_enrichment", "text_version": current_version("fsp_enrichment"), "granted_at": now}
            )
            fsp_links.append(
                {"user_id": uid, "fsp_id": subs[i], "achievements": ach, "athlete": ath, "last_sync_at": now, "linked_at": now, "created_at": now}
            )
    db.execute(insert(User), users)
    db.execute(insert(CandidateProfile), profiles)
    db.execute(insert(Consent), consents)
    db.execute(insert(Qualification), quals)
    if fsp_links:
        db.execute(insert(FspLink), fsp_links)
    db.flush()
    return ids


def test_selection_build_p95_2000_candidates(db_session, make_account, client):
    emp = make_account("hr@severny-kod.ru", role="employer")
    _bulk_pool(db_session, N)
    t0 = time.perf_counter()
    rebuilt = feat.ensure_fresh(db_session)  # предрасчёт признаков — вне замера
    rebuild_ms = (time.perf_counter() - t0) * 1000
    assert rebuilt >= N
    user = db_session.get(User, emp.user_id)
    need = svc.create_need(db_session, user, svc.BriefInput(text=DEMO_BRIEF), title=None, is_internship=False)
    # Выбираем Middle и Senior — около 1600 кандидатов в подборке
    svc._select_categories(need, [("backend_python", "middle"), ("backend_python", "senior")])
    db_session.flush()
    secret = "test-secret"
    times, phases = [], []
    for run in range(10):
        t = time.perf_counter()
        nv = svc.create_version(db_session, user, need, secret=secret, filters=[{"code": "has_fsp"}] if run % 2 else [])
        t_built = time.perf_counter()
        view = svc.version_view(db_session, need, nv, secret=secret)
        times.append((time.perf_counter() - t) * 1000)
        phases.append(nv.stats["timings_ms"] | {"persist+impr": round((t_built - t) * 1000 - nv.stats["timings_ms"]["total_ms"], 1), "view_ms": round((time.perf_counter() - t_built) * 1000, 1)})
        assert view["items"] and nv.stats["total_n"] >= 1500
    warm = sorted(times[1:])  # первая сборка прогревает кэши конфигов и справочников
    p95 = warm[min(len(warm) - 1, int(round(0.95 * (len(warm) - 1))))]
    # «Сборка подборки» без записи снимка в БД: проверка свежести + загрузка признаков + Score и объяснения + DTO
    build = sorted(p["total_ms"] for p in phases[1:])
    build_p95 = build[min(len(build) - 1, int(round(0.95 * (len(build) - 1))))]
    # Повторная проверка свежести кэша без изменений почти бесплатна
    t = time.perf_counter()
    assert feat.ensure_fresh(db_session) == 0
    check_ms = (time.perf_counter() - t) * 1000
    print(
        f"\n[perf] {N} кандидатов: пересборка кэша {rebuild_ms:.0f} мс; сборка подборки p95 {build_p95:.0f} мс "
        f"(median {statistics.median(build):.0f}); весь запрос с записью снимка и первой страницей: median "
        f"{statistics.median(warm):.0f} мс, p95 {p95:.0f} мс (все: {[round(x) for x in times]}); "
        f"проверка свежести {check_ms:.0f} мс"
    )
    for p in phases:
        print("[perf] этапы:", p)
    # Цель spec-contact §6.2 (p95 сборки ≤ 300 мс) проверяется отдельным прогоном: VZ_PERF_STRICT=1.
    # По умолчанию порог — страж от регрессий вида O(n²) (например, план с вложенными циклами — секунды):
    # в общем прогоне на общей машине параллельные задачи замедляют CPU и сеть Docker в 2–3 раза.
    strict = os.environ.get("VZ_PERF_STRICT") == "1"
    assert build_p95 <= (300 if strict else 1500), f"сборка p95 {build_p95:.0f} мс"
    assert p95 <= (450 if strict else 3000), f"запрос p95 {p95:.0f} мс"
