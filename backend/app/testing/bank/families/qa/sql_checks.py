"""qa_testing, проверки данных SQL-запросами: какие записи нарушают правило (sqlite в памяти).

Ключ и каждый дистрактор — результат исполнения запроса (или запроса-мутанта с типичным заблуждением) на показанной
кандидату мини-таблице. Ответ — значения столбцов результата по строкам. Двойник в буфере строится общим мутатором
anticheat (формат sql_result, язык sql: меняется числовое значение ячейки таблицы), поэтому проверяемые значения —
числа (суммы, часы, остатки, лимиты в отдельной таблице), а текст служит только отбору: ответ каждого вида проверки
зависит от значений ячеек так, что общий мутатор способен его изменить.
"""

from __future__ import annotations

import random
from typing import Any

from app.testing.bank.base import Instance, register
from app.testing.bank.execution import run_sql
from app.testing.bank.families._scenario import norm_ints
from app.testing.bank.families.backend_python.sql import Table, setup_sql, table_payload
from app.testing.bank.families.qa._common import MODULE, SPECS, BankFamily, GenerationRetry

Built = tuple[list[Table], str, dict[str, str | None], str]  # таблицы, запрос, мутанты (тег → запрос), пояснение


def _reconcile(rng: random.Random) -> Built:
    """Сверка итога заказа с суммой позиций: внутреннее соединение не видит заказов без позиций."""
    n = rng.randint(10, 11)
    orders, items = [], []
    iid = 1
    no_items = rng.randrange(n)
    wrong = set(rng.sample([i for i in range(n) if i != no_items], rng.randint(3, 5)))
    for oid in range(1, n + 1):
        total = 0
        if oid - 1 != no_items:
            for _ in range(rng.randint(1, 2)):
                qty, price = rng.randint(1, 4), rng.randrange(50, 600, 10)
                items.append((iid, oid, qty, price))
                iid += 1
                total += qty * price
            if oid - 1 in wrong:
                total += rng.choice([-120, -30, 20, 90, 250])
        else:
            total = rng.randrange(100, 900, 10)
        orders.append((oid, total))
    to = Table("orders", ("id", "total"), ("INTEGER", "INTEGER"), tuple(orders))
    ti = Table("items", ("id", "order_id", "qty", "price"), ("INTEGER",) * 4, tuple(items))
    q = (
        "SELECT o.id, o.total - t.s AS diff FROM orders AS o\nJOIN (\n"
        "  SELECT order_id, SUM(qty * price) AS s FROM items GROUP BY order_id\n"
        ") AS t ON t.order_id = o.id\nWHERE o.total <> t.s\nORDER BY o.id;"
    )
    mutants: dict[str, str | None] = {
        "orders_without_items": q.replace("JOIN (", "LEFT JOIN (").replace("t.s", "COALESCE(t.s, 0)"),
        "only_underpaid": q.replace("o.total <> t.s", "o.total < t.s"),
        "sum_prices_only": q.replace("SUM(qty * price)", "SUM(price)"),
    }
    why = (
        "Внутреннее соединение с суммой позиций оставляет только заказы, у которых позиции есть, и условие "
        "o.total <> t.s выбирает те, где итог не равен сумме qty × price. Заказ без позиций в запрос не попадает."
    )
    return [to, ti], q, mutants, why


def _limits(rng: random.Random) -> Built:
    """Платежи вне лимитов валюты: BETWEEN включает границы, NULL в сумме условие не выбирает."""
    cur = ["RUB", "USD", "EUR"]
    lim = [(c, rng.randrange(100, 400, 50), rng.randrange(5000, 9000, 500)) for c in cur]
    n = rng.randint(12, 14)
    rows = []
    for i in range(1, n + 1):
        c, lo, hi = rng.choice(lim)
        rows.append([i, c, rng.randrange(lo + 50, hi - 50, 50)])
    special = rng.sample(range(n), 7)
    for k, i in enumerate(special):
        _, lo, hi = next(x for x in lim if x[0] == rows[i][1])
        rows[i][2] = (
            lo if k == 0 else hi if k == 1 else lo - rng.randrange(10, lo - 4, 10) if k in (2, 3)
            else hi + rng.randrange(100, 2000, 100) if k in (4, 5) else None
        )
    tl = Table("limits", ("currency", "min_amount", "max_amount"), ("TEXT", "INTEGER", "INTEGER"), tuple(lim))
    tp = Table("payments", ("id", "currency", "amount"), ("INTEGER", "TEXT", "INTEGER"), tuple(tuple(r) for r in rows))
    q = (
        "SELECT p.id, p.amount FROM payments AS p\nJOIN limits AS l ON l.currency = p.currency\n"
        "WHERE p.amount NOT BETWEEN l.min_amount AND l.max_amount\nORDER BY p.id;"
    )
    mutants: dict[str, str | None] = {
        "exclusive_bounds": q.replace(
            "p.amount NOT BETWEEN l.min_amount AND l.max_amount", "(p.amount <= l.min_amount OR p.amount >= l.max_amount)"
        ),
        "null_is_violation": q.replace("WHERE ", "WHERE p.amount IS NULL OR "),
        "only_above": q.replace("p.amount NOT BETWEEN l.min_amount AND l.max_amount", "p.amount > l.max_amount"),
    }
    why = (
        "BETWEEN включает обе границы, поэтому сумма ровно на лимите допустима, а NOT BETWEEN выбирает значения вне "
        "диапазона своей валюты. Пустая сумма даёт NULL в сравнении, и такой платёж запрос не возвращает."
    )
    return [tl, tp], q, mutants, why


def _stock(rng: random.Random) -> Built:
    """Остатки склада: резерв больше остатка или отрицательный остаток; равенство резерва и остатка допустимо."""
    n = rng.randint(12, 14)
    kinds = ["reserve_over"] * rng.randint(3, 4) + ["negative"] * 2 + ["equal"] * 2
    kinds += ["ok"] * (n - len(kinds))
    rng.shuffle(kinds)
    rows = []
    for i, kind in enumerate(kinds, 1):
        on_hand = rng.randrange(10, 200, 5)
        row = {
            "reserve_over": (on_hand, on_hand + rng.randrange(5, 60, 5)),
            "negative": (-rng.randrange(5, 40, 5), rng.randrange(0, 30, 5)),
            "equal": (on_hand, on_hand),
            "ok": (on_hand, rng.randrange(0, on_hand, 5)),
        }[kind]
        rows.append((i, f"SKU-{100 + i}", *row))
    t = Table(
        "stock", ("id", "sku", "on_hand", "reserved"), ("INTEGER", "TEXT", "INTEGER", "INTEGER"), tuple(rows)
    )
    q = "SELECT id, reserved - on_hand AS excess FROM stock\nWHERE reserved > on_hand OR on_hand < 0\nORDER BY id;"
    mutants: dict[str, str | None] = {
        "equal_flagged": q.replace("reserved > on_hand", "reserved >= on_hand"),
        "negative_missed": q.replace(" OR on_hand < 0", ""),
        "and_instead_of_or": q.replace(" OR ", " AND "),
    }
    why = (
        "Нарушение — резерв больше остатка или отрицательный остаток (OR: достаточно любого условия). Если резерв равен "
        "остатку, товар зарезервирован целиком и правило не нарушено."
    )
    return [t], q, mutants, why


def _worklog(rng: random.Random) -> Built:
    """Журнал работ: перерасход часов только у выполненных задач; NULL в оценке — ловушка сравнения."""
    n = rng.randint(12, 14)
    kinds = ["over"] * rng.randint(3, 4) + ["null_est"] * 2 + ["over_open"] * 2
    kinds += ["ok"] * (n - len(kinds))
    rng.shuffle(kinds)
    rows = []
    for i, kind in enumerate(kinds, 1):
        est = rng.randrange(4, 41, 2)
        row = {
            "over": (est, est * 2 + rng.randrange(2, 20, 2), "done"),
            "null_est": (None, rng.randrange(4, 40, 2), "done"),
            "over_open": (est, est * 2 + rng.randrange(2, 20, 2), "open"),
            "ok": (est, rng.randrange(2, est * 2 + 1, 2), rng.choice(["done", "open"])),
        }[kind]
        rows.append((i, *row))
    t = Table("tasks", ("id", "estimate", "spent", "status"), ("INTEGER", "INTEGER", "INTEGER", "TEXT"), tuple(rows))
    q = "SELECT id, spent FROM tasks\nWHERE status = 'done' AND spent > estimate * 2\nORDER BY id;"
    mutants: dict[str, str | None] = {
        "open_included": q.replace("status = 'done' AND ", ""),
        "null_as_zero": q.replace("estimate * 2", "COALESCE(estimate, 0) * 2"),
        "ge_instead_gt": q.replace("spent > estimate * 2", "spent >= estimate * 2"),
    }
    why = (
        "Нарушение — выполненная задача, на которую ушло больше чем вдвое больше часов, чем в оценке. Для задач без "
        "оценки (estimate = NULL) сравнение даёт NULL и условие не выполняется; открытые задачи отсекает status = 'done'."
    )
    return [t], q, mutants, why


def _sla(rng: random.Random) -> Built:
    """Нарушение срока решения по приоритету; незавершённые обращения (resolved = NULL) в сравнение не попадают."""
    n = rng.randint(12, 14)
    fast, slow = rng.choice([(4, 24), (8, 48), (6, 36)])
    rows = []
    for i in range(1, n + 1):
        prio = rng.choice(["high", "high", "normal"])
        opened = rng.randrange(0, 200, 4)
        limit = fast if prio == "high" else slow
        kind = rng.choices(["late", "ok", "edge", "open"], weights=[3, 5, 1, 2])[0]
        resolved = {
            "late": opened + limit + rng.randrange(2, 20, 2),
            "ok": opened + rng.randrange(1, limit, 1),
            "edge": opened + limit,
            "open": None,
        }[kind]
        rows.append((i, prio, opened, resolved))
    t = Table(
        "tickets", ("id", "priority", "opened_h", "resolved_h"), ("INTEGER", "TEXT", "INTEGER", "INTEGER"), tuple(rows)
    )
    q = (
        "SELECT id, resolved_h - opened_h AS spent FROM tickets\n"
        f"WHERE resolved_h - opened_h > CASE priority WHEN 'high' THEN {fast} ELSE {slow} END\nORDER BY id;"
    )
    mutants: dict[str, str | None] = {
        "ge_instead_gt": q.replace("> CASE", ">= CASE"),
        "all_high_limit": q.replace(f"CASE priority WHEN 'high' THEN {fast} ELSE {slow} END", str(fast)),
        "open_as_violation": q.replace("WHERE ", "WHERE resolved_h IS NULL OR "),
    }
    why = (
        "Срок — время от открытия до решения; предел зависит от приоритета (CASE). Равенство пределу — не нарушение "
        "(строго больше). У незавершённых обращений resolved_h = NULL: разность и сравнение дают NULL, и условие не "
        "выполняется."
    )
    return [t], q, mutants, why


_KINDS = [_reconcile, _limits, _stock, _worklog, _sla]


@register
class SqlDataChecks(BankFamily):
    id = "qa.sql.data_checks"
    title = "Проверка данных SQL-запросом: какие записи нарушают правило"
    specs = SPECS
    module = MODULE
    format = "sql_result"
    target_grade = "middle"
    position = 0.1
    time_limit_s = 150
    is_anchor = True
    # одна механика с общими SQL-семействами: NULL в сравнениях и условие соединения (cat.py блокирует в обе стороны)
    enemies = ("sql.subquery.not_in_null", "sql.join.on_vs_where")
    answer_kind = "ints"
    measures = (
        "Проверочные запросы к данным: сверка итога с позициями, границы BETWEEN и NULL, строгие и нестрогие "
        "сравнения, фильтры по статусу, трёхзначная логика сравнений."
    )
    misconceptions = {
        "orders_without_items": "внутреннее соединение не включает заказы без позиций: у них нет суммы для сравнения",
        "only_underpaid": "условие <> ловит расхождения в обе стороны, а не только заниженный итог",
        "sum_prices_only": "сумма заказа — это сумма qty × price по позициям, а не сумма цен",
        "exclusive_bounds": "BETWEEN включает обе границы: сумма ровно на границе допустима",
        "null_is_violation": "NOT BETWEEN для NULL даёт NULL, а не истину: платёж с пустой суммой запрос не вернёт",
        "only_above": "диапазон ограничен с двух сторон: слишком маленькая сумма тоже нарушение",
        "equal_flagged": "резерв, равный остатку, допустим: нарушение — только строго больше",
        "negative_missed": "отрицательный остаток — нарушение независимо от резерва",
        "and_instead_of_or": "условия связаны через OR: достаточно любого из них",
        "open_included": "правило относится только к выполненным задачам: открытые с большим расходом часов "
        "не нарушение",
        "null_as_zero": "NULL — не ноль: без оценки сравнение spent > estimate * 2 даёт NULL, и задача не попадает "
        "в результат",
        "ge_instead_gt": "нарушение — строго больше предела: при равенстве правило не нарушено",
        "all_high_limit": "предел зависит от приоритета (CASE): для обычных обращений он больше",
        "open_as_violation": "незавершённое обращение (NULL) условием не выбирается: сравнение с NULL не истинно",
    }
    answer_hint = "Введите значения столбцов результата по строкам, через пробел (порядок строк — как в запросе)."

    def normalize(self, answer: Any) -> Any:
        return norm_ints(answer)

    def build(self, rng: random.Random, seed: int) -> Instance:
        tables, query, mutants, why = rng.choice(_KINDS)(rng)
        setup = setup_sql(*tables)

        def run(q: str | None) -> str | None:
            if q is None:
                return None
            try:
                rows = run_sql(setup, q)
            except Exception:  # noqa: BLE001 — мутант-запрос может быть невалиден
                return None
            return " ".join(str(v) for r in rows for v in r) or None

        key = run(query)
        if not key or not 6 <= len(key.split()) <= 14:
            raise GenerationRetry
        wrongs = [(run(q), tag) for tag, q in mutants.items()]
        wrongs = [(a, t) for a, t in wrongs if a is not None and a != key]
        if len({a for a, _ in wrongs}) < 2:
            raise GenerationRetry
        return self.text_instance(
            seed,
            prompt="Правило целостности данных проверяют запросом. Что вернёт запрос на этих данных?",
            code=query,
            language="sql",
            table=table_payload(*tables),
            raw_key=key,
            wrongs=wrongs,
            why=why,
            answer_hint=self.answer_hint,
        )
