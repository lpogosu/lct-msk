"""Персона «Илья» собирается детерминированно и гарантированно (демо-сид не флакает).

Раньше `confirmed_session` брал фиксированного «абитуриента» и 12 сидов: оценка SQL по 4–6 заданиям шумная,
нужный исход («Middle подтверждён, SQL не выше порога») бывал у трети попыток, и сид иногда логировал
«нужный результат не получен» и оставлял Илью с подтверждённым SQL. Теперь после `escalate_after` неудач
«истинный» уровень модуля занижается дальше — исход гарантирован.
"""

from __future__ import annotations

from app.services.demo import personas
from app.services.demo.cfg import section
from app.testing.simulate import SimulatedExaminee


def test_escalating_examinee_is_deterministic_and_lowers_weak_module_only():
    make = personas.escalating_examinee(0.8, {"python_core": 0.8, "sql": -1.0}, "sql", step=0.15, after=8)
    assert make(0).module_theta == make(8).module_theta == {"python_core": 0.8, "sql": -1.0}
    late = make(28)
    assert late.module_theta["sql"] == -1.0 - 0.15 * 20 and late.module_theta["python_core"] == 0.8
    assert make(28).module_theta == late.module_theta  # одна и та же попытка — тот же абитуриент


def test_ilya_config_escalates_and_has_budget():
    cfg = section("personas")["ilya"]
    assert int(cfg["session_tries"]) > int(cfg["escalate_after"]) + 10
    worst = float(cfg["theta"]) + float(cfg["weak_modules"]["sql"]) - float(cfg["escalate_step"]) * (
        int(cfg["session_tries"]) - 1 - int(cfg["escalate_after"])
    )
    assert worst < float(cfg["unconfirmed_theta_max"]) - 1.0  # к последней попытке «истина» SQL заведомо ниже порога


def test_confirmed_session_stops_on_first_good_attempt_with_escalation(db_session, monkeypatch):
    """Подмена движка: SQL «не подтверждён» только у абитуриента с истинным уровнем ≤ -2,5 — такой достигается
    на конкретной попытке, и перебор останавливается на ней, а не на последней и не «как есть»."""
    from types import SimpleNamespace

    seen: list[tuple[int, float]] = []

    class Row:
        subscores: dict = {}

    def fake_run(db, user, *, examinee, rng, **kw):
        seen.append((len(seen), examinee.module_theta["sql"]))
        Row.subscores = {"sql": {"theta": examinee.module_theta["sql"]}}  # идеальная оценка = истина

        class T:
            main_id = None

        return T()

    monkeypatch.setattr(personas, "run_session", fake_run)
    monkeypatch.setattr(personas, "_qual_grade", lambda db, u, s: "middle")
    monkeypatch.setattr(personas, "get_row", lambda db, uid, spec: Row)
    user = SimpleNamespace(id=1)
    make = personas.escalating_examinee(0.8, {"sql": -1.0}, "sql", step=0.15, after=8)
    tr = personas.confirmed_session(
        db_session, user, spec="backend_python", grade="middle", examinee=make, key="t", tries=40,
        want_grade="middle", weak_module="sql", weak_theta_max=-2.5, extensions="decline",
    )
    assert tr is not None
    # -1.0 - 0.15·(k-8) <= -2.5  →  k = 18
    assert seen[-1][0] == 18 and seen[-1][1] <= -2.5 and all(s[1] > -2.5 for s in seen[:-1])
    assert isinstance(make(0), SimulatedExaminee)
