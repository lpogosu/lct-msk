"""Рыночные ориентиры зарплат из открытых данных: инварианты данных, подсказка в форме брифа, сигнал доверия."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.matching import market
from app.models.matching import Need
from app.trust import flags as trust_flags
from tests.test_api_contact_support import make_employer, make_need

DATA = Path(__file__).resolve().parents[2] / "data" / "market"


def test_config_quantiles_monotone_and_enough_data():
    cfg = market.market_cfg()
    assert cfg["meta"]["basis"] == "gross_assumed" and cfg["meta"]["currency"] == "RUB"
    cells = [*cfg["direction_grade"], *cfg["direction"]]
    assert cells
    for c in cells:
        assert c["n"] >= cfg["min_n"]
        assert c["p10"] <= c["p25"] <= c["p50"] <= c["p75"] <= c["p90"]


def test_benchmark_gross_net_and_fallback():
    b = market.benchmark("devops", "middle")
    assert b and b["scope"] == "direction_grade" and b["grade"] == "middle"
    ndfl = market.market_cfg()["meta"]["ndfl_rate"]
    assert abs(b["net"]["p50"] - b["gross"]["p50"] * (1 - ndfl)) <= 100
    # тонкая ячейка грейда -> по направлению целиком
    f = market.benchmark("backend_python", "junior")
    assert f and f["scope"] == "direction" and f["grade"] is None
    assert market.benchmark("no_such_direction", "middle") is None
    assert market.benchmark(None, None) is None


def test_salary_vs_market_respects_basis():
    b = market.benchmark("qa", "middle")
    p25 = b["gross"]["p25"]
    ratio = 0.8
    # «на руки» пересчитывается в gross: 0.5·p25 на руки далеко ниже рынка, 2·p25 на руки — нет
    low = market.salary_vs_market("qa", "middle", int(0.5 * p25), True, ratio)
    high = market.salary_vs_market("qa", "middle", int(2 * p25), True, ratio)
    assert low["far_below"] and not high["far_below"]
    # ровно та же сумма «до НДФЛ» не дороже, чем «на руки»: gross-основание не умножается на 1/(1-ндфл)
    gross = market.salary_vs_market("qa", "middle", int(0.85 * p25), False, ratio)
    net = market.salary_vs_market("qa", "middle", int(0.85 * p25), True, ratio)
    assert not gross["far_below"] and not net["far_below"]
    assert net["to_gross"] > gross["to_gross"]


def test_open_data_files_have_no_personal_data_or_raw_texts():
    assert (DATA / "README.md").exists()
    for f in DATA.glob("*.json"):
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), f.name
        assert not re.search(r"(?<![\d.])(?:\+7|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}", text), f.name
        assert len(text) < 400_000, f.name
    gi = (DATA.parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert "data/market/_raw/" in gi


def test_market_endpoint_employer_only(client, make_account):
    emp = make_account("hr@severny-kod.ru", role="employer")
    r = client.get(
        "/api/needs/market-salary",
        params={"specialization": "devops", "grade": "middle", "salary_to": 10000, "salary_net": "true"},
        headers=emp.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] and body["gross"]["p25"] <= body["gross"]["p75"]
    assert body["far_below"] is True and body["ratio_to_p25"] < 0.8
    assert "регион" in body["note"].lower()
    none = client.get("/api/needs/market-salary", params={"specialization": "uiux_design"}, headers=emp.headers)
    assert none.status_code == 200 and none.json()["available"] is False
    cand = make_account("c@example.org", role="candidate")
    assert client.get("/api/needs/market-salary", params={"specialization": "devops"}, headers=cand.headers).status_code == 403


def test_trust_flag_salary_far_below_market(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    make_need(db_session, emp)  # 220–280 тыс. на руки — выше рынка
    codes = [f["code"] for f in trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now())]
    assert "salary_far_below_market" not in codes
    make_need(db_session, emp, spec="devops", grade="middle", salary_from=15000, salary_to=20000)
    found = trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now())
    flag = next(f for f in found if f["code"] == "salary_far_below_market")
    assert "рыночной" in flag["label"] and "до НДФЛ" in flag["detail"]
    # сигнал не меняет бриф и не блокирует его
    assert db_session.query(Need).filter(Need.company_id == emp.company_id).count() == 2


@pytest.mark.parametrize("direction", ["backend_python", "devops", "qa"])
def test_benchmark_has_source_and_date(direction):
    b = market.benchmark(direction, None)
    assert b and "Работа в России" in b["source"] and re.match(r"\d{4}-\d{2}-\d{2}$", b["fetched"])
