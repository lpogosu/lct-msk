"""API тестирования: целостность (spec-testing §3.10) — «чужой ключ» между двумя аккаунтами с источником,
события страницы → мягкий флаг, «оспорить», решение методиста, карантин семейства, точки anti-assist."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.clock import clock
from app.models.enums import FlagStatus
from app.models.testing import DriftEvent, ItemFamilyParams, ItemInstance, SessionEvent
from app.models.testing import Flag as FlagRow
from app.models.testing import TestSession as SessionRow
from app.services.testing import anticheat_hooks
from app.services.testing.integrity import maybe_quarantine_family
from app.services.testing.keystore import decrypt_key
from app.services.testing.qualification import get_qualification_snapshot
from tests import test_api_testing_support as support
from tests.test_api_testing_support import (
    by_ability,
    do_survey,
    make_role,
    restrict_bank,
    run_session,
    start,
)

# Фикстуры общего модуля (присваиванием, а не импортом: так их видит pytest и не ругается линтер)
api = support.api
bank = support.bank
fixed_seed = support.fixed_seed

pytestmark = pytest.mark.usefixtures("frozen_clock")
SPEC = "backend_python"


def _distinct_key_families(db) -> set[str]:
    """По 2 семейства на модуль, у которых все ключи пула различны (детектор работает при коллизиях < 2%)."""
    rows = db.execute(
        select(ItemInstance.family_id, func.count(), func.count(func.distinct(ItemInstance.key_hash))).group_by(
            ItemInstance.family_id
        )
    ).all()
    distinct = {fid for fid, n, d in rows if n == d}
    by_module: dict[str, list[str]] = {}
    for p in db.scalars(
        select(ItemFamilyParams).where(ItemFamilyParams.specialization == SPEC).order_by(ItemFamilyParams.family_id)
    ):
        if p.family_id in distinct:
            by_module.setdefault(p.module, []).append(p.family_id)
    return {f for fams in by_module.values() for f in fams[:2]}


def test_foreign_key_from_another_account_is_detected_with_source(api, bank, make_account, fixed_seed):
    keep = _distinct_key_families(bank)
    restrict_bank(bank, SPEC, keep)
    anna, ilya = make_account(), make_account()
    for acc in (anna, ilya):
        do_survey(api, acc)
    sa = start(api, anna, "middle")
    run_session(api, bank, anna, sa["id"], by_ability(0.0))

    used: list[tuple[str, uuid.UUID]] = []
    honest = by_ability(0.0)

    def leaked(db, inst, item):
        """Илья вставляет ответ Анны: ключ её экземпляра того же семейства (у Ильи другой вариант)."""
        if not used:
            src = db.scalars(
                select(ItemInstance).where(
                    ItemInstance.family_id == inst.family_id, ItemInstance.issued_to_user_id == anna.user_id
                )
            ).first()
            if src is not None and src.key_hash != inst.key_hash:
                used.append((inst.family_id, src.issued_to_session_id))
                return decrypt_key(src.key_enc)
        return honest(db, inst, item)

    si = start(api, ilya, "middle")
    run_session(api, bank, ilya, si["id"], leaked)
    assert used, "у Ильи не нашлось семейства, которое видела Анна"
    family_id, source_session = used[0]

    res = api.get(f"/api/tests/sessions/{si['id']}/result", ilya)
    if res["session"]["status"] == "EXTENSION_OFFERED":
        # Предложение добора действует до 24 ч, но с «чужим ключом» кандидата нет в витрине уже сейчас
        assert "методист" in res["message"]
        snap = get_qualification_snapshot(bank, ilya.user_id, SPEC)
        assert snap is not None and snap.visible is False
        assert api.get(f"/api/tests/qualification/{SPEC}", ilya)["hidden"] is True
        api.post(f"/api/tests/sessions/{si['id']}/extension", ilya, {"decision": "decline"})
        res = api.get(f"/api/tests/sessions/{si['id']}/result", ilya)
    assert res["session"]["status"] == "UNDER_REVIEW"
    assert res["visible"] is False and "методиста" in res["message"]
    api.get(f"/api/tests/sessions/{si['id']}/review", ilya, expect=409)

    q = api.get(f"/api/tests/qualification/{SPEC}", ilya)
    assert q["hidden"] is True
    mine = [f for f in q["flags"] if f["kind"] == "foreign_key"]
    assert len(mine) == 1 and mine[0]["severity"] == "hard" and mine[0]["can_dispute"]
    assert "source_session_id" not in mine[0]  # источник — чужая сессия, кандидату не показываем
    snap = get_qualification_snapshot(bank, ilya.user_id, SPEC)
    assert snap is not None and snap.visible is False

    # У Анны флагов нет: верные ответы на свой вариант
    assert not api.get(f"/api/tests/qualification/{SPEC}", anna)["flags"]

    # Методист видит источник «чужого ключа» и решает
    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    flags = api.get("/api/lab/flags?status=open", methodist)
    fk = next(f for f in flags if f["kind"] == "foreign_key")
    assert fk["session_id"] == si["id"] and fk["family_id"] == family_id
    assert fk["source_session_id"] == str(source_session) == sa["id"]
    trace = api.get(f"/api/lab/sessions/{si['id']}/trace", methodist)
    assert any(f["kind"] == "foreign_key" for f in trace["flags"])

    out = api.post(f"/api/lab/flags/{fk['id']}/resolve", methodist, {"decision": "confirm", "comment": "совпал ключ"})
    assert out["review_resolved"] is True and out["session_status"] == "INVALIDATED"
    res = api.get(f"/api/tests/sessions/{si['id']}/result", ilya)
    assert res["visible"] is False and "аннулирован" in res["message"]
    q = api.get(f"/api/tests/qualification/{SPEC}", ilya)
    assert q["hidden"] is False and q["grade"] is None
    api.post(f"/api/lab/flags/{fk['id']}/resolve", methodist, {"decision": "dismiss"}, expect=409)


def test_paste_event_soft_flag_dispute_and_dismiss_restores_trust(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    url = f"/api/tests/sessions/{s['id']}"
    api.get(f"{url}/next", acc)
    now = clock.now()
    ev = api.post(
        f"{url}/events",
        acc,
        {
            "events": [
                {"type": "blur", "at": now.isoformat()},
                {"type": "focus", "at": (now + timedelta(seconds=3)).isoformat()},
                {"type": "paste", "payload": {"field": "answer"}},
                {"type": "copy", "at": "2026-10-05T09:00:00"},  # без часового пояса — как UTC
            ]
        },
    )
    assert ev == {"accepted": 4, "ignored": 0, "status": "IN_PROGRESS"}
    rows = bank.scalars(select(SessionEvent).where(SessionEvent.session_id == uuid.UUID(s["id"]))).all()
    assert {r.type for r in rows} == {"blur", "focus", "paste", "copy"}
    assert all(r.payload.get("seq") == 1 for r in rows)  # событие привязано к текущему заданию

    run_session(api, bank, acc, s["id"], by_ability(3.0))
    res = api.get(f"{url}/result", acc)
    if res["session"]["status"] == "EXTENSION_OFFERED":
        api.post(f"{url}/extension", acc, {"decision": "decline"})
    q = api.get(f"/api/tests/qualification/{SPEC}", acc)
    paste = next(f for f in q["flags"] if f["kind"] == "paste")
    assert paste["severity"] == "soft" and paste["message"] == "Вставка текста в поле ответа"
    assert q["trust"] == "сниженное"

    # Закрытая сессия событий не принимает
    late = api.post(f"{url}/events", acc, {"events": [{"type": "blur"}]})
    assert late["accepted"] == 0 and late["ignored"] == 1

    # «Оспорить»: один раз, только свой открытый флаг
    other = make_account()
    api.post(f"/api/tests/flags/{paste['id']}/dispute", other, {"text": "чужой флаг"}, expect=404)
    d = api.post(f"/api/tests/flags/{paste['id']}/dispute", acc, {"text": "вставлял из своего черновика"})
    assert d["dispute_text"] == "вставлял из своего черновика" and d["can_dispute"] is False
    err = api.post(f"/api/tests/flags/{paste['id']}/dispute", acc, {"text": "ещё раз"}, expect=409)
    assert err["code"] == "flag_already_disputed"

    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    queue = api.get("/api/lab/flags", methodist)
    assert queue[0]["id"] == paste["id"]  # оспоренные — первыми в очереди
    out = api.post(f"/api/lab/flags/{paste['id']}/resolve", methodist, {"decision": "dismiss"})
    assert out["flag"]["status"] == "dismissed"
    assert out["message"] == "Флаг снят, доверие к результату восстановлено"
    q = api.get(f"/api/tests/qualification/{SPEC}", acc)
    assert [f["status"] for f in q["flags"]] == ["dismissed"]
    assert q["trust"] == "высокое"  # снятый флаг доверие не снижает


def test_anti_assist_hooks_are_called(api, bank, make_account, monkeypatch):
    rendered: list[str] = []
    seen: list[str] = []

    def clipboard_variant(instance):
        rendered.append(instance.family_id)
        # Пример будущей защиты: невидимая метка в тексте задания (новый словарь — JSONB без отслеживания правок)
        instance.rendered = {**instance.rendered, "prompt": instance.rendered["prompt"] + "​"}

    def on_client_event(session, event):
        seen.append(event.type)
        if event.type == "copy":
            return [anticheat_hooks.AntiAssistSignal(kind="clipboard", message_ru="Копирование текста задания")]
        return []

    monkeypatch.setattr(anticheat_hooks, "clipboard_variant", clipboard_variant)
    monkeypatch.setattr(anticheat_hooks, "on_client_event", on_client_event)
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    item = api.get(f"/api/tests/sessions/{s['id']}/next", acc)["item"]
    assert len(rendered) == 1
    again = api.get(f"/api/tests/sessions/{s['id']}/next", acc)["item"]
    assert again["seq"] == item["seq"] == 1 and len(rendered) == 1  # то же задание — повторно не рендерится
    assert item["prompt"].endswith("​") and again["prompt"] == item["prompt"]  # правка хука сохранена
    api.post(f"/api/tests/sessions/{s['id']}/events", acc, {"events": [{"type": "focus"}, {"type": "copy"}]})
    assert seen == ["focus", "copy"]
    flags = bank.scalars(select(FlagRow).where(FlagRow.session_id == uuid.UUID(s["id"]))).all()
    assert [(f.kind, f.severity) for f in flags] == [("clipboard", "soft")]


def test_anti_assist_answer_hook_clip_and_fault_isolation(api, bank, make_account, monkeypatch):
    """Точки для «двойника в буфере» (spec-antiassist §7): clip уходит в задание без изменений, каждый
    принятый ответ проходит через on_answer, details сигнала видит только методист, сбой хука не мешает
    ответить на задание."""
    answers: list[tuple[str, bool]] = []

    def clipboard_variant(instance):
        instance.rendered = {**instance.rendered, "clip": {"render_id": "r-1", "text": "x = 41"}}

    def on_answer(session, instance, answer, *, normalized, correct):
        answers.append((instance.family_id, correct))
        if len(answers) == 1:
            return [
                anticheat_hooks.AntiAssistSignal(
                    kind="clipboard_twin", message_ru="Ответ совпал с изменённой копией задания", details={"twin": "x′"}
                )
            ]
        raise RuntimeError("сбой расширения")

    monkeypatch.setattr(anticheat_hooks, "clipboard_variant", clipboard_variant)
    monkeypatch.setattr(anticheat_hooks, "on_answer", on_answer)
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    url = f"/api/tests/sessions/{s['id']}"
    item = api.get(f"{url}/next", acc)["item"]
    assert item["clip"] == {"render_id": "r-1", "text": "x = 41"}
    assert api.post(f"{url}/answer", acc, {"seq": 1, "answer": "1"})["accepted"]
    assert api.post(f"{url}/answer", acc, {"seq": 1, "answer": "1"})["duplicate"]  # повтор хук не зовёт
    item2 = api.get(f"{url}/next", acc)["item"]
    res = api.post(f"{url}/answer", acc, {"seq": item2["seq"], "answer": "2"})  # хук упал — ответ принят
    assert res["accepted"] and not res["duplicate"]
    assert len(answers) == 2
    flag = bank.scalars(select(FlagRow).where(FlagRow.session_id == uuid.UUID(s["id"]))).one()
    assert flag.kind == "clipboard_twin" and flag.details["signal"] == {"twin": "x′"}
    q = api.get(f"/api/tests/qualification/{SPEC}", acc)
    mine = next(f for f in q["flags"] if f["kind"] == "clipboard_twin")
    assert mine["message"] == "Ответ совпал с изменённой копией задания" and "signal" not in json.dumps(mine)
    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    tr = api.get(f"/api/lab/sessions/{s['id']}/trace", methodist)
    lab_flag = next(f for f in tr["flags"] if f["kind"] == "clipboard_twin")
    assert lab_flag["evidence"] == {"twin": "x′"}  # карточка доказательства — методисту


def test_flag_is_resolved_only_after_the_session_closes(api, bank, make_account, monkeypatch):
    """Подтверждение флага посреди теста оставило бы сессию UNDER_REVIEW без открытых флагов — проверку
    нечем было бы завершить, кандидат скрыт навсегда. Поэтому решение — после закрытия сессии."""

    def on_client_event(session, event):
        if event.type == "copy_bypass":
            return [anticheat_hooks.AntiAssistSignal(kind="copy_bypass", message_ru="Копирование мимо обработчика", severity="hard")]
        return []

    monkeypatch.setattr(anticheat_hooks, "on_client_event", on_client_event)
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    api.get(f"/api/tests/sessions/{s['id']}/next", acc)
    api.post(f"/api/tests/sessions/{s['id']}/events", acc, {"events": [{"type": "copy_bypass"}]})
    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    flag = next(f for f in api.get("/api/lab/flags", methodist) if f["session_id"] == s["id"])
    assert flag["severity"] == "hard" and flag["session_status"] == "IN_PROGRESS"
    err = api.post(f"/api/lab/flags/{flag['id']}/resolve", methodist, {"decision": "confirm"}, expect=409)
    assert err["code"] == "session_not_closed"

    run_session(api, bank, acc, s["id"], by_ability(3.0))
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    if res["session"]["status"] == "EXTENSION_OFFERED":
        api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "decline"})
    assert api.get(f"/api/tests/sessions/{s['id']}", acc)["status"] == "UNDER_REVIEW"
    out = api.post(f"/api/lab/flags/{flag['id']}/resolve", methodist, {"decision": "dismiss"})
    assert out["review_resolved"] is True and out["session_status"] == "FINAL"
    q = api.get(f"/api/tests/qualification/{SPEC}", acc)
    assert q["hidden"] is False and q["grade"]["code"] == "middle" and q["trust"] == "сниженное"


def test_hard_flag_hides_candidate_at_decision_while_offer_is_open(api, bank, make_account, monkeypatch):
    """Сессия с жёстким флагом решена и ждёт решения о доборе (до 24 ч): кандидат скрыт из витрины сразу."""
    monkeypatch.setattr(
        anticheat_hooks,
        "on_client_event",
        lambda session, event: [
            anticheat_hooks.AntiAssistSignal(kind="copy_bypass", message_ru="Копирование мимо обработчика", severity="hard")
        ],
    )
    acc = make_account()
    do_survey(api, acc, months=60)
    s = start(api, acc, "middle")
    api.get(f"/api/tests/sessions/{s['id']}/next", acc)
    api.post(f"/api/tests/sessions/{s['id']}/events", acc, {"events": [{"type": "copy_bypass"}]})
    run_session(api, bank, acc, s["id"], by_ability(3.0))
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["session"]["status"] == "EXTENSION_OFFERED" and res["offers"]  # уверенный проход → добор вверх
    assert "методист" in res["message"]
    q = api.get(f"/api/tests/qualification/{SPEC}", acc)
    assert q["hidden"] is True and q["trust"] == "на проверке"
    snap = get_qualification_snapshot(bank, acc.user_id, SPEC)
    assert snap is not None and snap.visible is False
    # Добор можно принять; основная часть закрывается на проверку, кандидат остаётся скрытым
    ext = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "up"})
    assert ext["session"]["status"] == "UNDER_REVIEW"
    assert api.get(f"/api/tests/qualification/{SPEC}", acc)["hidden"] is True


def test_methodist_decision_closes_a_silently_abandoned_session(api, bank, make_account, monkeypatch):
    """Кандидат ушёл посреди теста: сессия в БД ещё IN_PROGRESS, но таймер её уже закрыл — методист решает."""

    def on_client_event(session, event):
        return [anticheat_hooks.AntiAssistSignal(kind="copy_bypass", message_ru="Копирование мимо обработчика")]

    monkeypatch.setattr(anticheat_hooks, "on_client_event", on_client_event)
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    item = api.get(f"/api/tests/sessions/{s['id']}/next", acc)["item"]
    api.post(f"/api/tests/sessions/{s['id']}/events", acc, {"events": [{"type": "copy_bypass"}]})
    clock.advance(item["time_limit_s"] + 5 + 31 * 60)
    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    flag = next(f for f in api.get("/api/lab/flags", methodist) if f["session_id"] == s["id"])
    out = api.post(f"/api/lab/flags/{flag['id']}/resolve", methodist, {"decision": "confirm"})
    assert out["session_status"] == "ABANDONED" and out["flag"]["status"] == "confirmed"


def test_events_are_not_accepted_after_silent_abandonment(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    item = api.get(f"/api/tests/sessions/{s['id']}/next", acc)["item"]
    clock.advance(item["time_limit_s"] + 5 + 31 * 60)  # тишина > 30 мин после дедлайна задания
    out = api.post(f"/api/tests/sessions/{s['id']}/events", acc, {"events": [{"type": "focus"}]})
    assert out == {"accepted": 0, "ignored": 1, "status": "ABANDONED"}
    assert not bank.scalars(select(SessionEvent).where(SessionEvent.session_id == uuid.UUID(s["id"]))).all()
    assert api.get(f"/api/tests/sessions/{s['id']}", acc)["status"] == "ABANDONED"


def test_three_foreign_keys_in_7_days_quarantine_family(bank, make_account, api):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    fam = bank.scalars(select(ItemFamilyParams.family_id).where(ItemFamilyParams.specialization == SPEC)).first()
    now = clock.now()
    for i in range(3):
        bank.add(
            FlagRow(
                session_id=uuid.UUID(s["id"]),
                kind="foreign_key",
                severity="hard",
                details={"family_id": fam},
                status=FlagStatus.open.value,
                created_at=now - timedelta(days=i),
            )
        )
    bank.flush()
    assert maybe_quarantine_family(bank, fam, now) is True
    statuses = set(bank.scalars(select(ItemFamilyParams.status).where(ItemFamilyParams.family_id == fam)))
    assert statuses == {"quarantine"}
    assert bank.scalars(select(DriftEvent).where(DriftEvent.family_id == fam)).one().action == "quarantine"
    assert maybe_quarantine_family(bank, fam, now) is False  # повтор ничего не меняет
    assert bank.get(SessionRow, uuid.UUID(s["id"])) is not None
