"""Общие фикстуры и помощники API-тестов тестирования (опрос, тест, лаборатория). Тестов здесь нет.

Пул экземпляров генерируется один раз на процесс (малый: K клонов на семейство) и вставляется в базу
внутри транзакции каждого теста — откат теста убирает и пул, и выдачи. Сценарный «кандидат» отвечает
по заданной способности: верно, если его уровень не ниже сложности семейства (детерминированно).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.models.testing import ItemFamilyParams, ItemInstance, TestResponse
from app.models.users import User
from app.services.testing.keystore import decrypt_key
from app.services.testing.pool import families_for, generate_rows, pool_seeds, seed_family_params

POOL_K = 4  # клонов на семейство в тестах (боевой пул — 200)
_ROWS: dict[str, list[dict[str, Any]]] = {}

# Поля, которых нет и не может быть в ответах кандидату до разбора (ключи, теги заблуждений, хеши)
FORBIDDEN_FIELDS = {
    "key",
    "key_enc",
    "key_hash",
    "distractors",
    "distractor_tag",
    "distractor_tags",
    "correct_answer",
    "correct_answer_text",
    "misconception",
    "explanation",
    "is_correct",
}
PUBLIC_ITEM_FIELDS = {
    "seq",
    "variant_code",
    "prompt",
    "code",
    "language",
    "options",
    "lines",
    "table",
    "answer_mode",
    "answer_hint",
    "answer_format",
    "time_limit_s",
    "deadline_at",
    "seconds_left",
    "clip",  # данные хука anti-assist для обработчика копирования (по умолчанию null)
}


def pool_rows(spec: str) -> list[dict[str, Any]]:
    if spec not in _ROWS:
        # Seed — как в боевом пуле (секретные, pool_seeds): тесты проверяют и то, что их не перебрать
        _ROWS[spec] = [row for f in families_for(spec) for row in generate_rows(f, pool_seeds(f.id, range(POOL_K)))]
    return _ROWS[spec]


def install_bank(db: Session, spec: str = "backend_python") -> None:
    rows = pool_rows(spec)
    db.execute(pg_insert(ItemInstance).values(rows).on_conflict_do_nothing(index_elements=["family_id", "seed"]))
    seed_family_params(db, spec=spec)
    db.flush()


@pytest.fixture
def bank(db_session: Session) -> Session:
    """Банк Backend (Python): экспертные приоры + малый пул экземпляров в транзакции теста."""
    install_bank(db_session, "backend_python")
    return db_session


def restrict_bank(db: Session, spec: str, keep: set[str], status: str = "retired") -> None:
    """Оставить в бою только семейства keep (остальные — status): сценарии с гарантированным пересечением."""
    db.execute(
        update(ItemFamilyParams)
        .where(ItemFamilyParams.specialization == spec, ItemFamilyParams.family_id.not_in(keep))
        .values(status=status)
    )
    db.flush()


def set_family_status(db: Session, spec: str, family_ids: set[str], status: str) -> None:
    db.execute(
        update(ItemFamilyParams)
        .where(ItemFamilyParams.specialization == spec, ItemFamilyParams.family_id.in_(family_ids))
        .values(status=status)
    )
    db.flush()


def make_role(db: Session, user_id: uuid.UUID, role: str) -> None:
    db.get(User, user_id).role = role  # роль методиста саморегистрацией не выдаётся
    db.flush()


# ---------------------------------------------------------------------------
# HTTP-обёртка, собирающая все тела ответов (для проверки утечек)
# ---------------------------------------------------------------------------


@dataclass
class Api:
    client: Any
    bodies: list[tuple[str, Any]] = field(default_factory=list)

    def call(self, method: str, url: str, acc: Any = None, *, json_body: Any = None, expect: int | None = 200) -> Any:
        headers = acc.headers if acc is not None else {}
        r = self.client.request(method, url, headers=headers, json=json_body)
        if expect is not None:
            assert r.status_code == expect, f"{method} {url}: {r.status_code} {r.text}"
        body = r.json() if r.content else None
        self.bodies.append((url, body))
        return body

    def get(self, url: str, acc: Any = None, **kw: Any) -> Any:
        return self.call("GET", url, acc, **kw)

    def post(self, url: str, acc: Any = None, body: Any = None, **kw: Any) -> Any:
        return self.call("POST", url, acc, json_body=body, **kw)

    def put(self, url: str, acc: Any = None, body: Any = None, **kw: Any) -> Any:
        return self.call("PUT", url, acc, json_body=body, **kw)


@pytest.fixture
def api(client) -> Api:
    return Api(client)


def do_survey(api: Api, acc: Any, spec: str = "backend_python", months: int = 30) -> dict[str, Any]:
    api.put("/api/survey/steps/industries", acc, {"industries": ["fintech"]})
    api.put("/api/survey/steps/actions", acc, {"actions": {"server_api": "regularly", "sql_db": "sometimes"}})
    api.put(
        "/api/survey/steps/stack",
        acc,
        {"technologies": {}, "experience_months": months, "roles": ["разработчик"], "self_assessment": {}},
    )
    return api.put("/api/survey/steps/specialization", acc, {"chosen_spec": spec})


def start(
    api: Api,
    acc: Any,
    grade: str | None = "middle",
    *,
    spec: str = "backend_python",
    upgrade: bool = False,
    consent: bool = True,
    expect: int = 201,
) -> dict[str, Any]:
    body: dict[str, Any] = {"specialization": spec, "upgrade": upgrade, "consent_test_logging": consent}
    if grade is not None:
        body["grade"] = grade
    return api.post("/api/tests/sessions", acc, body, expect=expect)


# ---------------------------------------------------------------------------
# Сценарный кандидат
# ---------------------------------------------------------------------------


def instance_for(db: Session, session_id: str, seq: int) -> ItemInstance:
    resp = db.scalars(
        select(TestResponse).where(TestResponse.session_id == uuid.UUID(session_id), TestResponse.seq == seq)
    ).one()
    return db.get(ItemInstance, resp.instance_id)


def wrong_answer(key: Any, item: dict[str, Any]) -> Any:
    mode = item["answer_mode"]
    if mode == "choice":
        return next(o["id"] for o in item["options"] if str(o["id"]) != str(key))
    if mode == "line_choice":
        n = max(1, len(item["lines"]))
        line = int(key["line"]) % n + 1 if n > 1 else int(key["line"])
        fix = key["fix"] if n > 1 else next(o["id"] for o in item["options"] if o["id"] != key["fix"])
        return {"line": line, "fix": fix}
    if mode == "order":
        rev = list(reversed(key))
        return rev if rev != list(key) else list(key[1:]) + list(key[:1])
    return "заведомо-неверный-ответ"


Policy = Callable[[Session, ItemInstance, dict[str, Any]], Any]


def by_ability(theta: float, spec: str = "backend_python") -> Policy:
    """Верно, если уровень кандидата не ниже сложности семейства на шкале spec (без случайности)."""

    def policy(db: Session, inst: ItemInstance, item: dict[str, Any]) -> Any:
        p = db.get(ItemFamilyParams, (inst.family_id, spec))
        key = decrypt_key(inst.key_enc)
        return key if theta >= p.b else wrong_answer(key, item)

    return policy


@dataclass
class Run:
    session_id: str
    items: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    instances: list[ItemInstance] = field(default_factory=list)
    last: dict[str, Any] | None = None


def run_session(api: Api, db: Session, acc: Any, session_id: str, policy: Policy, max_steps: int = 40) -> Run:
    run = Run(session_id)
    for _ in range(max_steps):
        nxt = api.get(f"/api/tests/sessions/{session_id}/next", acc)
        run.last = nxt
        item = nxt["item"]
        if item is None:
            return run
        run.items.append(item)
        inst = instance_for(db, session_id, item["seq"])
        run.instances.append(inst)
        ans = policy(db, inst, item)
        res = api.post(f"/api/tests/sessions/{session_id}/answer", acc, {"seq": item["seq"], "answer": ans})
        run.answers.append(res)
        if res["finished"]:
            run.last = api.get(f"/api/tests/sessions/{session_id}/next", acc)
            return run
    raise AssertionError("сессия не завершилась")


def advance(seconds: float) -> None:
    clock.advance(seconds)


def advance_days(days: float) -> None:
    clock.advance(timedelta(days=days).total_seconds())


# ---------------------------------------------------------------------------
# Проверка утечек
# ---------------------------------------------------------------------------


def _walk(value: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    """(путь, имя поля, значение) по всему JSON."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def _strings(value: Any, skip: set[str]) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in skip:
                yield from _strings(v, skip)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v, skip)


def assert_no_leaks(bodies: list[tuple[str, Any]], instances: list[ItemInstance]) -> None:
    """Ни в одном ответе кандидату (кроме разбора) нет ключей, хешей ключей и тегов заблуждений."""
    secrets: set[str] = set()
    for inst in instances:
        secrets.add(inst.key_hash)
        secrets.update(str(t) for t in (inst.distractors or {}).values())
        key = decrypt_key(inst.key_enc)
        if (inst.rendered or {}).get("answer_mode") == "text" and isinstance(key, str) and len(key) >= 2:
            secrets.add(key)
    # Текст задания (формулировка, код, таблица, строки, варианты) может случайно содержать значение,
    # совпадающее с текстовым ключом другого экземпляра, — сравниваем все остальные строки
    rendered_fields = {"prompt", "code", "table", "lines", "options", "answer_hint"}
    for url, body in bodies:
        if url.endswith("/review") or body is None:
            continue
        for _, name, _ in _walk(body):
            assert name not in FORBIDDEN_FIELDS, f"{url}: поле {name} в ответе кандидату"
        for s in _strings(body, rendered_fields):
            assert s not in secrets, f"{url}: секрет экземпляра в ответе: {s!r}"
        text = json.dumps(body, ensure_ascii=False)
        for inst in instances:
            assert inst.key_hash not in text, f"{url}: key_hash в ответе"
        assert "θ" not in text, f"{url}: «θ» в ответе"


@pytest.fixture
def fixed_seed(monkeypatch):
    """Один seed выбора заданий для всех сессий теста: трасса воспроизводима, сценарий у порога детерминирован."""
    from app.services.testing import sessions

    monkeypatch.setattr(sessions, "seed_of", lambda session_id: 20261011)


def find_scenario(
    api: Api,
    db: Session,
    make_account: Callable[..., Any],
    grade: str,
    thetas: list[float],
    want: Callable[[dict[str, Any], Run], bool],
) -> tuple[Any, dict[str, Any], Run, dict[str, Any]]:
    """Первый уровень из thetas, при котором исход сессии удовлетворяет want (с fixed_seed — детерминированно,
    перебор держит тест устойчивым к пополнению банка)."""
    for theta in thetas:
        acc = make_account()
        do_survey(api, acc)
        s = start(api, acc, grade)
        run = run_session(api, db, acc, s["id"], by_ability(theta))
        res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
        if want(res, run):
            return acc, s, run, res
    raise AssertionError(f"ни один уровень из {thetas} не дал нужного исхода")
