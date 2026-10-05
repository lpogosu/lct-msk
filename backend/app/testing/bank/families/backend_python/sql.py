"""sql: ORDER BY/LIMIT, JOIN и NULL, агрегаты с NULL, GROUP BY/HAVING, DISTINCT, составной индекс,
условие в ON против WHERE, NOT IN с NULL, оконные функции.

Ключ и каждый дистрактор — результат исполнения запроса (или запроса-мутанта с заблуждением) в sqlite
на показанной кандидату мини-таблице. Использование индекса проверяется планировщиком (EXPLAIN QUERY PLAN).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

from app.testing.bank.base import Instance, register
from app.testing.bank.execution import run_sql
from app.testing.bank.families.backend_python._common import SPECS, BankFamily, GenerationRetry, plural, tpl

NUMBER_HINT = "Введите число."
_PEOPLE_RU = ["Анна", "Борис", "Вера", "Глеб", "Дина", "Егор", "Зоя", "Илья", "Кира", "Лев", "Мила", "Олег",
              "Рита", "Тимур", "Яна", "Павел", "Саша", "Нина", "Фёдор", "Ульяна"]
_DEPTS_RU = ["Продажи", "Склад", "Разработка", "Поддержка", "Маркетинг", "Финансы", "Логистика", "Закупки"]


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[str, ...]
    types: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return str(v)


def setup_sql(*tables: Table) -> str:
    parts = []
    for t in tables:
        cols = ", ".join(f"{c} {ty}" for c, ty in zip(t.columns, t.types, strict=True))
        parts.append(f"CREATE TABLE {t.name} ({cols});")
        if t.rows:
            values = ", ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in t.rows)
            parts.append(f"INSERT INTO {t.name} VALUES {values};")
    return "\n".join(parts)


def table_payload(*tables: Table) -> dict[str, Any]:
    """Контракт Rendered.table: name/columns/rows первой таблицы; при нескольких — полный список в "tables"."""
    items = [{"name": t.name, "columns": list(t.columns), "rows": [list(r) for r in t.rows]} for t in tables]
    payload = dict(items[0])
    if len(items) > 1:
        payload["tables"] = items
    return payload


def num(v: Any) -> str:
    """Число из sqlite → каноническая строка (12000.0 → 12000)."""
    if v is None:
        return "NULL"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def scalar(setup: str, query: str) -> Any:
    rows = run_sql(setup, query)
    return rows[0][0] if rows else None


def count_rows(setup: str, query: str) -> int:
    return len(run_sql(setup, query))


# ============================================================== Стажёр


@register
class OrderLimitOffset(BankFamily):
    id = "sql.order.limit_offset"
    title = "ORDER BY, LIMIT и OFFSET"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "intern"
    position = 0.0
    time_limit_s = 90
    is_anchor = True
    answer_kind = "number"
    measures = "Порядок WHERE → ORDER BY → OFFSET/LIMIT; OFFSET пропускает строки, отсчёт с нуля."
    misconceptions = {
        "offset_one_based": "OFFSET k пропускает k строк: OFFSET 1 — это вторая строка, а не первая",
        "order_reversed": "DESC — по убыванию, ASC — по возрастанию",
        "where_ignored": "WHERE отбирает строки до сортировки: строки других категорий не участвуют",
    }

    _DOMAINS = [
        ("products", "name", "category", {
            "Ноутбуки": ["Лёгкий 13", "Рабочий 14", "Офисный 15", "Игровой 16", "Трансформер 14"],
            "Мониторы": ["IPS 24", "QHD 27", "Изогнутый 32", "Офисный 22"],
            "Наушники": ["Вкладыши", "Студийные", "Спортивные", "Дорожные"]}),
        ("courses", "title", "track", {
            "Backend": ["Python с нуля", "FastAPI", "Django", "PostgreSQL", "Docker"],
            "Frontend": ["React", "TypeScript", "CSS", "Vue"],
            "Data": ["Pandas", "SQL для аналитиков", "ML", "Статистика"]}),
    ]

    def build(self, rng: random.Random, seed: int) -> Instance:
        tname, label, cat_col, cats = rng.choice(self._DOMAINS)
        cat_names = list(cats)
        target = rng.choice(cat_names)
        others = [c for c in cat_names if c != target]
        picks = [(n, target) for n in rng.sample(cats[target], 4)]
        for oc in others:
            picks += [(n, oc) for n in rng.sample(cats[oc], 2)]
        rng.shuffle(picks)
        prices = rng.sample(range(1500, 99000, 10), len(picks))
        rows = tuple((i + 1, n, c, p) for i, ((n, c), p) in enumerate(zip(picks, prices, strict=True)))
        t = Table(tname, ("id", label, cat_col, "price"), ("INTEGER", "TEXT", "TEXT", "INTEGER"), rows)
        setup = setup_sql(t)
        direction = rng.choice(["DESC", "ASC"])
        k = rng.choice([1, 2])

        def q(where: bool = True, dir_: str = direction, off: int = k) -> str:
            w = f"\nWHERE {cat_col} = '{target}'" if where else ""
            o = f" OFFSET {off}" if off else ""
            return f"SELECT price\nFROM {tname}{w}\nORDER BY price {dir_}\nLIMIT 1{o};"

        query = q()
        wrongs = [
            (num(scalar(setup, q(off=k - 1))), "offset_one_based"),
            (num(scalar(setup, q(dir_="ASC" if direction == "DESC" else "DESC"))), "order_reversed"),
            (num(scalar(setup, q(where=False))), "where_ignored"),
        ]
        return self.text_instance(
            seed,
            prompt=f"Дана таблица {tname}. Какое значение вернёт запрос?",
            code=query,
            language="sql",
            table=table_payload(t),
            raw_key=num(scalar(setup, query)),
            wrongs=wrongs,
            why=(
                "Сначала WHERE оставляет строки нужной категории, затем ORDER BY сортирует их, затем OFFSET "
                f"пропускает {plural(k, 'строку', 'строки', 'строк')}, и LIMIT 1 берёт следующую."
            ),
            answer_hint=NUMBER_HINT,
        )


# ============================================================== Junior


_JOIN_DOMAINS = [
    # (левая, алиас, правая, алиас, внешний ключ, столбец значения, пул имён)
    ("customers", "c", "orders", "o", "customer_id", "amount", _PEOPLE_RU),
    ("authors", "a", "posts", "p", "author_id", "likes", _PEOPLE_RU),
    ("departments", "d", "employees", "e", "dept_id", "salary", _DEPTS_RU),
]


@register
class LeftJoinRows(BankFamily):
    id = "sql.join.left_rows"
    title = "Сколько строк даст LEFT JOIN"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "junior"
    position = -0.5
    c = 0.25
    time_limit_s = 120
    is_anchor = True
    answer_kind = "choice"
    enemies = ("sql.join.on_vs_where",)
    measures = "LEFT JOIN один-ко-многим: строка на каждое совпадение плюс строка с NULL для левых строк без пары."
    misconceptions = {
        "left_as_inner": "LEFT JOIN сохраняет и левые строки без пары — с NULL в столбцах правой таблицы",
        "one_row_per_left": "при нескольких совпадениях левая строка повторяется для каждого из них",
        "right_table_rows": "число строк результата не равно числу строк правой таблицы: правые строки без пары "
        "не попадают, а левые без пары добавляются с NULL",
        "full_join": "правые строки без пары (здесь с NULL в ключе) LEFT JOIN не возвращает — это делает FULL JOIN",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        left, la, right, ra, fk, val, pool = rng.choice(_JOIN_DOMAINS)
        n_left = rng.randint(4, 6)
        names = rng.sample(pool, n_left)
        counts = [rng.choice([0, 0, 1, 1, 2, 3]) for _ in range(n_left)]
        if 0 not in counts or max(counts) < 2:
            raise GenerationRetry
        right_rows: list[tuple[Any, ...]] = []
        for lid, cnt in enumerate(counts, start=1):
            right_rows += [(lid,)] * cnt
        right_rows += [(None,)] * rng.randint(1, 2)
        rng.shuffle(right_rows)
        rrows = tuple((i + 1, r[0], rng.randrange(100, 5000, 10)) for i, r in enumerate(right_rows))
        tl = Table(left, ("id", "name"), ("INTEGER", "TEXT"), tuple((i + 1, n) for i, n in enumerate(names)))
        tr = Table(right, ("id", fk, val), ("INTEGER", "INTEGER", "INTEGER"), rrows)
        setup = setup_sql(tl, tr)
        query = (
            f"SELECT {la}.name, {ra}.{val}\nFROM {left} AS {la}\n"
            f"LEFT JOIN {right} AS {ra} ON {ra}.{fk} = {la}.id;"
        )
        key = count_rows(setup, query)
        inner = count_rows(setup, query.replace("LEFT JOIN", "JOIN"))
        unmatched_right = scalar(
            setup, f"SELECT COUNT(*) FROM {right} WHERE {fk} IS NULL OR {fk} NOT IN (SELECT id FROM {left})")
        wrongs = [
            (str(inner), "left_as_inner"),
            (str(len(tl.rows)), "one_row_per_left"),
            (str(len(tr.rows)), "right_table_rows"),
            (str(key + unmatched_right), "full_join"),
        ]
        rng.shuffle(wrongs)
        return self.choice_instance(
            seed,
            rng,
            prompt=f"Даны таблицы {left} и {right}. Сколько строк вернёт запрос?",
            correct=str(key),
            wrongs=wrongs,
            code=query,
            language="sql",
            table=table_payload(tl, tr),
            why=(
                "LEFT JOIN даёт по строке на каждую найденную пару; левая строка без пары попадает один раз "
                "с NULL справа. Правые строки без пары (NULL в ключе) не попадают. "
                f"Итого {key}."
            ),
        )


@register
class AggregateNull(BankFamily):
    id = "sql.aggregate.null"
    title = "COUNT и AVG при NULL"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "junior"
    position = 0.0
    time_limit_s = 120
    answer_kind = "numbers"
    enemies = ("sql.distinct.null",)
    measures = "COUNT(*) против COUNT(столбец), AVG игнорирует NULL, фильтр WHERE до агрегации."
    misconceptions = {
        "count_col_counts_null": "COUNT(столбец) не считает строки, где в столбце NULL",
        "avg_null_as_zero": "AVG пропускает NULL, а не считает их нулями: делим на число непустых значений",
        "count_star_skips_null": "COUNT(*) считает все строки, независимо от NULL в столбцах",
        "where_ignored": "WHERE применяется до агрегатов — считаются только строки выбранного отдела",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        depts = rng.sample(_DEPTS_RU, 3)
        target = depts[0]
        n = rng.randint(4, 6)
        k = rng.randint(1, 2)
        m = n - k
        lcm = n * m // math.gcd(n, m)
        values = [rng.randrange(2000, 30001, 10) for _ in range(m - 1)]
        # Последнее значение подбираем так, чтобы обе средние (верная и «NULL как 0») были целыми.
        cands = [v for v in range(2000, 30001, 10) if (sum(values) + v) % lcm == 0]
        if not cands:
            raise GenerationRetry
        values.append(rng.choice(cands))
        tgt = values + [None] * k
        rng.shuffle(tgt)
        other: list[tuple[str, int | None]] = []
        for d in depts[1:]:
            other += [(d, rng.choice([None, rng.randrange(2000, 30001, 10)])) for _ in range(2)]
        rows_raw = [(target, b) for b in tgt] + other
        rng.shuffle(rows_raw)
        names = rng.sample(_PEOPLE_RU, len(rows_raw))
        rows = tuple((i + 1, names[i], d, b) for i, (d, b) in enumerate(rows_raw))
        t = Table("employees", ("id", "name", "dept", "bonus"), ("INTEGER", "TEXT", "TEXT", "INTEGER"), rows)
        setup = setup_sql(t)

        def q(sel: str, where: bool = True) -> str:
            w = f"\nWHERE dept = '{target}'" if where else ""
            return f"SELECT {sel}\nFROM employees{w};"

        def run(query: str) -> str:
            return " ".join(num(v) for v in run_sql(setup, query)[0])

        base_sel = "COUNT(*), COUNT(bonus), AVG(bonus)"
        query = q(base_sel)
        wrongs = [
            (run(q("COUNT(*), COUNT(*), AVG(bonus)")), "count_col_counts_null"),
            (run(q("COUNT(*), COUNT(bonus), AVG(COALESCE(bonus, 0))")), "avg_null_as_zero"),
            (run(q("COUNT(bonus), COUNT(bonus), AVG(bonus)")), "count_star_skips_null"),
            (run(q(base_sel, where=False)), "where_ignored"),
        ]
        return self.text_instance(
            seed,
            prompt="Дана таблица employees (bonus может быть NULL). Что вернёт запрос?",
            code=query,
            language="sql",
            table=table_payload(t),
            raw_key=run(query),
            wrongs=wrongs,
            why=(
                "COUNT(*) считает строки, COUNT(bonus) — только строки с непустым bonus, AVG(bonus) делит сумму "
                "непустых значений на их количество. Всё это — по строкам, прошедшим WHERE."
            ),
            answer_hint="Введите три числа через пробел — в порядке столбцов результата.",
        )


@register
class GroupByHaving(BankFamily):
    id = "sql.group.having"
    title = "GROUP BY, HAVING и порядок фильтрации"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "junior"
    position = 0.5
    time_limit_s = 150
    answer_kind = "number"
    measures = "Порядок выполнения WHERE → GROUP BY → HAVING → ORDER BY → LIMIT; HAVING фильтрует группы."
    misconceptions = {
        "where_ignored": "WHERE отбрасывает строки до группировки: отменённые заказы не входят ни в сумму, ни в COUNT",
        "having_counts_all_rows": "HAVING COUNT(*) считает строки группы уже после WHERE, а не все строки клиента",
        "having_ignored": "HAVING отбрасывает группы, не прошедшие условие, ещё до сортировки и LIMIT",
        "order_asc": "ORDER BY total DESC ставит первой самую большую сумму",
    }

    _DOMAINS = [("orders", "customer", "status", "paid", "cancelled", "amount"),
                ("payments", "merchant", "state", "ok", "failed", "amount"),
                ("invoices", "client", "status", "paid", "void", "total_sum")]

    def build(self, rng: random.Random, seed: int) -> Instance:
        tname, who, st, good, bad, amt = rng.choice(self._DOMAINS)
        people = rng.sample(_PEOPLE_RU, 4)
        rows_raw: list[tuple[str, str, int]] = []
        # Один клиент с единственной, но крупной оплатой (проверяет HAVING), у одного — крупная отмена (WHERE).
        rows_raw.append((people[0], good, rng.randint(6000, 9000)))
        for p in people[1:]:
            for _ in range(rng.randint(2, 3)):
                rows_raw.append((p, good, rng.randint(500, 4000)))
        rows_raw.append((people[1], bad, rng.randint(5000, 9000)))
        rows_raw.append((people[0], bad, rng.randint(500, 3000)))
        rng.shuffle(rows_raw)
        rows = tuple((i + 1, w, s, a) for i, (w, s, a) in enumerate(rows_raw))
        t = Table(tname, ("id", who, st, amt), ("INTEGER", "TEXT", "TEXT", "INTEGER"), rows)
        setup = setup_sql(t)

        def q(where: bool = True, having: str | None = "COUNT(*) >= 2", direction: str = "DESC") -> str:
            w = f"\nWHERE {st} = '{good}'" if where else ""
            h = f"\nHAVING {having}" if having else ""
            return (f"SELECT {who}, SUM({amt}) AS total\nFROM {tname} AS t{w}\nGROUP BY {who}{h}\n"
                    f"ORDER BY total {direction}\nLIMIT 1;")

        query = q()
        totals = run_sql(setup, query.replace("\nLIMIT 1;", ";"))
        if len(totals) < 2 or totals[0][1] == totals[1][1]:
            raise GenerationRetry  # первая строка должна быть однозначной
        all_rows = f"(SELECT COUNT(*) FROM {tname} AS t2 WHERE t2.{who} = t.{who}) >= 2"

        def total_of(query_: str) -> str | None:
            rows_ = run_sql(setup, query_)
            return num(rows_[0][1]) if rows_ else None

        wrongs = [
            (total_of(q(where=False)), "where_ignored"),
            (total_of(q(having=all_rows)), "having_counts_all_rows"),
            (total_of(q(having=None)), "having_ignored"),
            (total_of(q(direction="ASC")), "order_asc"),
        ]
        return self.text_instance(
            seed,
            prompt=f"Дана таблица {tname}. Какое значение total вернёт запрос?",
            code=query,
            language="sql",
            table=table_payload(t),
            raw_key=num(totals[0][1]),
            wrongs=wrongs,
            why=(
                f"Сначала WHERE оставляет строки со статусом '{good}', затем строки группируются, HAVING оставляет "
                "группы минимум с двумя такими строками, ORDER BY total DESC ставит наибольшую сумму первой, LIMIT 1 "
                "берёт её."
            ),
            answer_hint=NUMBER_HINT,
        )


# ============================================================== Middle


@register
class DistinctNull(BankFamily):
    id = "sql.distinct.null"
    title = "DISTINCT и NULL"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "middle"
    position = -0.5
    c = 0.25
    time_limit_s = 120
    answer_kind = "choice"
    enemies = ("sql.aggregate.null",)
    measures = "COUNT(DISTINCT x) не считает NULL; SELECT DISTINCT оставляет одну строку с NULL."
    misconceptions = {
        "null_counted_in_distinct": "COUNT(DISTINCT столбец) пропускает NULL, как и любой COUNT по столбцу",
        "null_dropped_by_distinct": "SELECT DISTINCT не выбрасывает NULL: все NULL схлопываются в одну строку",
        "each_null_distinct": "для DISTINCT все NULL считаются одинаковыми и схлопываются в одно значение",
        "distinct_ignored": "DISTINCT убирает повторы: одинаковые значения считаются один раз",
        "count_star": "здесь считаются различные значения, а не все строки таблицы",
    }

    _DOMAINS = [("clients", "city", ["Москва", "Казань", "Самара", "Пермь", "Омск", "Тверь"]),
                ("tickets", "assignee", _PEOPLE_RU),
                ("products", "brand", ["Кедрон", "Вельта", "Сайрус", "Ломира", "Тарвис", "Эльбра"]),  # вымышленные
                ("employees", "office", ["Центр", "Север", "Юг", "Восток", "Запад"])]

    def build(self, rng: random.Random, seed: int) -> Instance:
        tname, col, pool = rng.choice(self._DOMAINS)
        d = rng.randint(3, 4)
        vals = rng.sample(pool, d)
        n_null = 2
        cells: list[str | None] = list(vals) + [rng.choice(vals) for _ in range(rng.randint(3, 4))] + [None] * n_null
        rng.shuffle(cells)
        rows = tuple((i + 1, v) for i, v in enumerate(cells))
        t = Table(tname, ("id", col), ("INTEGER", "TEXT"), rows)
        setup = setup_sql(t)
        q_count_distinct = f"SELECT COUNT(DISTINCT {col})\nFROM {tname};"
        q_distinct_rows = f"SELECT COUNT(*)\nFROM (SELECT DISTINCT {col} FROM {tname}) AS t;"
        count_distinct = scalar(setup, q_count_distinct)
        distinct_rows = scalar(setup, q_distinct_rows)
        count_col = scalar(setup, f"SELECT COUNT({col}) FROM {tname}")
        count_star = scalar(setup, f"SELECT COUNT(*) FROM {tname}")
        each_null = count_distinct + n_null
        if rng.random() < 0.5:
            query, key = q_count_distinct, count_distinct
            wrongs = [(str(distinct_rows), "null_counted_in_distinct"), (str(each_null), "each_null_distinct"),
                      (str(count_col), "distinct_ignored"), (str(count_star), "count_star")]
        else:
            query, key = q_distinct_rows, distinct_rows
            wrongs = [(str(count_distinct), "null_dropped_by_distinct"), (str(each_null), "each_null_distinct"),
                      (str(count_col), "distinct_ignored"), (str(count_star), "count_star")]
        rng.shuffle(wrongs)
        return self.choice_instance(
            seed,
            rng,
            prompt=f"Дана таблица {tname}. Какое число вернёт запрос?",
            correct=str(key),
            wrongs=wrongs,
            code=query,
            language="sql",
            table=table_payload(t),
            why=(
                "COUNT(DISTINCT x) считает различные НЕпустые значения — NULL не учитывается. SELECT DISTINCT "
                "возвращает строки, и все NULL схлопываются в одну строку."
            ),
        )


@register
class CompositeIndex(BankFamily):
    id = "sql.index.composite"
    title = "Когда работает составной индекс"
    specs = SPECS
    module = "sql"
    format = "mcq_concept"
    target_grade = "middle"
    position = 0.0
    c = 0.25
    time_limit_s = 120
    answer_kind = "choice"
    measures = "Правило левого префикса составного B-tree-индекса; что мешает поиску по индексу."
    misconceptions = {
        "leading_column_skipped": "без условия на первый столбец индекса поиск по нему невозможен — остаётся полный "
        "просмотр",
        "function_on_column": "функция над столбцом прячет его от индекса: индекс хранит исходные значения",
        "or_condition": "OR со столбцом, который не является началом индекса, требует просмотра всей таблицы",
        "leading_wildcard": "LIKE с % в начале шаблона не задаёт префикс, по которому можно искать в индексе",
    }

    _DOMAINS = [
        ("orders", "customer_code", "status", "created_at", ["C-1042", "C-2077", "C-3310", "C-4518"],
         ["new", "paid", "shipped"], "upper"),
        ("users", "email", "status", "created_at", ["anna@mail.example", "oleg@corp.example", "vera@corp.example"],
         ["active", "blocked", "pending"], "lower"),
        ("tickets", "project_key", "priority", "opened_at", ["CORE", "BILL", "SHOP", "AUTH"],
         ["low", "high", "urgent"], "upper"),
        ("payments", "account_no", "currency", "paid_at", ["40817001", "40817052", "40702190"],
         ["RUB", "USD", "CNY"], "trim"),
    ]

    def build(self, rng: random.Random, seed: int) -> Instance:
        tname, c1, c2, c3, v1s, v2s, func = rng.choice(self._DOMAINS)
        idx = f"idx_{tname}_{c1}"
        v1, v1b = rng.sample(v1s, 2)
        v2 = rng.choice(v2s)
        date = f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}"
        suffix = v1[-2:]
        code = tpl(
            """
            CREATE TABLE $t (
              id INTEGER PRIMARY KEY,
              $c1 TEXT,
              $c2 TEXT,
              $c3 TEXT,
              amount INTEGER
            );
            CREATE INDEX $idx ON $t ($c1, $c2, $c3);
            """,
            t=tname, c1=c1, c2=c2, c3=c3, idx=idx,
        )
        sel = f"SELECT * FROM {tname} WHERE "
        good = rng.choice([
            f"{c2} = '{v2}' AND {c1} = '{v1}'",
            f"{c1} = '{v1}' AND {c3} >= '{date}'",
            f"{c1} IN ('{v1}', '{v1b}') AND {c2} = '{v2}'",
        ])
        bad_pool = [
            (rng.choice([f"{c2} = '{v2}' AND {c3} >= '{date}'", f"{c3} >= '{date}'"]), "leading_column_skipped"),
            (f"{func}({c1}) = '{v1}'", "function_on_column"),
            (f"{c1} = '{v1}' OR {c2} = '{v2}'", "or_condition"),
            (f"{c1} LIKE '%{suffix}'", "leading_wildcard"),
        ]
        bad = rng.sample(bad_pool, 3)
        # Ключ — по плану запроса sqlite: ровно один вариант должен искать по индексу (SEARCH … USING INDEX).
        uses_index = {}
        for cond in [good] + [b for b, _ in bad]:
            plan = " ".join(r[-1] for r in run_sql(code, "EXPLAIN QUERY PLAN " + sel + cond))
            uses_index[cond] = plan.startswith("SEARCH") and idx in plan
        if not uses_index[good] or any(uses_index[b] for b, _ in bad):
            raise AssertionError(f"{self.id}: план sqlite не совпал с ожидаемым для {tname}")
        return self.choice_instance(
            seed,
            rng,
            # СУБД названа явно: PostgreSQL 18 и MySQL 8 умеют skip scan (пропуск первого столбца индекса), и без
            # уточнения «вариант без условия на первый столбец» стал бы спорным. SQLite без ANALYZE его не применяет.
            prompt=(
                f"Для таблицы {tname} (SQLite) создан составной индекс {idx}. В каком запросе планировщик найдёт "
                "строки поиском по этому индексу, а не просмотром всей таблицы?"
            ),
            correct=sel + good + ";",
            wrongs=[(sel + b + ";", tag) for b, tag in bad],
            code=code,
            language="sql",
            why=(
                f"B-tree-индекс по ({c1}, {c2}, {c3}) упорядочен сначала по {c1}: искать по нему можно, когда есть "
                f"условие равенства (или IN) на {c1}, дальше — на следующие столбцы по порядку (правило левого "
                "префикса). Порядок условий в WHERE значения не имеет. Проверено планировщиком SQLite (EXPLAIN QUERY "
                "PLAN). Оговорка: PostgreSQL 18 и MySQL 8 умеют skip scan — обходят первый столбец, если у него мало "
                "различных значений; это частная оптимизация, проектировать индексы по-прежнему нужно по левому префиксу."
            ),
        )


@register
class OnVsWhere(BankFamily):
    id = "sql.join.on_vs_where"
    title = "LEFT JOIN: условие в ON или в WHERE"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "middle"
    position = 0.4
    c = 0.25
    time_limit_s = 150
    is_anchor = True
    answer_kind = "choice"
    enemies = ("sql.join.left_rows",)
    measures = "Условие на правую таблицу в WHERE превращает LEFT JOIN во внутреннее соединение; в ON — нет."
    misconceptions = {
        "where_keeps_left_rows": "WHERE проверяется после соединения и отбрасывает строки отделов без подходящих "
        "сотрудников: у сотрудников с меньшей зарплатой условие ложно, а у отдела без сотрудников e.salary = NULL "
        "и сравнение даёт NULL",
        "on_filters_left_rows": "условие в ON LEFT JOIN влияет только на то, какие строки справа присоединятся; "
        "все строки слева остаются",
        "row_per_employee": "после GROUP BY по отделу каждая группа — одна строка, а не строка на сотрудника",
        "condition_ignored": "условие на зарплату тоже влияет на результат",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        n_d = rng.randint(4, 6)
        depts = rng.sample(_DEPTS_RU, n_d)
        threshold = rng.randrange(100_000, 180_001, 10_000)
        emps: list[tuple[int, int]] = []
        for did in range(1, n_d + 1):
            kind = rng.choice(["none", "low", "high", "high", "mixed"])
            if kind == "low":
                emps += [(did, rng.randrange(60_000, threshold, 1000)) for _ in range(rng.randint(1, 2))]
            elif kind == "high":
                emps += [(did, rng.randrange(threshold + 1000, 300_001, 1000)) for _ in range(rng.randint(1, 3))]
            elif kind == "mixed":
                emps += [(did, rng.randrange(60_000, threshold, 1000)),
                         (did, rng.randrange(threshold + 1000, 300_001, 1000))]
        rng.shuffle(emps)
        names = rng.sample(_PEOPLE_RU, min(len(emps), len(_PEOPLE_RU)))
        if len(emps) > len(names) or not emps:
            raise GenerationRetry
        td = Table("departments", ("id", "name"), ("INTEGER", "TEXT"), tuple((i + 1, n) for i, n in enumerate(depts)))
        te = Table("employees", ("id", "name", "dept_id", "salary"), ("INTEGER", "TEXT", "INTEGER", "INTEGER"),
                   tuple((i + 1, names[i], d, s) for i, (d, s) in enumerate(emps)))
        setup = setup_sql(td, te)
        head = "SELECT d.name, COUNT(e.id) AS cnt\nFROM departments AS d\nLEFT JOIN employees AS e ON e.dept_id = d.id"
        q_where = f"{head}\nWHERE e.salary > {threshold}\nGROUP BY d.name;"
        q_on = f"{head} AND e.salary > {threshold}\nGROUP BY d.name;"
        n_where, n_on = count_rows(setup, q_where), count_rows(setup, q_on)
        matched = scalar(setup, f"SELECT COUNT(*) FROM employees WHERE salary > {threshold}")
        with_any = count_rows(setup, f"{head}\nWHERE e.id IS NOT NULL\nGROUP BY d.name;")
        if rng.random() < 0.5:
            query, key = q_where, n_where
            wrongs = [(str(n_on), "where_keeps_left_rows"), (str(matched), "row_per_employee"),
                      (str(with_any), "condition_ignored")]
        else:
            query, key = q_on, n_on
            wrongs = [(str(n_where), "on_filters_left_rows"), (str(matched), "row_per_employee"),
                      (str(with_any), "condition_ignored")]
        return self.choice_instance(
            seed,
            rng,
            prompt="Даны таблицы departments и employees. Сколько строк вернёт запрос?",
            correct=str(key),
            wrongs=wrongs,
            code=query,
            language="sql",
            table=table_payload(td, te),
            why=(
                "Условие в ON решает, какие сотрудники присоединятся; отдел без подходящих всё равно остаётся (cnt = 0). "
                "Условие в WHERE проверяется после соединения и отбрасывает строки, где оно не истинно: сотрудников "
                "с зарплатой не выше порога и строки-заглушки отделов без сотрудников (e.salary = NULL, сравнение "
                "даёт NULL). Отделы без подходящих сотрудников пропадают — LEFT JOIN фактически становится внутренним."
            ),
        )


# ============================================================== Senior


@register
class NotInNull(BankFamily):
    id = "sql.subquery.not_in_null"
    title = "NOT IN, NOT EXISTS и NULL в подзапросе"
    specs = (*SPECS, "qa")
    module = "sql"
    format = "sql_result"
    target_grade = "senior"
    position = -0.5
    c = 0.25
    time_limit_s = 150
    answer_kind = "choice"
    measures = "Трёхзначная логика: NOT IN по подзапросу с NULL не возвращает строк; NOT EXISTS устойчив к NULL."
    misconceptions = {
        "not_in_ignores_null": "x NOT IN (…, NULL) никогда не бывает истинным: сравнение с NULL даёт NULL, "
        "поэтому фильтр не пройдёт ни одна строка и COUNT(*) вернёт 0",
        "null_poisons_everything": "NOT EXISTS проверяет наличие строк и не зависит от NULL; NULL отфильтрован "
        "или не участвует в сравнении",
        "in_vs_not_in": "перепутаны «есть связанные строки» и «нет связанных строк»",
        "subquery_ignored": "подзапрос отбрасывает часть строк, считаются не все строки таблицы",
    }

    _DOMAINS = [("customers", "orders", "customer_id", "amount", "клиентов без заказов (гостевые заказы — с NULL)"),
                ("employees", "tasks", "assignee_id", "estimate", "сотрудников без задач (неназначенные задачи — с NULL)")]

    def build(self, rng: random.Random, seed: int) -> Instance:
        left, right, fk, val, purpose = rng.choice(self._DOMAINS)
        total = rng.randint(4, 6)
        k = rng.randint(1, total - 1)
        if 2 * k == total:
            raise GenerationRetry
        ids = list(range(1, total + 1))
        without = set(rng.sample(ids, k))
        with_ = [i for i in ids if i not in without]
        refs: list[int | None] = list(with_) + [rng.choice(with_) for _ in range(rng.randint(0, 2))]
        refs += [None] * rng.randint(1, 2)
        rng.shuffle(refs)
        tl = Table(left, ("id", "name"), ("INTEGER", "TEXT"),
                   tuple((i, n) for i, n in zip(ids, rng.sample(_PEOPLE_RU, total), strict=True)))
        tr = Table(right, ("id", fk, val), ("INTEGER", "INTEGER", "INTEGER"),
                   tuple((i + 1, r, rng.randrange(100, 5000, 10)) for i, r in enumerate(refs)))
        setup = setup_sql(tl, tr)
        q_not_in = f"SELECT COUNT(*)\nFROM {left}\nWHERE id NOT IN (SELECT {fk} FROM {right});"
        q_not_exists = (f"SELECT COUNT(*)\nFROM {left} AS l\nWHERE NOT EXISTS (\n"
                        f"  SELECT 1 FROM {right} AS r WHERE r.{fk} = l.id\n);")
        q_not_in_nn = f"SELECT COUNT(*)\nFROM {left}\nWHERE id NOT IN (SELECT {fk} FROM {right} WHERE {fk} IS NOT NULL);"
        v_not_in = scalar(setup, q_not_in)
        v_exists = scalar(setup, q_not_exists)
        v_in = scalar(setup, f"SELECT COUNT(*) FROM {left} WHERE id IN (SELECT {fk} FROM {right})")
        v_total = scalar(setup, f"SELECT COUNT(*) FROM {left}")
        variant = rng.randrange(3)
        if variant == 0:
            query, key = q_not_in, v_not_in
            wrongs = [(str(v_exists), "not_in_ignores_null"), (str(v_in), "in_vs_not_in"),
                      (str(v_total), "subquery_ignored")]
        else:
            query = q_not_exists if variant == 1 else q_not_in_nn
            key = scalar(setup, query)
            wrongs = [(str(v_not_in), "null_poisons_everything"), (str(v_in), "in_vs_not_in"),
                      (str(v_total), "subquery_ignored")]
        return self.choice_instance(
            seed,
            rng,
            prompt=f"Запрос должен посчитать {purpose}. Какое число он вернёт на этих данных?",
            correct=str(key),
            wrongs=wrongs,
            code=query,
            language="sql",
            table=table_payload(tl, tr),
            why=(
                "x NOT IN (список) истинно, только если x не равно ни одному элементу. Сравнение с NULL даёт NULL, "
                "поэтому при NULL в подзапросе условие никогда не истинно и запрос вернёт 0. NOT EXISTS и NOT IN по "
                "подзапросу без NULL работают ожидаемо."
            ),
        )


@register
class WindowRank(BankFamily):
    id = "sql.window.dense_rank"
    title = "Оконные функции ранжирования: топ-N в группе"
    specs = SPECS
    module = "sql"
    format = "sql_result"
    target_grade = "senior"
    position = 0.3
    time_limit_s = 180
    is_anchor = True
    answer_kind = "number"
    measures = "ROW_NUMBER / RANK / DENSE_RANK при равных значениях, PARTITION BY, отбор топ-N по группам."
    misconceptions = {
        "as_row_number": "при равных зарплатах ROW_NUMBER всё равно нумерует подряд, а RANK/DENSE_RANK дают "
        "одинаковый номер",
        "as_rank": "RANK после равных пропускает номера (1, 1, 3), DENSE_RANK — нет (1, 1, 2)",
        "as_dense_rank": "DENSE_RANK не пропускает номера после равных значений, в отличие от RANK",
        "partition_ignored": "PARTITION BY dept нумерует отдельно внутри каждого отдела",
        "order_asc": "ORDER BY salary DESC — номер 1 у самой высокой зарплаты",
    }

    _FUNCS = {"DENSE_RANK": "as_dense_rank", "RANK": "as_rank", "ROW_NUMBER": "as_row_number"}

    def build(self, rng: random.Random, seed: int) -> Instance:
        depts = rng.sample(_DEPTS_RU, 3)
        rows_raw: list[tuple[str, int]] = []
        for j, d in enumerate(depts):
            base = sorted(rng.sample(range(800, 3000), 4), reverse=True)
            if j == 0:
                base[1] = base[0]  # равенство на первом месте
            elif j == 1:
                base[2] = base[1]  # равенство на втором месте
            size = 4 if j < 2 else rng.randint(3, 4)
            rows_raw += [(d, s * 100) for s in base[:size]]
        rng.shuffle(rows_raw)
        names = rng.sample(_PEOPLE_RU, len(rows_raw))
        t = Table("employees", ("id", "name", "dept", "salary"), ("INTEGER", "TEXT", "TEXT", "INTEGER"),
                  tuple((i + 1, names[i], d, s) for i, (d, s) in enumerate(rows_raw)))
        setup = setup_sql(t)
        func = rng.choice(list(self._FUNCS))
        top_n = 2

        def q(fn: str = func, partition: bool = True, direction: str = "DESC") -> str:
            part = "PARTITION BY dept " if partition else ""
            return tpl(
                """
                SELECT SUM(salary)
                FROM (
                  SELECT salary,
                         $fn() OVER ($part ORDER BY salary $dir) AS pos
                  FROM employees
                ) AS ranked
                WHERE pos <= $n;
                """,
                fn=fn, part=part.strip(), dir=direction, n=top_n,
            ).replace("( ORDER", "(ORDER")

        query = q()
        key = num(scalar(setup, query))
        by_fn = {fn: num(scalar(setup, q(fn=fn))) for fn in self._FUNCS}
        if len(set(by_fn.values())) < 3:
            raise GenerationRetry  # все три функции должны давать разные ответы
        wrongs = [(by_fn[fn], tag) for fn, tag in self._FUNCS.items() if fn != func]
        wrongs += [(num(scalar(setup, q(partition=False))), "partition_ignored"),
                   (num(scalar(setup, q(direction="ASC"))), "order_asc")]
        return self.text_instance(
            seed,
            prompt=f"Дана таблица employees. Какое число вернёт запрос с {func}?",
            code=query,
            language="sql",
            table=table_payload(t),
            raw_key=key,
            wrongs=wrongs,
            why=(
                "Внутри каждого отдела строки нумеруются по убыванию зарплаты. ROW_NUMBER: 1, 2, 3 даже при "
                "равенстве; RANK: равным — одинаковый номер, следующий с пропуском (1, 1, 3); DENSE_RANK: без "
                f"пропуска (1, 1, 2). WHERE pos <= {top_n} оставляет строки с такими номерами, их зарплаты "
                "суммируются."
            ),
            answer_hint=NUMBER_HINT,
        )
