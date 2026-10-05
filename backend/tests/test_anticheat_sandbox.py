"""Песочница жюри «Попробуй списать»: мини-тест с двойниками, уникальный для проверяющего, диагноз по каналам."""

from __future__ import annotations

import json
import re

import pytest

from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.evidence import tokens_of
from app.testing.anticheat.sandbox import build_sandbox, sandbox_pool, score_sandbox
from app.testing.bank.registry import all_families

CFG = load_anticheat_config()
REG = all_families()


@pytest.fixture(scope="module")
def sandbox():
    return build_sandbox("reviewer-1")


def test_sandbox_has_four_items_from_different_families_each_with_twin(sandbox):
    assert len(sandbox.items) == CFG.sandbox.n_items == 4
    assert [i.no for i in sandbox.items] == [1, 2, 3, 4]
    assert len({i.family_id for i in sandbox.items}) == 4
    langs = [i.instance.rendered.language for i in sandbox.items]
    assert langs == list(CFG.sandbox.language_order)  # python, javascript, sql, python
    for it in sandbox.items:
        assert it.twin.twin_key != it.twin.key
        assert it.twin.family_id == it.family_id and it.instance.variant_code == it.variant_code
        assert it.instance.rendered.answer_mode == "text"
    assert sandbox.demo_assisted is True  # такие сессии исключаются из метрик


def test_sandbox_is_deterministic_per_reviewer_seed_and_unique_across_reviewers(sandbox):
    again = build_sandbox("reviewer-1")
    assert again.test_id == sandbox.test_id
    assert [(i.family_id, i.seed, i.twin.twin_key) for i in again.items] == [
        (i.family_id, i.seed, i.twin.twin_key) for i in sandbox.items
    ]
    ids = {sandbox.test_id}
    for n in range(2, 7):
        other = build_sandbox(f"reviewer-{n}")
        ids.add(other.test_id)
        assert [(i.family_id, i.seed) for i in other.items] != [(i.family_id, i.seed) for i in sandbox.items]
    assert len(ids) == 6  # у каждого проверяющего свой набор
    assert build_sandbox(12345).test_id == build_sandbox(12345).test_id  # зерно может быть числом


def test_sandbox_pool_covers_python_javascript_and_sql():
    pool = sandbox_pool(REG, CFG)
    assert {"python", "javascript", "sql"} <= set(pool)
    assert all(len(v) >= 3 for k, v in pool.items() if k in ("python", "javascript", "sql"))


def test_client_payload_has_clip_but_no_keys(sandbox):
    payload = sandbox.to_client()
    assert payload["demo_assisted"] is True and len(payload["items"]) == 4
    assert "Списывайте как угодно" in payload["banner_ru"] and "с ноутбука" in payload["readme_note_ru"]
    for item, src in zip(payload["items"], sandbox.items, strict=True):
        assert item["clip"]["text"] == src.twin.clipboard_text and item["clip"]["map"]
        assert item["clip"]["render_id"].startswith(sandbox.test_id)
        assert item["prompt"] and item["answer_mode"] == "text"
        assert not any("key" in k for k in item), item.keys()
        assert not any("key" in k for k in item["clip"])
    dumped = json.dumps(payload, ensure_ascii=False)
    for it in sandbox.items:
        assert f'"{it.twin.twin_key}"' not in dumped and f'"{it.twin.key}"' not in dumped


def test_scoring_diagnoses_each_channel(sandbox):
    a, b, c, d = sandbox.items
    answers = {
        1: a.twin.twin_key,  # скопировал текст из буфера в чат-бот и ввёл ответ
        2: b.instance.key,  # решил по экрану / скриншот / ручной ввод
        3: "zzz",  # ни то ни другое
        4: "",  # без ответа
    }
    res = score_sandbox(sandbox, answers)
    assert (res.total, res.caught, res.correct_uncaught) == (4, 1, 1)
    assert res.summary_ru == "Поймано двойником: 1 из 4. Верных ответов, которые двойник не ловит: 1."
    caught, correct, other, blank = res.items
    assert caught.caught and caught.channel == "clipboard_text" and caught.channel_ru == "копирование текста"
    assert "пойман двойником" in caught.diagnosis_ru and a.twin.twin_key in caught.diagnosis_ru
    assert a.twin.key in caught.diagnosis_ru  # «на экране было …»
    assert caught.act is not None and caught.act.answer_class.matched == "twin_key"
    assert not correct.caught and correct.channel == "screen_or_retyped" and correct.act is None
    assert "скриншот" in correct.diagnosis_ru and "подтверждение за 5 минут" in correct.diagnosis_ru
    assert not other.caught and other.channel == "unknown" and "двойник не сработал" in other.diagnosis_ru
    assert blank.diagnosis_ru == "Ответа нет." and blank.answer_class is None
    json.dumps(res.to_dict(), ensure_ascii=False)


def test_scoring_normalizes_answers_like_the_family(sandbox):
    a = sandbox.items[0]
    spaced = "  " + a.twin.twin_key.replace(" ", "   ") + " "
    res = score_sandbox(sandbox, {"1": spaced})
    assert res.items[0].caught  # лишние пробелы не мешают: как у настоящего теста
    assert res.items[1].diagnosis_ru == "Ответа нет."  # ключи ответов могут быть и строками


def test_misconception_and_token_trace_are_not_reported_as_caught(sandbox):
    it = next((i for i in sandbox.items if i.instance.distractors), None)
    if it is not None:
        wrong = next(iter(it.instance.distractors))
        r = score_sandbox(sandbox, {it.no: wrong}).items[it.no - 1]
        assert r.channel == "misconception" and not r.caught
    it = sandbox.items[0]
    to = it.twin.changed_literal.to
    seen = tokens_of(it.twin.shown_text) | tokens_of(it.twin.key)
    if to not in seen:
        r = score_sandbox(sandbox, {1: f"{to} 0"}).items[0]
        assert r.channel == "clipboard_text" and not r.caught  # слабый след: только контекст
        assert re.search(r"только контекст", r.diagnosis_ru)


def test_diagnosis_texts_have_no_accusations_or_theta(sandbox):
    res = score_sandbox(sandbox, {1: sandbox.items[0].twin.twin_key, 2: sandbox.items[1].instance.key, 3: "q"})
    blob = json.dumps(res.to_dict(), ensure_ascii=False)
    for word in ("θ", "списал", "читер", "обман"):
        assert word not in blob
    assert not re.search(r"\bИИ\b", blob)
