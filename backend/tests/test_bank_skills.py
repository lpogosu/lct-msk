"""Семейства навыков (проверочный набор по вакансии): redis, очереди, FastAPI, PostgreSQL.

Контрольные примеры — ручной разбор по документации; решатели не сверяются сами с собой.
CI-гейты (200 экземпляров, детерминизм, различные ключи) гоняет tests/test_bank_gates.py по всему реестру.
"""

from __future__ import annotations

import pytest

from app.testing.bank.families.skills.fastapi_web import run_app, run_depends
from app.testing.bank.families.skills.postgres import (
    PgNullsOrder,
    PgUpsertReturning,
    run_isolation,
    run_sql_script,
)
from app.testing.bank.families.skills.queues import (
    range_assign,
    run_ack,
    run_dispatch,
    run_group,
)
from app.testing.bank.families.skills.redis import RedisSim, run_lock, run_session, run_window
from app.testing.bank.registry import all_families

SKILL_FAMILIES = (
    "redis.ttl_session",
    "redis.lock_ownership",
    "redis.fixed_window",
    "mq.ack_order",
    "mq.consumer_group",
    "mq.fair_dispatch",
    "fastapi.status_codes",
    "fastapi.depends_log",
    "pg.upsert_returning",
    "pg.nulls_order",
    "pg.isolation",
)


def test_registered_with_check_only_modules():
    fams = all_families()
    from app.matching.skills import get_catalog

    cat = get_catalog()
    for fid in SKILL_FAMILIES:
        f = fams[fid]
        assert f.module in cat.check_only_modules, fid  # ни в одном blueprint: стандартный тест их не выберет
        assert f.observes and all(s in cat.skills for s in f.observes), fid
        assert f.module in {m for s in f.observes for m in cat.modules_of(s)}, fid


# ---------------------------------------------------------------- Redis


def test_redis_ttl_rules():
    code = "\n".join(
        [
            "redis> SET a v EX 60",
            "(+20 с)",
            "? TTL a",
            "redis> SET a w",
            "? TTL a",
            "redis> INCR c",
            "redis> EXPIRE c 30",
            "(+10 с)",
            "redis> INCR c",
            "? TTL c",
            "? EXPIRE nope 5",
            "? TTL nope",
        ]
    )
    assert [v for _, v in run_session(code)] == [40, -1, 20, 0, -2]
    # мутант «SET сохраняет TTL» даёт другой ответ
    assert [v for _, v in run_session(code, "set_keeps_ttl")][1] == 40


def test_redis_expired_key_is_gone_and_boundary_is_rejected():
    sim = RedisSim()
    sim.set("k", "v", ex=10)
    sim.wait(11)
    assert sim.exists("k") == 0 and sim.setnx("k", "w") == 1 and sim.ttl("k") == -1
    sim2 = RedisSim()
    sim2.set("k", "v", ex=10)
    sim2.wait(10)
    with pytest.raises(Exception, match="граница"):
        sim2.ttl("k")


def test_redis_lock_unconditional_del_releases_foreign_lock():
    code = "\n".join(
        [
            "1) t=0 A: SET lock:x A NX EX 10",
            "2) t=4 B: SET lock:x B NX EX 10",  # занято
            "3) t=12 B: SET lock:x B NX EX 10",  # срок A истёк
            "4) t=15 A: DEL lock:x",  # A снимает ЧУЖУЮ блокировку B
            "5) t=16 C: SET lock:x C NX EX 10",  # теперь свободно
        ]
    )
    got = [t for t, _, ok in run_lock(code) if ok]
    assert got == [0, 12, 16]
    safe = [t for t, _, ok in run_lock(code, "del_is_safe") if ok]
    assert safe == [0, 12]


def test_redis_fixed_window_vs_extending_window():
    first = (
        "лимит: 2 запроса в окне 5 с\nна каждый запрос:\n    n = r.incr(\"rl:u1\")\n    if n == 1:\n"
        '        r.expire("rl:u1", 5)\n    if n > 2:\n        вернуть 429\nзапросы (с): 0 1 2 6 7 8'
    )
    assert run_window(first) == [3, 6]  # окно открыто в t=0, закрылось в t=5; новое открыто в t=6
    always = first.replace('    if n == 1:\n        r.expire("rl:u1", 5)', '    r.expire("rl:u1", 5)')
    assert run_window(always) == [3, 4, 5, 6]  # окно продлевается каждым запросом и не обнуляется


# ---------------------------------------------------------------- очереди


def test_ack_order_semantics():
    base = "Брокер: Kafka, одна партиция\nСообщения: офсеты 0–4\nПодтверждение (commit): {ack}\nПадения процесса (каждое случается один раз):\n{crash}\nПосле падения потребитель стартует с последней подтверждённой позиции"
    after = base.format(ack="commit после обработки каждого сообщения", crash="падение после обработки офсета 2, до подтверждения")
    assert run_ack(after) == [0, 1, 2, 2, 3, 4]  # at-least-once: офсет 2 обработан дважды
    before = base.format(ack="commit до обработки каждого сообщения", crash="падение во время обработки офсета 2")
    assert run_ack(before) == [0, 1, 3, 4]  # at-most-once: офсет 2 потерян
    during_after = base.format(ack="commit после обработки каждого сообщения", crash="падение во время обработки офсета 2")
    assert run_ack(during_after) == [0, 1, 2, 3, 4]
    batch = base.format(
        ack="commit после обработки офсета, кратного 2 (подтверждается позиция «офсет + 1»)",
        crash="падение после обработки офсета 3, до подтверждения",
    )
    assert run_ack(batch) == [0, 1, 2, 3, 2, 3, 4]  # граница пакета — после офсета 1: повторяется 2 3


def test_range_assignment_matches_kafka_range_assignor():
    # 7 партиций, 3 потребителя: первым 7 mod 3 = 1 достаётся на партицию больше
    assert range_assign(7, ["c", "a", "b"]) == {"a": [0, 1, 2], "b": [3, 4], "c": [5, 6]}
    assert range_assign(2, ["a", "b", "c"]) == {"a": [0], "b": [1], "c": []}


def test_consumer_group_rebalance():
    code = (
        "Топик t: 4 партиций, номера от 0\nСообщений в партициях: 10 20 30 40\n"
        "События:\n1) в группу входят b, a\n2) входит c\n3) выходит a\nВопрос: нагрузка потребителя b"
    )
    # после события 3: b, c; b получает партиции 0 1 -> 30, c простаивает нет; простаивающих 0
    assert run_group(code) == (30, 0)
    idle = code.replace("4 партиций", "1 партиций").replace("10 20 30 40", "10")
    assert run_group(idle.replace("Вопрос: нагрузка потребителя b", "Вопрос: нагрузка потребителя b")) == (10, 1)


def test_fair_dispatch_prefetch():
    base = "Очередь tasks, сообщения 1…4 лежат в очереди сразу, потребители начинают одновременно\nПотребителей: 2 (подтверждают сообщение после обработки)\nНастройка: {p}\nВремя обработки сообщений (с): 9 1 2 3"
    # по кругу: w1 получает 1 и 3 (9, потом 2 -> 11), w2 получает 2 и 4 (1, потом 3 -> 4)
    assert run_dispatch(base.format(p="prefetch не задан")) == [2, 4, 1, 3, 11]
    # prefetch 1: w1 занят 9 с, w2 берёт 2 (1 с), 3 (3 с), 4 (6 с); w1 -> 1 к 9
    assert run_dispatch(base.format(p="prefetch_count = 1")) == [2, 3, 4, 1, 9]


# ---------------------------------------------------------------- FastAPI (реальное исполнение)


_APP = '''from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

app = FastAPI()


class Order(BaseModel):
    sku: str = Field(min_length=3)
    qty: int = Field(ge=1, le=10)


@app.post("/orders", status_code=201)
def create(order: Order):
    return {"sku": order.sku}


@app.get("/orders/{order_id}")
def read(order_id: int, limit: int = Query(5, le=20), x_token: str = Header()):
    if x_token != "secret":
        raise HTTPException(status_code=401)
    return {"id": order_id}


client = TestClient(app)
calls = [
    ("POST", "/orders", {"sku": "abc", "qty": 2}, None),
    ("POST", "/orders", {"sku": "ab", "qty": 2}, None),
    ("POST", "/orders", {"sku": "abc", "qty": "3"}, None),
    ("POST", "/orders", {"sku": "abc", "qty": 2.5}, None),
    ("GET", "/orders/1", None, None),
    ("GET", "/orders/1", None, {"x-token": "x"}),
    ("GET", "/orders/abc", None, {"x-token": "secret"}),
    ("GET", "/orders/1?limit=99", None, {"x-token": "secret"}),
    ("GET", "/order/1", None, None),
    ("DELETE", "/orders/1", None, None),
]
for method, url, body, headers in calls:
    print(client.request(method, url, json=body, headers=headers).status_code)
'''


def test_fastapi_status_codes_real_execution():
    assert run_app(_APP).split() == ["201", "422", "201", "422", "422", "401", "422", "422", "404", "405"]
    assert run_app(_APP, "validation_400").split()[1] == "400"
    assert run_app(_APP, "strict_qty").split()[2] == "422"
    assert run_app(_APP, "header_optional_401").split()[4] == "401"


_DEPS = '''from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
log = []


def db():
    log.append("db:open")
    yield 3
    log.append("db:close")


def user(conn=Depends(db)):
    log.append("user")
    return 4


@app.get("/x")
def handler(a=Depends(db), b=Depends(user)):
    log.append(f"handler:{a + b}")
    return {}


client = TestClient(app)
client.get("/x")
print(" ".join(log))
'''


def test_fastapi_depends_cache_and_close_order():
    assert run_depends(_DEPS) == "db:open user handler:7 db:close"  # db выполнен один раз (кеш запроса)
    assert run_depends(_DEPS, "no_cache") == "db:open db:open user handler:7 db:close db:close"


# ---------------------------------------------------------------- PostgreSQL


def test_pg_upsert_returning_semantics():
    f = PgUpsertReturning()
    base = (
        "CREATE TABLE stock (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL);\n"
        "INSERT INTO stock VALUES ('k1', 10);\n\n-- выполняется\n"
        "INSERT INTO stock (sku, qty) VALUES ('k1', 5), ('k2', 7)\n"
    )
    add = base + "ON CONFLICT (sku) DO UPDATE SET qty = stock.qty + EXCLUDED.qty\nRETURNING sku, qty;"
    assert run_sql_script(add) == [("k1", 15), ("k2", 7)]
    nothing = base + "ON CONFLICT (sku) DO NOTHING\nRETURNING sku;"
    assert run_sql_script(nothing) == [("k2",)]  # конфликтующая строка в RETURNING не попадает
    assert f.normalize(f.solve_impl(None, add)) == "K1 15 K2 7"


def test_pg_nulls_order_defaults():
    f = PgNullsOrder()
    code = (
        "CREATE TABLE results (name TEXT, score INTEGER);\n"
        "INSERT INTO results VALUES ('a', 5), ('b', NULL), ('c', 9);\n\n-- выполняется\n"
        "SELECT name FROM results\nORDER BY score {d}, name\nLIMIT 3;"
    )
    assert f.solve_impl(None, code.format(d="")) == "a c b"  # ASC: NULL в конце
    assert f.solve_impl(None, code.format(d="DESC")) == "b c a"  # DESC: NULL первым
    assert f.solve_impl(None, code.format(d="DESC"), "sqlite_default") == "c a b"


def test_pg_isolation_models():
    rc = "Таблица account: одна строка, balance = 100.\n1) A: BEGIN ISOLATION LEVEL READ COMMITTED\n2) A: SELECT\n3) B: ADD 30\n4) A: SELECT\n5) A: ADD 20\n6) A: COMMIT"
    assert run_isolation(rc) == ["100", "130", "150"]
    rr = rc.replace("READ COMMITTED", "REPEATABLE READ")
    # снимок 100; ADD после чужой фиксации — ошибка сериализации, транзакция аварийна, итог — только B
    assert run_isolation(rr) == ["100", "100", "ERR", "130"]
    lost = rc.replace("5) A: ADD 20", "5) A: SET 120")
    assert run_isolation(lost) == ["100", "130", "120"]  # запись по устаревшему числу теряет чужое изменение


# ---------------------------------------------------------------- общие свойства


@pytest.mark.parametrize("fid", SKILL_FAMILIES)
def test_distractors_have_tags_and_key_differs(fid):
    f = all_families()[fid]
    for seed in (0, 1):
        inst = f.generate(seed)
        assert inst.distractors and inst.key not in inst.distractors
        assert set(inst.distractors.values()) <= set(f.misconceptions)
        assert f.check(inst, inst.key)[0] is True
        wrong = next(iter(inst.distractors))
        ok, tag = f.check(inst, wrong)
        assert ok is False and tag == inst.distractors[wrong]


@pytest.mark.parametrize("fid", [x for x in SKILL_FAMILIES if not x.startswith("fastapi")])
def test_twin_exists_for_most_instances(fid):
    f = all_families()[fid]
    n = sum(f.try_twin(f.generate(s)).twin is not None for s in range(12))
    assert n >= 10, (fid, n)
