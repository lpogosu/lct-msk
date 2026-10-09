"""Гейт семейства twin_gate, отчёт по реестру (CLI) и конфиг config/anticheat.yaml."""

from __future__ import annotations

import json
import re

import pytest

from app.core.configs import load_config
from app.testing.anticheat import gates, report
from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.gates import registry_coverage, registry_runs, twin_gate
from app.testing.anticheat.twins import reason_text
from app.testing.bank.base import Instance, Rendered
from app.testing.bank.registry import all_families
from tests.test_anticheat_support import TinyFamily

CFG = load_anticheat_config()
REG = all_families()


# ----------------------------------------------------------------------------- конфиг


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list | tuple):
        for v in node:
            yield from _strings(v)


def test_config_has_all_thresholds_from_the_spec():
    raw = load_config("anticheat")
    assert raw["version"] == 1
    assert CFG.twins.classes_mvp == ("data_literal", "table_value")  # граничные мутации запрещены каталогом M1
    assert CFG.eps.default == {"data_literal": 0.02, "table_value": 0.02}  # spec §2.1: консервативные ε
    assert CFG.gate.n == 50 and CFG.gate.min_twin_share == 0.9 and CFG.gate.max_honest_collision == 0.01
    assert CFG.gate.min_family_share == 0.85
    assert CFG.evidence.trust_weight == 0.0 and CFG.evidence.calibrated is False and CFG.evidence.auto_ban is False
    assert CFG.evidence.review_p_threshold == 1e-3 and CFG.evidence.twin_rate_per_session == 20  # двойник на каждом готовом задании (VALIDATION §10)
    assert CFG.evidence.cap_before_warning == "note" and CFG.evidence.cap_a11y_mode == "note"
    assert CFG.verification.n_trace_questions == 3 and CFG.verification.max_skill_questions == 2
    assert CFG.verification.rubric_max == 2
    assert CFG.sandbox.n_items == 4
    assert frozenset("17") in CFG.twins.confusable_pairs and frozenset("38") in CFG.twins.confusable_pairs


def test_config_texts_are_russian_and_free_of_theta():
    strings = list(_strings(load_config("anticheat")["texts"]))
    assert len(strings) > 60
    assert not any("θ" in s for s in strings)  # в пользовательских строках «θ» нет
    for s in strings:
        if re.search(r"[A-Za-z]{4,}", s) and not re.search(r"[А-Яа-я]", s):
            continue  # коды (data_literal и т. п.) и названия технологий
        assert not re.search(r"\bИИ\b", s), s  # в уликах слова «ИИ» не используется (spec §2.1 (а))


def test_config_text_helper_formats_and_rejects_unknown_keys():
    assert CFG.text("act_title") == "Акт сверки: показано и скопировано"
    assert CFG.text("verification.trace_ask_code", line=3, **{"from": "9", "to": "12"}).startswith("Что выведет программа")
    with pytest.raises(KeyError):
        CFG.text("verdict.no_such_text")
    with pytest.raises(KeyError):
        AntiCheatConfig.from_dict({})


def test_every_twin_reason_has_a_russian_text():
    for reason in ("ok", "format_unsupported", "answer_mode_not_text", "language_not_executable", "no_code",
                   "key_not_reproducible", "executor_failed", "no_candidates", "all_candidates_rejected"):
        assert re.search(r"[А-Яа-я]", reason_text(reason)), reason


# ----------------------------------------------------------------------------- twin_gate


@pytest.mark.parametrize("fid", ["py.defaults.mutable", "sql.aggregate.null", "js.closure_loop", "py.dict.basics"])
def test_twin_gate_passes_eligible_families(fid):
    res = twin_gate(REG[fid], n=12)
    assert res.applicable and res.eligible and res.ok, res.errors
    assert res.stats.share >= CFG.gate.min_twin_share
    assert res.stats.key_equals_twin == 0 and res.stats.pool_collision <= CFG.gate.max_honest_collision
    assert 0.0 <= res.stats.natural_collision < 0.5
    assert res.stats.honest_mean == pytest.approx(0.02, abs=0.02)  # модельная вероятность честного совпадения


def test_twin_gate_known_unsupported_forms_do_not_fail_the_gate():
    css = twin_gate(REG["css.box_model"], n=4)  # язык без офлайн-исполнителя
    assert css.applicable and not css.eligible and css.ok and css.stats.ineligible_reason == "language_not_executable"
    choice = twin_gate(REG["sql.join.left_rows"], n=4)  # выбор из вариантов
    assert choice.applicable and not choice.eligible and choice.ok
    assert choice.stats.ineligible_reason == "answer_mode_not_text"
    bug = next(f for f in REG.values() if f.format == "bug_line")
    out = twin_gate(bug, n=3)
    assert not out.applicable and out.ok  # формат вне MVP двойников


def test_twin_gate_fails_when_family_has_no_safe_twin():
    class NoLiterals(TinyFamily):
        id = "test.noliterals"

        def generate(self, seed: int) -> Instance:
            code = f'print("hello")  # {seed}'
            return Instance(self.id, seed, Rendered(prompt="Что напечатает программа?", code=code, language="python",
                                                    answer_mode="text"), "hello", {})

    res = twin_gate(NoLiterals(), n=6)
    assert res.eligible and not res.ok
    assert any("доля экземпляров с двойником 0.00" in e for e in res.errors)
    assert res.stats.reasons == {"no_candidates": 6}


def _collide_family(base_of):
    """Семейство «v = base; print(v * 2)»: ключ base·2. Кандидат-двойник (множитель 2 → 4) для base = 300 даёт 1200 —
    ключ соседа с base = 600; гейт 4 такого кандидата отбрасывает."""

    class Collide(TinyFamily):
        id = "test.collide"

        def generate(self, seed: int) -> Instance:
            base = base_of(seed)
            code = f"v = {base}\nprint(v * 2)"
            return Instance(self.id, seed, Rendered(prompt="Что напечатает программа?", code=code, language="python",
                                                    answer_mode="text"), str(base * 2), {})

    return Collide()


def test_twin_gate_reports_pool_collisions():
    # два экземпляра пула (300 и 600) взаимно перекрывают кандидатов друг друга, остальные ключи уникальны
    fam = _collide_family(lambda seed: 300 if seed == 0 else 600 if seed == 1 else 1000 + seed * 37)
    res = twin_gate(fam, n=8)
    # плотность ключей семейства видна (кандидаты совпали с ключами соседей), но гейт 4 их отбрасывает
    assert res.stats.natural_collision > 0 and res.stats.rejects.get("pool_collision", 0) > 0
    assert res.stats.pool_collision == 0  # ни один итоговый двойник не совпал с ключом соседнего экземпляра
    assert res.ok, res.errors


def test_twin_gate_flags_a_family_with_dense_keys():
    # всего два различных ключа на восемь экземпляров: каждый второй кандидат совпал бы с ответом соседа, и это
    # плотность выше порога семейства (gate.max_natural_collision), а не ошибка гейта 4: итоговые двойники чистые
    res = twin_gate(_collide_family(lambda seed: 300 if seed % 2 == 0 else 600), n=8)
    assert res.stats.pool_collision == 0 and res.stats.valid == 8
    assert res.stats.natural_collision > load_anticheat_config().gate.max_natural_collision
    assert not res.ok and any("плотность ключей семейства" in e for e in res.errors)


# ----------------------------------------------------------------------------- реестр и отчёт


def test_registry_coverage_report_numbers():
    cov = registry_coverage(20)
    assert cov.total == len(REG) == len(registry_runs(20))
    assert len(cov.applicable) >= 55 and len(cov.eligible) >= 30
    assert cov.meets_threshold and cov.family_share >= CFG.gate.min_family_share
    by_id = {s.family_id: s for s in cov.stats}
    assert by_id["react.stale_closure"].eligible and by_id["react.stale_closure"].share == 0.0  # JSX Node не исполняет
    assert by_id["py.mro.super"].reasons.get("no_candidates") == by_id["py.mro.super"].n  # в коде только слова
    assert not by_id["css.box_model"].eligible and not by_id["http.status.scenario"].eligible
    assert by_id["http.status.scenario"].ineligible_reason == "language_not_executable"


def test_report_cli_prints_per_family_coverage(capsys):
    code = report.main(["--n", "3", "--family", "py.defaults.mutable", "--family", "css.box_model"])
    out = capsys.readouterr().out
    assert code == 0
    assert "py.defaults.mutable" in out and "css.box_model" in out and "3/3" in out
    assert "язык не исполняется офлайн-исполнителем" in out
    assert "Двойник на ≥ 90% сидов" in out


def test_report_cli_json_and_unknown_family(capsys):
    assert report.main(["--n", "2", "--family", "sql.aggregate.null", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["n"] == 2 and data["families"][0]["family_id"] == "sql.aggregate.null"
    assert data["families"][0]["valid"] == 2 and data["eligible"] == 1
    assert report.main(["--family", "no.such.family"]) == 2
    capsys.readouterr()


def test_report_formatting_for_the_whole_registry():
    text = report.format_report(registry_coverage(20))
    assert f"Семейств в реестре: {len(REG)}" in text
    assert text.count("\n") >= len(REG)
    assert "порог 85%): ОК" in text
    assert gates.stats_to_dict(registry_coverage(20).stats[0])["family_id"]
