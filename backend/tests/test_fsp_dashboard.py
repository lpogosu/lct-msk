"""Панель ФСП «Кадровый резерв»: k-анонимность, доступ по ролям, согласованность чисел на маленькой фикстуре, нет ПДн.
docs/FSP_INTEGRATION.md → «Панель ФСП: кадровый резерв»; config/fsp_dashboard.yaml."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.contact import Invitation, OfferTerms
from app.models.enums import NeedStatus, QualificationStatus, SessionStatus, UserRole, UserStatus
from app.models.fsp import FspAchievementCache, FspLink
from app.models.matching import Need
from app.models.testing import Qualification, TestSession
from app.models.users import User
from app.services.demo import fsp_reserve
from app.services.fsp_dashboard import service
from app.services.fsp_dashboard.kanon import fold_small_rows, mask, mask_partition
from app.services.profile.regions import REGIONS
from tests.test_api_testing_support import make_role

K = 5


# --------------------------------------------------------------------------- k-анонимность (чистая логика)


def test_mask_hides_one_to_k_minus_one_but_not_zero():
    assert mask(0, K).text == "0" and not mask(0, K).suppressed
    for n in range(1, K):
        c = mask(n, K)
        assert c.suppressed and c.n is None and c.text == "<5"
    assert mask(K, K).n == K and mask(K + 7, K).text == str(K + 7)


def test_partition_hides_complement_so_single_cell_cannot_be_subtracted():
    out = mask_partition({"a": 20, "b": 2, "c": 9}, K)
    assert out["b"].suppressed
    assert out["c"].suppressed, "скрыта одна ячейка — скрываем и наименьшую из видимых, иначе b = итог − a − c"
    assert out["a"].n == 20
    # две скрытые ячейки вычесть нельзя — вторичное подавление не нужно
    out = mask_partition({"a": 20, "b": 2, "c": 3}, K)
    assert out["a"].n == 20
    # без вторичного подавления
    assert not mask_partition({"a": 20, "b": 2, "c": 9}, K, complementary=False)["c"].suppressed


def test_fold_small_rows_never_leaves_small_remainder():
    shown, other = fold_small_rows({"x": 12, "y": 6, "z": 2}, K)
    assert other >= K or other == 0
    assert "z" not in shown and sum(shown.values()) + other == 20


def test_district_config_is_a_function_of_region_code():
    seen: dict[str, str] = {}
    for d in load_config("fsp_dashboard")["districts"]:
        for code in d["regions"]:
            assert code not in seen, f"регион {code} в двух округах: {seen[code]} и {d['code']}"
            seen[code] = d["code"]
    assert not (set(REGIONS) - set(seen)), "у каждого субъекта из справочника должен быть округ"


# --------------------------------------------------------------------------- фикстура: 7 спортсменов ФСП и 3 остальных


class World:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.company = Company(email_domain="co.example.com", name="ООО «Квазар-9»", is_demo=True)
        db.add(self.company)
        db.flush()
        self.need = Need(
            company_id=self.company.id, raw_text="x", status=NeedStatus.active.value, specialization="backend_python",
            grade="middle", salary_from=100_000, salary_to=140_000,
        )
        db.add(self.need)
        db.flush()

    def user(self, tag: str, *, fsp: bool, region: str, disciplines: tuple[str, ...] = (), confirmed: bool = True) -> User:
        db = self.db
        u = User(email=f"{tag}@example.com", email_domain="example.com", role=UserRole.candidate.value, status=UserStatus.active.value)
        db.add(u)
        db.flush()
        db.add(CandidateProfile(user_id=u.id, region_code=region))
        if fsp:
            db.add(FspLink(user_id=u.id, fsp_id=f"fsp-{tag}", is_demo=True, data_source="fsp_mock", linked_at=clock.now() - timedelta(days=2)))
            for i, disc in enumerate(disciplines):
                db.add(FspAchievementCache(user_id=u.id, fsp_id=f"fsp-{tag}", external_id=f"{tag}-{i}", kind="result", discipline=disc))
        db.add(TestSession(candidate_id=u.id, specialization="backend_python", kind="initial", claimed_grade="middle",
                           status=SessionStatus.final.value, seed=1))
        if confirmed:
            db.add(Qualification(candidate_id=u.id, specialization="backend_python", status=QualificationStatus.confirmed.value, grade="middle"))
        db.flush()
        return u

    def invite(self, u: User, *, mutual: bool = False, hired: bool = False, mid: int = 120_000, net: bool = True) -> None:
        now = clock.now()
        inv = Invitation(
            company_id=self.company.id, need_id=self.need.id, candidate_id=u.id, pseudonym="К-1000", respond_by=now + timedelta(days=7),
            created_at=now - timedelta(days=1), status="closed" if hired else ("accepted" if mutual else "sent"),
            accepted_at=now if (mutual or hired) else None, outcome="hired" if hired else None, closed_at=now if hired else None,
        )
        self.db.add(inv)
        self.db.flush()
        self.db.add(OfferTerms(invitation_id=inv.id, ver=1, author="employer", title="t", description="d",
                               salary_from=mid - 10_000, salary_to=mid + 10_000, salary_net=net, sha256="0" * 64))
        self.db.flush()


@pytest.fixture
def world(db_session: Session) -> World:
    w = World(db_session)
    athletes = [w.user(f"a{i}", fsp=True, region="77", disciplines=("algo",), confirmed=i < 6) for i in range(6)]
    athletes.append(w.user("a6", fsp=True, region="16", disciplines=("product",), confirmed=False))  # единственный из ПФО
    others = [w.user(f"o{i}", fsp=False, region="78") for i in range(6)]
    w.invite(athletes[0], hired=True)  # из приглашённых: 1 нанят, 3 «Взаимно» (включая нанятого), 4 приглашены — всё меньше k
    w.invite(athletes[1], mutual=True)
    w.invite(athletes[2], mutual=True)
    w.invite(athletes[3])
    for o in others:
        w.invite(o, mid=100_000)
    return w


def _dashboard(db: Session) -> dict:
    return service.build_dashboard(db)


def _funnel(d: dict) -> dict:
    return {s["key"]: s for s in d["funnel"]}


def test_funnel_numbers_match_fixture_and_hide_small_cells(world: World):
    f = _funnel(_dashboard(world.db))
    assert f["linked"]["fsp"].n == 7 and f["linked"]["other"].n == 6
    assert f["tested"]["fsp"].n == 7
    assert f["confirmed"]["fsp"].n == 6 and f["confirmed"]["other"].n == 6
    assert f["shortlisted"]["fsp"].suppressed  # 4 приглашённых из подтверждённых < k
    assert f["invited"]["fsp"].text == "<5" and f["invited"]["fsp"].n is None
    assert f["mutual"]["fsp"].text == "<5" and f["hired"]["fsp"].text == "<5"
    # доля от предыдущей стадии не показывается, если сама ячейка или знаменатель скрыты
    assert f["invited"]["fsp_rate"] is None and f["mutual"]["fsp_rate"] is None
    assert f["tested"]["fsp_rate"] == 100.0


def test_funnel_is_nested_and_monotone(world: World):
    d = _dashboard(world.db)
    cands = service._load_candidates(world.db)
    service._load_invitations(world.db, cands)
    for group in ([c for c in cands.values() if c.fsp], [c for c in cands.values() if not c.fsp]):
        counts = service._stage_counts(group)
        order = [s["key"] for s in load_config("fsp_dashboard")["funnel"]["stages"]]
        values = [counts[k] for k in order]
        assert values == sorted(values, reverse=True), values
    assert [s["key"] for s in d["funnel"]] == [s["key"] for s in load_config("fsp_dashboard")["funnel"]["stages"]]


def test_breakdowns_are_consistent_with_funnel_and_suppressed(world: World):
    d = _dashboard(world.db)
    disc = {r["key"]: r["stages"] for r in d["by_discipline"]}
    assert disc["algo"]["linked"].n == 6 and disc["product"]["linked"].suppressed and disc["robotics"]["linked"].n == 0
    # округа: ПФО (1 человек) скрыт, поэтому вторичное подавление скрывает и ЦФО — иначе ПФО = 7 − ЦФО
    dist = {r["key"]: r["stages"]["linked"] for r in d["by_district"]}
    assert dist["pfo"].suppressed and dist["cfo"].suppressed
    # регионы: Татарстан (1 человек) сливается в «прочие», а «прочие» < k домешивают Москву, иначе Татарстан = итог − Москва
    assert d["by_region"]["rows"] == []
    assert d["by_region"]["other"]["stages"]["linked"].n == 7
    # специализация × грейд: подтверждённых спортсменов 6 (a6 без категории)
    assert d["spec_grade"]["rows"][0]["cells"]["middle"].n == 6


def test_salary_medians_by_grade_hidden_below_k_and_labelled(world: World):
    s = _dashboard(world.db)["salary"]
    row = next(r for r in s["rows"] if r["key"] == "middle")
    assert row["fsp"]["n"].suppressed and row["fsp"]["median_net"] is None  # 4 приглашённых спортсмена < 5
    assert row["other"]["n"].n == 6 and row["other"]["median_net"] == 100_000
    assert "на руки" in s["basis"] and "до НДФЛ" in s["basis"]
    # добавим приглашённых, чтобы дошло до k, и проверим медиану и пересчёт «до НДФЛ»
    w = world
    for i in range(2):
        w.invite(w.user(f"x{i}", fsp=True, region="77"), mid=120_000)
    w.invite(w.user("g", fsp=True, region="77"), mid=200_000, net=False)  # 200 000 до НДФЛ → 174 000 на руки
    s = service.build_dashboard(w.db)["salary"]
    row = next(r for r in s["rows"] if r["key"] == "middle")
    assert row["fsp"]["n"].n == 7 and row["fsp"]["median_net"] == 120_000
    ndfl = s["ndfl_rate"]
    assert row["fsp"]["median_gross"] == round(120_000 / (1 - ndfl), -3)


def test_demand_vs_supply_has_no_company_names_and_marks_deficit(world: World):
    d = _dashboard(world.db)
    row = next(r for r in d["demand_supply"] if r["spec"] == "backend_python" and r["grade"] == "middle")
    # одна компания со спросом < k_min_companies: число скрыто, оценка «мало данных»
    assert row["demand_companies"].suppressed and row["status"] == "few_data"


def test_no_personal_data_in_response(world: World):
    d = _dashboard(world.db)
    blob = json.dumps(d, default=lambda o: o.model_dump(), ensure_ascii=False)
    users = [u for u in world.db.query(User).all()]
    for u in users:
        assert u.email not in blob and str(u.id) not in blob
    assert "Квазар" not in blob, "названия компаний в ответе быть не должно"
    assert "fsp-a0" not in blob


def test_csv_keeps_suppressed_cells_and_has_bom(world: World):
    text = service.to_csv(_dashboard(world.db))
    assert text.startswith("﻿") and "<5" in text
    assert "воронка" in text and "Демо-данные синтетические" in text
    for u in world.db.query(User).all():
        assert u.email not in text


def test_history_seed_skips_small_world(db_session: Session):
    assert "skipped" in fsp_reserve.seed_history(db_session)


# --------------------------------------------------------------------------- доступ по ролям


def test_dashboard_is_for_methodist_and_admin_only(client, make_account, db_session: Session):
    cand, emp, met, adm = make_account(), make_account(role="employer"), make_account(), make_account()
    make_role(db_session, met.user_id, "methodist")
    make_role(db_session, adm.user_id, "admin")
    for url in ("/api/fsp/dashboard", "/api/fsp/dashboard/export.csv"):
        assert client.get(url).status_code == 401
        assert client.get(url, headers=cand.headers).status_code == 403
        assert client.get(url, headers=emp.headers).status_code == 403
        assert client.get(url, headers=met.headers).status_code == 200
        assert client.get(url, headers=adm.headers).status_code == 200
    r = client.get("/api/fsp/dashboard", headers=met.headers)
    body = r.json()
    assert body["k_min"] == K and len(body["funnel"]) == 7 and body["synthetic"] is False
    assert client.get("/api/fsp/dashboard/export.csv", headers=met.headers).headers["content-type"].startswith("text/csv")
    assert str(met.user_id) not in r.text
