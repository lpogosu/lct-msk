"""«Акт сверки» для методиста: показано / скопировано, подсветка литерала, оба ключа, вероятность, чек-лист."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest

from app.testing.anticheat.act import build_act, build_act_for
from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.twins import make_twin
from app.testing.bank.base import Instance
from tests.test_anticheat_support import PRICES_CODE, TinyFamily, code_instance, orders_rendered

CFG = load_anticheat_config()
FORBIDDEN = ("искусственн", "нейросет", "списал", "читер", "обман", "мошен", "θ", "виновен")


@pytest.fixture(scope="module")
def prices():
    fam, inst = code_instance(PRICES_CODE)
    twin = make_twin(fam, inst)
    assert twin is not None
    return fam, inst, twin


def _assert_no_accusation(text: str) -> None:
    low = text.lower()
    for word in FORBIDDEN:
        assert word.lower() not in low, f"в тексте акта есть «{word}»: {text}"
    assert not re.search(r"\bИИ\b", text)


def test_act_spans_highlight_the_changed_literal(prices):
    fam, inst, twin = prices
    act = build_act_for(fam, inst, twin, twin.twin_key)
    c = twin.changed_literal
    # диапазоны в составном тексте
    assert act.shown_span.text == c.from_ and act.clipboard_span.text == c.to
    assert act.shown_text[act.shown_span.start : act.shown_span.end] == c.from_
    assert act.clipboard_text[act.clipboard_span.start : act.clipboard_span.end] == c.to
    assert act.shown_span.start == act.clipboard_span.start == c.offset
    assert (act.shown_span.line, act.shown_span.col) == (c.line, c.col)
    # построчно: колонки показаны рядом, подсвечена ровно одна строка
    assert len(act.lines) == act.shown_text.count("\n") + 1 == act.clipboard_text.count("\n") + 1
    changed = [ln for ln in act.lines if ln.changed]
    assert len(changed) == 1
    ln = changed[0]
    assert ln.no == c.line and ln.shown_span and ln.clipboard_span
    assert ln.shown[ln.shown_span[0] : ln.shown_span[1]] == c.from_
    assert ln.clipboard[ln.clipboard_span[0] : ln.clipboard_span[1]] == c.to
    assert ln.shown_span[0] == ln.clipboard_span[0] == c.col
    assert all(x.shown == x.clipboard for x in act.lines if not x.changed)


def test_act_for_sql_cell_points_into_the_table_block():
    fam = TinyFamily()
    fam.format = "sql_result"
    rendered = orders_rendered()
    from app.testing.anticheat.executor import project_rows, run_query, tables_from_rendered

    ok, rows, _ = run_query(tables_from_rendered(rendered.table), rendered.code or "")
    assert ok
    inst = Instance(fam.id, 0, rendered, fam.normalize(project_rows(rows, "all_cells")), {})
    twin = make_twin(fam, inst)
    assert twin is not None
    act = build_act_for(fam, inst, twin, twin.twin_key)
    assert act.changed_literal["block"] == "table:orders" and act.changed_literal["column"] == "amount"
    ln = next(x for x in act.lines if x.changed)
    assert ln.shown[ln.shown_span[0] : ln.shown_span[1]] == twin.changed_literal.from_
    assert ln.clipboard[ln.clipboard_span[0] : ln.clipboard_span[1]] == twin.changed_literal.to
    assert "|" in ln.shown  # строка таблицы данных, а не кода запроса


def test_act_shows_both_keys_and_which_one_matched(prices):
    fam, inst, twin = prices
    act = build_act_for(fam, inst, twin, twin.twin_key, phase="after_warning")
    assert act.key_shown == inst.key and act.key_clipboard == twin.twin_key and act.key_shown != act.key_clipboard
    assert act.candidate_answer == twin.twin_key
    assert act.answer_class.kind == "twin_trace" and act.answer_class.matched == "twin_key"
    assert act.matched_ru == "ключ копии из буфера обмена"
    assert act.phase == "after_warning" and act.phase_ru == "после предупреждения"
    assert twin.twin_key in act.verdict_ru and inst.key in act.verdict_ru
    assert f"{twin.changed_literal.from_} → {twin.changed_literal.to}" in act.verdict_ru
    assert "наблюдение о тексте" in act.verdict_ru  # нейтральная формулировка, без обвинений
    _assert_no_accusation(act.verdict_ru)
    for before in ("before_warning", "unknown"):
        assert build_act(twin, answer=twin.twin_key, phase=before).phase_ru == CFG.text(f"phase_ru.{before}")


def test_act_for_every_answer_kind_is_free_of_accusations(prices):
    fam, inst, twin = prices
    cases = {
        "correct": (inst.key, "key"),
        "other": ("0 0 0", "none"),
        "misconception": ("1", "distractor"),
    }
    for kind, (answer, matched) in cases.items():
        act = build_act(
            twin, answer=answer, distractors={"1": "known_error"}, normalize=fam.normalize
        )
        assert act.answer_class.kind == kind and act.answer_class.matched == matched
        assert act.verdict_ru and "{" not in act.verdict_ru
        _assert_no_accusation(act.verdict_ru)
    assert "совпал с ключом для показанного условия" in build_act(twin, answer=inst.key).verdict_ru
    # слабый след: значение, которого нет на экране, — только контекст
    token = build_act(twin, answer=f"{twin.changed_literal.to} 1", normalize=fam.normalize)
    if token.answer_class.strength == "token":
        assert "только контекст" in token.verdict_ru
        _assert_no_accusation(token.verdict_ru)


def test_act_probability_line_is_modelled_not_a_verdict(prices):
    fam, inst, twin = prices
    act = build_act(twin, answer=twin.twin_key)
    assert act.p_honest == twin.honest_collision_estimate
    assert act.expected_per_10k == pytest.approx(act.p_honest * 10000)
    assert "на 10 000 сессий" in act.p_honest_text and "модельная оценка" in act.p_honest_text
    assert "вероятность, что вы" not in act.p_honest_text.lower()
    custom = build_act(twin, answer=twin.twin_key, p_honest=3.16e-5)
    assert "3,2·10⁻⁵" in custom.p_honest_text and "0,3 таких случаев" in custom.p_honest_text


def test_act_checklist_of_alternative_explanations(prices):
    fam, inst, twin = prices
    base = build_act(twin, answer=twin.twin_key, render_id="r1", twin_render_id="r1")
    assert [c.id for c in base.checklist] == ["self_paste", "misconception", "glyphs", "stale_clip", "a11y_mode"]
    assert {c.status for c in base.checklist} == {"ok"}
    sp = build_act(twin, answer=twin.twin_key, self_paste=True)
    assert {c.id: c.status for c in sp.checklist}["self_paste"] == "excluded"
    stale = build_act(twin, answer=twin.twin_key, render_id="r2", twin_render_id="r1")
    assert {c.id: c.status for c in stale.checklist}["stale_clip"] == "excluded"
    unknown = build_act(twin, answer=twin.twin_key)
    assert {c.id: c.status for c in unknown.checklist}["stale_clip"] == "unknown"
    a11y = build_act(twin, answer=twin.twin_key, a11y_mode=True)
    assert {c.id: c.status for c in a11y.checklist}["a11y_mode"] == "attention"
    misc = build_act(twin, answer="7", distractors={"7": "tag_x"})
    item = {c.id: c for c in misc.checklist}["misconception"]
    assert item.status == "attention" and "tag_x" in item.note_ru
    for act in (base, sp, stale, a11y, misc):
        for c in act.checklist:
            assert c.title_ru and c.status_ru and "{" not in c.note_ru
            _assert_no_accusation(c.note_ru)


def test_act_to_dict_is_json_and_candidate_view_is_minimal(prices):
    fam, inst, twin = prices
    t0 = datetime(2026, 10, 5, 12, 3, 14, tzinfo=UTC)
    act = build_act_for(
        fam, inst, twin, twin.twin_key, phase="after_warning", render_id="r1", twin_render_id="r1",
        copy_at=t0, answered_at=t0 + timedelta(seconds=38), warned_at=t0 - timedelta(minutes=5),
    )
    d = act.to_dict("methodist")
    json.dumps(d, ensure_ascii=False)  # сериализуется целиком
    assert d["keys"] == {"shown": inst.key, "clipboard": twin.twin_key}
    assert d["copy_at"] == t0.isoformat() and d["answered_at"] == (t0 + timedelta(seconds=38)).isoformat()
    assert d["mutation_class"] == "data_literal" and d["title_ru"] == "Акт сверки: показано и скопировано"
    assert d["shown_span"]["text"] == twin.changed_literal.from_
    cand = act.to_dict("candidate")
    assert set(cand) == {"variant_code", "message_ru", "actions"}
    assert "не соответствует показанному условию" in cand["message_ru"] and inst.variant_code in cand["message_ru"]
    assert twin.twin_key not in cand["message_ru"].replace(f"#{inst.variant_code}", "")
    assert cand == act.for_candidate()


def test_act_is_not_available_to_employer(prices):
    fam, inst, twin = prices
    act = build_act_for(fam, inst, twin, twin.twin_key)
    with pytest.raises(ValueError, match="только методисту и кандидату"):
        act.to_dict("employer")
