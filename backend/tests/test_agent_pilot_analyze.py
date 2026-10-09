"""Пилот с ИИ-испытуемыми: метрики анализа, предрегистрация и вставка раздела в документ (eval/agent_pilot)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.agent_pilot import analyze as an
from eval.agent_pilot.personas import PERSONAS

RULER = [{"grade": g, "name": g, "score": s} for g, s in (("intern", 300), ("junior", 400), ("middle", 500), ("senior", 600))]


def _run(pid: str, attempt: int, score: int, passed: bool, evidence: list[dict[str, Any]] | None = None, copied: int = 0) -> dict[str, Any]:
    p = PERSONAS[pid]
    items = [
        {"seq": i + 1, "wall_s": 2.0, "timed_out": False, "skipped": False, "used_clip": i < copied, "clip_present": i < copied}
        for i in range(10)
    ]
    return {
        "persona": pid, "attempt": attempt, "spec": p.spec, "claimed_grade": p.grade, "intended": p.intended, "kind": p.kind,
        "items": items,
        "result": {"score": score, "ruler": RULER, "decision": {"passed": passed, "label": "ok" if passed else "нет"}},
        "audit": {
            "session": {"status": "DECIDED", "trust": None},
            "responses": [{"is_pretest": False, "is_correct": i % 2 == 0} for i in range(10)],
            "flags": [], "evidence": evidence or [],
        },
    }


def test_kappa_perfect_and_chance() -> None:
    assert an.kappa([0, 1, 2, 3], [0, 1, 2, 3], weighted=False) == 1.0
    assert an.kappa([2, 2, 2, 2], [2, 2, 2, 2], weighted=True) is None  # нет разброса — каппа не определена
    k = an.kappa([0, 1, 2, 3, 4, 2], [0, 2, 2, 3, 3, 2], weighted=True)
    assert k is not None and 0.7 < k < 1.0  # близкие полосы штрафуются слабее далёких


def test_band_uses_ruler_cuts() -> None:
    assert an.band(_run("P01", 1, 450, True)) == 2  # Junior: 400 ≤ 450 < 500
    assert an.band(_run("P01", 1, 283, False)) == 0
    assert an.band(_run("P01", 1, 600, True)) == 4


def test_analyze_retest_and_confusion() -> None:
    runs = {
        "P01": {1: _run("P01", 1, 430, True), 2: _run("P01", 2, 510, True)},  # Junior завышен, повтор различается на полосу
        "P03": {1: _run("P03", 1, 610, True), 2: _run("P03", 2, 605, True)},
        "C01": {1: _run("C01", 1, 380, False, [{"seq": 1, "kind": "twin_trace", "matched": "twin_key", "strength": "exact", "honest_p": 0.01}], 5)},
    }
    res = an.analyze(runs)
    assert res["retest"]["n"] == 2
    assert res["retest"]["exact"] == 0.5 and res["retest"]["within1"] == 1.0
    assert res["bias"]["junior"] == 0.5 and res["bias"]["senior"] == 0.0
    assert res["honest_false_alarms"] == {"runs": 4, "alarms": 0}
    assert res["cheaters"][0]["detected"] is True and res["cheater_detect_rate"] == 1.0
    assert res["cheaters"][0]["copied"] == 5


def test_honest_twin_trace_counts_as_false_alarm() -> None:
    ev = [{"seq": 1, "kind": "twin_trace", "matched": "twin_key", "strength": "exact", "honest_p": 0.01}]
    res = an.analyze({"P02": {1: _run("P02", 1, 520, True, ev)}})
    assert res["honest_false_alarms"]["alarms"] == 1


def test_render_without_runs_has_only_preregistration() -> None:
    text = an.render(None)
    assert "## 10. Пилот с ИИ-испытуемыми" in text and "предрегистрация" in text and "ещё не выполнены" in text
    assert "ожидаем завышение у младших персон" in text


def test_render_with_runs_and_splice(tmp_path: Path) -> None:
    runs = {"P01": {1: _run("P01", 1, 430, True), 2: _run("P01", 2, 440, True)}}
    text = an.render(an.analyze(runs))
    assert "Каппа Коэна (взвешенная)" in text and "P01" in text
    doc = tmp_path / "V.md"
    doc.write_text("# Док\n\n<!-- market-sim:begin -->\nM\n<!-- market-sim:end -->\n\n## Как воспроизвести\nX\n", encoding="utf-8")
    assert an.splice_docs(text, doc)
    first = doc.read_text(encoding="utf-8")
    assert first.index("market-sim:end") < first.index(an.MARK_BEGIN) < first.index("## Как воспроизвести")
    assert an.splice_docs(text, doc)  # повтор заменяет блок, не дублирует
    assert doc.read_text(encoding="utf-8") == first
    assert first.count(an.MARK_BEGIN) == 1


def test_persona_registry_covers_protocol() -> None:
    honest = [p for p in PERSONAS.values() if p.kind == "honest"]
    assert len(honest) == 12 and len(PERSONAS) == 14
    assert {(p.spec, p.intended) for p in honest} == {
        (s, g) for s in ("backend_python", "frontend_js", "qa", "devops") for g in ("junior", "middle", "senior")
    }
    protocol = (Path(an.__file__).parent / "PROTOCOL.md").read_text(encoding="utf-8")
    assert all(p.id in protocol for p in PERSONAS.values())
    json.dumps({k: v.actions for k, v in PERSONAS.items()})


def test_cheater_overrated_and_posthoc_split() -> None:
    """Завышенная категория (протокол §5.4) считается отдельно; попытки ≥ POSTHOC_FROM_ATTEMPT не входят в предрегистрированные доли."""
    hit = [{"seq": 1, "kind": "twin_trace", "matched": "twin_key", "strength": "exact", "honest_p": 0.02}]
    runs = {
        "C01": {
            1: _run("C01", 1, 560, True, hit, 10),  # Middle подтверждён при задуманном Junior — завышено
            2: _run("C01", 2, 420, False, hit, 10),
            an.POSTHOC_FROM_ATTEMPT: _run("C01", an.POSTHOC_FROM_ATTEMPT, 410, False, [], 10),  # без улики: в предрег. долю не идёт
        },
    }
    res = an.analyze(runs)
    assert res["cheater_overrated"] == {"pre": [1, 2], "post": [0, 1]}
    assert res["cheater_detect_rate"] == 1.0  # пост-хок прогон без улики долю не портит и не улучшает
    text = an.render(res)
    assert "получили 1 из 2 прогонов" in text and "10.4. Повтор «списывающих» после исправления (пост-хок)" in text
