"""«Антиподсказчик» в тесте кандидата: двойник хранится на сервере и не утекает ни в один JSON, ответ двойника — улика и
неверный ответ, события copy/self_paste дают контекст акта, решения методиста, доступы."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.models.testing import Flag as FlagRow
from app.services.anticheat.models import AnticheatEvidence, AnticheatTwin
from app.services.testing.keystore import decrypt_key
from app.testing.anticheat.config import load_anticheat_config
from tests import test_anticheat_api_support as sup
from tests import test_api_testing_support as support
from tests.test_api_testing_support import do_survey, make_role, run_session, start

api = support.api
bank = support.bank

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def methodist(make_account, bank):
    acc = make_account()
    make_role(bank, acc.user_id, "methodist")
    return acc


def _session_with_copies(api, bank, make_account, *, copy_all=True):
    """Кандидат, копирующий задания с двойником в чат-бот: ответы по ключу копии."""
    sup.prepare_twins(bank)
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    run = run_session(api, bank, acc, s["id"], sup.copy_policy(copy_all))
    return acc, s, run


def test_clip_has_only_text_and_render_id_and_keys_never_leak(api, bank, make_account):
    acc, s, run = _session_with_copies(api, bank, make_account, copy_all=False)
    clipped = [(i, inst) for i, inst in zip(run.items, run.instances, strict=True) if i.get("clip")]
    cfg = load_anticheat_config()
    assert clipped, "ни одно задание не получило двойника"
    assert len(clipped) <= cfg.evidence.twin_rate_per_session  # twin_rate_per_session: не больше N двойников на сессию
    for item, inst in clipped:
        assert set(item["clip"]) == {"render_id", "clipboard_text"}  # ни позиции литерала, ни карты подмены, ни ключа
        row = bank.get(AnticheatTwin, inst.id)
        assert row.status == "ok" and row.shown_in_session_id == uuid.UUID(s["id"])
        assert item["clip"]["render_id"] == row.render_id
        # текст в буфере отличается от показанного ровно в одном токене, но API это не подсказывает
        assert item["clip"]["clipboard_text"] != (item.get("code") or "")
    # Ключи двойников, их хеши, шифртекст и данные подмены нигде в ответах кандидату
    text = json.dumps([b for _, b in api.bodies], ensure_ascii=False)
    for _, inst in clipped:
        row = bank.get(AnticheatTwin, inst.id)
        assert row.twin_key_hash not in text and row.secret_enc not in text
        for forbidden in ("twin_key", "secret_enc", "changed_literal", "err_trace", "mutation_class", "honest"):
            assert forbidden not in text
    support.assert_no_leaks(api.bodies, run.instances)
    # Остальные задания (без двойника) — clip = null
    assert all(i["clip"] is None for i in run.items if not i.get("clip"))


def test_no_twin_when_not_precomputed_and_lazy_off(api, bank, make_account):
    """Двойники берутся только из предрасчёта (lazy_budget_s = 0): нет записи — задание без подмены, без ошибок."""
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    run = run_session(api, bank, acc, s["id"], sup.screen_policy)
    assert run.items and all(i["clip"] is None for i in run.items)
    assert not bank.scalars(select(AnticheatEvidence)).all()


def test_twin_answer_is_wrong_for_the_test_and_creates_evidence_without_flags(api, bank, make_account):
    acc, s, run = _session_with_copies(api, bank, make_account)
    rows = sup.evidence_rows(bank, s["id"])
    assert rows
    twin_rows = [r for r in rows if r.kind == "twin_trace"]
    assert twin_rows and all(r.matched == "twin_key" and r.strength == "exact" for r in twin_rows)
    by_seq = {r.seq: r for r in sup.responses_of(bank, s["id"])}
    for r in twin_rows:
        assert by_seq[r.seq].is_correct is False  # самонаказание: ответ двойника — неверный ответ на экран
        assert r.review_status == "open"
    # Вес улики в доверии 0 до калибровки: ни одного флага «двойника» (и вообще флагов от хуков) нет
    flags = list(bank.scalars(select(FlagRow).where(FlagRow.session_id == uuid.UUID(s["id"]))))
    assert not any(f.kind in ("clipboard", "twin", "clipboard_twin") for f in flags)
    assert not any("двойник" in (f.details or {}).get("message", "") for f in flags)
    # Кандидату результат/разбор не раскрывает улику: слов про буфер и двойника нет
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert "двойник" not in json.dumps(res, ensure_ascii=False).lower()


def test_screen_solver_leaves_no_twin_evidence(api, bank, make_account):
    """Решал по экрану (или со скриншота): ключ верный, улика двойника не возникает."""
    acc, s, run = _session_with_copies(api, bank, make_account, copy_all=False)
    rows = sup.evidence_rows(bank, s["id"])
    assert rows and all(r.kind == "correct" for r in rows)


def test_reconciliation_list_and_act_for_methodist(api, bank, make_account, methodist):
    acc, s, run = _session_with_copies(api, bank, make_account)
    lst = api.get("/api/lab/reconciliations", methodist)
    mine = [i for i in lst["items"] if i["session_id"] == s["id"]]
    assert mine and lst["counts"]["open"] >= len(mine)
    row = mine[0]
    assert row["kind"] == "twin_trace" and row["matched"] == "twin_key" and row["review_status"] == "open"
    assert row["headline_ru"] == "Ответ совпал с копией, а не с экраном"
    act = api.get(f"/api/lab/reconciliations/{row['id']}", methodist)
    # Оба ключа, ответ и совпадение
    assert act["keys"]["shown"] != act["keys"]["clipboard"]
    assert act["candidate_answer"] == act["keys"]["clipboard"] or act["matched"] == "twin_key"
    assert act["matched"] == "twin_key" and act["kind"] == "twin_trace" and act["auto_ban"] is False
    # Подсвеченный литерал: на одной из строк блока есть диапазоны слева и справа
    changed = [ln for ln in act["lines"] if ln["changed"]]
    assert changed and changed[0]["shown_span"] and changed[0]["clipboard_span"]
    ln = changed[0]
    assert ln["shown"][ln["shown_span"][0] : ln["shown_span"][1]] == act["changed_literal"]["from"]
    assert ln["clipboard"][ln["clipboard_span"][0] : ln["clipboard_span"][1]] == act["changed_literal"]["to"]
    # Четыре числа под ключами (Figma 39:2): шанс, фаза, засчитано неверно, вес 0
    stats = {x["id"]: x for x in act["stats"]}
    assert set(stats) == {"probability", "phase", "scored", "weight"}
    assert stats["scored"]["value"] == "неверно" and stats["weight"]["value"] == "вес 0"
    assert act["session"]["context_only"] is True and act["session"]["trust_weight"] == 0
    assert {c["id"] for c in act["checklist"]} == {"self_paste", "misconception", "glyphs", "stale_clip", "a11y_mode"}
    # Слов «ИИ» и «списал» в формулировках нет: только наблюдаемые факты
    visible = " ".join([act["verdict_ru"], act["means_ru"], act["headline_ru"]]).lower()
    assert "списал" not in visible and "читер" not in visible
    # Фильтр по сессии и по решению
    assert api.get(f"/api/lab/reconciliations?session_id={s['id']}&include_sandbox=false", methodist)["items"]
    assert not api.get(f"/api/lab/reconciliations?session_id={uuid.uuid4()}", methodist)["items"]
    assert not [i for i in api.get("/api/lab/reconciliations?status=dismissed", methodist)["items"] if i["session_id"] == s["id"]]


def _run_with_copy_events(api, bank, acc, sid, stale_first: bool = False) -> list[int]:
    """Кандидат копирует каждое задание с двойником (событие copy с render_id) и отвечает ключом копии."""
    seen: list[int] = []
    for _ in range(30):
        nxt = api.get(f"/api/tests/sessions/{sid}/next", acc)
        item = nxt["item"]
        if item is None:
            break
        inst = support.instance_for(bank, sid, item["seq"])
        if item.get("clip"):
            support.advance(5)
            render_id = "rd-old" if stale_first and not seen else item["clip"]["render_id"]
            api.post(
                f"/api/tests/sessions/{sid}/events",
                acc,
                {"events": [{"type": "copy", "payload": {"seq": item["seq"], "render_id": render_id}}]},
            )
            support.advance(20)
            seen.append(item["seq"])
            ans = sup.twin_key_of(bank, inst)
        else:
            ans = decrypt_key(inst.key_enc)
        res = api.post(f"/api/tests/sessions/{sid}/answer", acc, {"seq": item["seq"], "answer": ans})
        if res["finished"]:
            break
    return seen


def test_copy_event_gives_warning_context_before_after(api, bank, make_account, methodist):
    """Первое копирование — момент предупреждения (тост): задание, на котором оно случилось, «до предупреждения»,
    следующие — «после»; время копирования берётся из журнала событий."""
    sup.prepare_twins(bank)
    acc = make_account()
    do_survey(api, acc)
    sid = start(api, acc, "middle")["id"]
    seen = _run_with_copy_events(api, bank, acc, sid)
    assert len(seen) >= 2, "нужно хотя бы два задания с двойником"
    rows = {r["seq"]: r for r in api.get(f"/api/lab/reconciliations?session_id={sid}", methodist)["items"]}
    assert rows[seen[0]]["phase"] == "before_warning"
    assert rows[seen[1]]["phase"] == "after_warning"
    act = api.get(f"/api/lab/reconciliations/{rows[seen[1]]['id']}", methodist)
    assert act["copy_at"] and act["warned_at"] and act["answered_at"] and act["phase"] == "after_warning"
    stats = {x["id"]: x for x in act["stats"]}
    assert stats["phase"]["value"] in ("повторно", "впервые")
    assert {c["id"]: c for c in act["checklist"]}["stale_clip"]["status"] == "ok"  # копия относилась к этому же показу
    first = api.get(f"/api/lab/reconciliations/{rows[seen[0]]['id']}", methodist)
    assert {x["id"]: x for x in first["stats"]}["phase"]["value"] == "впервые"
    assert first["session"]["state"] in ("evidence", "review_candidate", "warning")
    assert first["session"]["trust_weight"] == 0 and first["session"]["suggested_level_ru"]


def test_stale_clip_render_is_excluded_from_evidence(api, bank, make_account, methodist):
    """Копия от другого показа (render_id не совпал) — не улика: в чек-листе «исключён», в попадания не считается."""
    sup.prepare_twins(bank)
    acc = make_account()
    do_survey(api, acc)
    sid = start(api, acc, "middle")["id"]
    seen = _run_with_copy_events(api, bank, acc, sid, stale_first=True)
    assert len(seen) >= 2
    rows = {r["seq"]: r for r in api.get(f"/api/lab/reconciliations?session_id={sid}", methodist)["items"]}
    act = api.get(f"/api/lab/reconciliations/{rows[seen[0]]['id']}", methodist)
    assert {c["id"]: c for c in act["checklist"]}["stale_clip"]["status"] == "excluded"
    from app.services.anticheat import evidence as ev

    ctx = ev.session_ctx(bank, uuid.UUID(sid))
    first = next(e for e in ctx.summary.items if e.seq == seen[0])
    assert first.excluded == "stale_clip" and not first.counted


def test_self_paste_excludes_the_hit(api, bank, make_account, methodist):
    acc, s, run = _session_with_copies(api, bank, make_account)
    # Событие self_paste пришло после ответа (пачки приходят с задержкой) — всё равно исключает ответ из улики
    clip_seqs = [i["seq"] for i in run.items if i.get("clip")]
    assert clip_seqs
    # сессия закрыта: события уже не принимаются, поэтому проверяем сам разбор журнала на живой (незакрытой) сессии
    from app.core.clock import clock
    from app.models.testing import SessionEvent as EventRow
    from app.services.anticheat import evidence as ev

    bank.add(EventRow(session_id=uuid.UUID(s["id"]), ts=clock.now(), type="self_paste", payload={"seq": clip_seqs[0]}))
    bank.flush()
    ctx = ev.session_ctx(bank, uuid.UUID(s["id"]))
    excluded = [e for e in ctx.summary.items if e.seq == clip_seqs[0]][0]
    assert excluded.excluded == "self_paste" and not excluded.counted
    rows = api.get(f"/api/lab/reconciliations?session_id={s['id']}", methodist)["items"]
    row = next(r for r in rows if r["seq"] == clip_seqs[0])
    act = api.get(f"/api/lab/reconciliations/{row['id']}", methodist)
    assert {c["id"]: c for c in act["checklist"]}["self_paste"]["status"] == "excluded"


def test_lab_endpoints_access_control(api, bank, make_account, methodist):
    acc, s, run = _session_with_copies(api, bank, make_account)
    emp = make_account(role="employer")
    row = api.get("/api/lab/reconciliations", methodist)["items"][0]
    urls = [
        ("GET", "/api/lab/reconciliations", None),
        ("GET", f"/api/lab/reconciliations/{row['id']}", None),
        ("POST", f"/api/lab/reconciliations/{row['id']}/review", {"action": "dismiss"}),
        ("GET", f"/api/lab/sessions/{s['id']}/verification-kit", None),
        ("GET", "/api/lab/twins/coverage", None),
        ("POST", "/api/lab/twins/precompute", {}),
        ("GET", "/api/lab/validation/report", None),
    ]
    for method, url, body in urls:
        for who, code in ((None, 401), (acc, 403), (emp, 403)):
            r = api.client.request(method, url, headers=who.headers if who else {}, json=body)
            assert r.status_code == code, (method, url, who and who.email, r.status_code, r.text)
    # Кандидат не видит улик о себе: ни в результате, ни в разборе, ни в «Мой грейд»
    for url in (f"/api/tests/sessions/{s['id']}/result", f"/api/tests/sessions/{s['id']}/review", "/api/tests/qualification"):
        body = json.dumps(api.get(url, acc, expect=None), ensure_ascii=False)
        assert "twin" not in body and "двойник" not in body.lower() and "reconcil" not in body


def test_review_actions_are_human_decisions_with_audit(api, bank, make_account, methodist):
    acc, s, run = _session_with_copies(api, bank, make_account)
    row = next(r for r in api.get("/api/lab/reconciliations", methodist)["items"] if r["session_id"] == s["id"])
    url = f"/api/lab/reconciliations/{row['id']}/review"
    a = api.post(url, methodist, {"action": "assign_verification", "note": "Предложить подтверждение"})
    assert a["review_status"] == "verification_assigned" and a["review_note"] == "Предложить подтверждение"
    assert [x["id"] for x in a["actions"]] == ["reopen"]
    assert api.get("/api/lab/reconciliations?status=verification_assigned", methodist)["counts"]["verification_assigned"] >= 1
    d = api.post(url, methodist, {"action": "dismiss"})
    assert d["review_status"] == "dismissed" and d["reviewed_at"]
    r = api.post(url, methodist, {"action": "reopen"})
    assert r["review_status"] == "open" and [x["id"] for x in r["actions"]] == ["assign_verification", "dismiss"]
    api.post(url, methodist, {"action": "ban"}, expect=422)  # бана нет в словаре действий
    api.post(url, methodist, {"action": "dismiss", "note": "x" * 501}, expect=422)
    api.get(f"/api/lab/reconciliations/{uuid.uuid4()}", methodist, expect=404)
    api.get("/api/lab/reconciliations/not-a-uuid", methodist, expect=404)
    from app.models.users import AuditLog

    assert bank.scalars(select(AuditLog).where(AuditLog.action == "lab.anticheat_review")).all()
    # Решение методиста не меняет ни результат теста, ни статус квалификации кандидата
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert "двойник" not in json.dumps(res, ensure_ascii=False).lower()


def test_coverage_and_precompute_endpoint(api, bank, make_account, methodist):
    sup.prepare_twins(bank)
    cov = api.get("/api/lab/twins/coverage", methodist)
    fams = {f["family_id"]: f for f in cov["families"]}
    ready = [f for f in fams.values() if f["twins_ok"]]
    assert ready and cov["totals"]["twins_ok"] >= len(ready)
    assert all(f["mechanism"] in ("own", "generic") for f in ready)
    assert any(f["mechanism"] == "none" and f["reason_ru"] for f in fams.values())
    own = [f for f in fams.values() if f["mechanism"] == "own"]
    assert own, "семейства QA и DevOps со своим Family.twin() должны быть в покрытии"
    assert "twin_key" not in json.dumps(cov) and "clipboard" not in json.dumps(cov)
    r = api.post("/api/lab/twins/precompute", methodist, {"family_id": "py.defaults.mutable", "per_family": 5})
    assert r["accepted"] is True  # в тестах сессия не привязана к Engine — фон не стартует, но запрос принят
    api.post("/api/lab/twins/precompute", methodist, {"family_id": "no.such"}, expect=404)
