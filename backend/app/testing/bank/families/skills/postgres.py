"""pg_core: особенности PostgreSQL на уровне навыка (проверочный набор по вакансии, навык «PostgreSQL»).

Стандартный тест проверяет SQL вообще (модуль sql). Здесь — то, что отличает PostgreSQL и что резюме не доказывает.
Ключ получаем так, чтобы он совпадал с PostgreSQL:
- upsert (INSERT … ON CONFLICT … DO UPDATE/NOTHING … RETURNING) — синтаксис и семантика sqlite 3.35+ те же, что в
  PostgreSQL 15+ (EXCLUDED, RETURNING отдаёт только вставленные или изменённые строки); дубликатов ключа внутри одного
  INSERT генератор не создаёт (PostgreSQL в этом случае падает с ошибкой, sqlite — нет);
- порядок NULL в ORDER BY: в PostgreSQL по умолчанию ASC → NULLS LAST, DESC → NULLS FIRST (у sqlite наоборот),
  поэтому решатель переписывает ORDER BY, добавляя явные NULLS FIRST/LAST по правилу PostgreSQL;
- изоляция транзакций (READ COMMITTED / REPEATABLE READ) — точная модель документированного поведения PostgreSQL
  (глава «Transaction Isolation»): снимок на первый оператор, повторная проверка строки при UPDATE в READ COMMITTED,
  ошибка сериализации в REPEATABLE READ, транзакция в аварийном состоянии после ошибки.
Реального PostgreSQL в банке нет и для генерации пула не нужен.
"""

from __future__ import annotations

import random
import re
import sqlite3

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import numeric_alternatives
from app.testing.bank.families.skills._common import (
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    SkillFamily,
)

MODULE = "pg_core"
_MARK = "-- выполняется\n"

_WORDS = ("amber", "birch", "cedar", "delta", "ember", "fjord", "grove", "haven", "ivory", "jasper", "kappa", "lunar")


def _statements(text: str) -> list[str]:
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        raise ScenarioError("оператор без точки с запятой")
    return out


def run_sql_script(code: str) -> list[tuple]:
    """Выполнить показанный скрипт: схема и данные, затем операторы после метки. Результат — строки последнего оператора с выдачей."""
    if _MARK not in code:
        raise ScenarioError("нет метки «-- выполняется»")
    head, tail = code.split(_MARK, 1)
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(head)
        rows: list[tuple] = []
        for st in _statements(tail):
            cur = con.execute(st)
            if cur.description:
                rows = [tuple(r) for r in cur.fetchall()]
        return rows
    finally:
        con.close()


def _flat(rows: list[tuple]) -> str:
    return " ".join("NULL" if v is None else str(v) for r in rows for v in r)


def _value_spans(code: str) -> list[tuple[int, int]]:
    """Числовые значения в строках VALUES (…, N): их меняет двойник."""
    head = code.split(_MARK, 1)[0]
    return [m.span(1) for m in re.finditer(r", (\d+)\)", head)] + [
        (len(head) + len(_MARK) + m.start(1), len(head) + len(_MARK) + m.end(1))
        for m in re.finditer(r", (\d+)\)", code.split(_MARK, 1)[1] if _MARK in code else "")
    ]


# ============================================================== upsert и RETURNING

_UPSERT_KINDS = ("add", "nothing", "greater")


def _upsert_sql(kind: str) -> str:
    if kind == "add":
        return "ON CONFLICT (sku) DO UPDATE SET qty = stock.qty + EXCLUDED.qty\nRETURNING sku, qty;"
    if kind == "nothing":
        return "ON CONFLICT (sku) DO NOTHING\nRETURNING sku, qty;"
    return "ON CONFLICT (sku) DO UPDATE SET qty = EXCLUDED.qty WHERE EXCLUDED.qty > stock.qty\nRETURNING sku, qty;"


def _upsert_mutate(code: str, mutant: str) -> str | None:
    if mutant == "overwrite" and "stock.qty + EXCLUDED.qty" in code:
        return code.replace("stock.qty + EXCLUDED.qty", "EXCLUDED.qty")
    if mutant == "nothing_returns_all" and "DO NOTHING" in code:
        return code.replace("DO NOTHING", "DO UPDATE SET sku = EXCLUDED.sku")
    if mutant == "where_ignored" and "WHERE EXCLUDED.qty > stock.qty" in code:
        return code.replace(" WHERE EXCLUDED.qty > stock.qty", "")
    if mutant == "add_ignored" and "stock.qty + EXCLUDED.qty" in code:
        return code.replace("stock.qty + EXCLUDED.qty", "stock.qty")
    return None


@register
class PgUpsertReturning(SkillFamily):
    id = "pg.upsert_returning"
    title = "PostgreSQL: INSERT … ON CONFLICT и что вернёт RETURNING"
    specs = SPECS
    module = MODULE
    observes = ("postgresql",)
    format = "predict_output"
    target_grade = "middle"
    position = -0.2
    time_limit_s = 180
    answer_kind = "tokens"
    language = "sql"
    measures = (
        "Upsert в PostgreSQL: EXCLUDED — предлагаемая к вставке строка, DO UPDATE меняет существующую, DO NOTHING её не "
        "трогает, RETURNING отдаёт только вставленные или изменённые строки (при условии WHERE — только прошедшие его)."
    )
    misconceptions = {
        "overwrite": "в DO UPDATE выражение stock.qty + EXCLUDED.qty складывает старое значение с предложенным, а не заменяет его",
        "nothing_returns_all": "при DO NOTHING RETURNING не возвращает строки, которые не были вставлены (конфликтующие пропускаются)",
        "where_ignored": "условие WHERE в DO UPDATE отсекает строки: непрошедшие не обновляются и в RETURNING не попадают",
        "add_ignored": "DO UPDATE применяется именно к конфликтующим строкам, и RETURNING показывает уже новые значения",
    }
    answer_hint = "Строки результата подряд, значения через пробел: код товара и количество (для DO NOTHING — только код)."
    twin_range = (2, 90)

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        return _value_spans(code)

    def make(self, rng: random.Random) -> Scenario:
        kind = rng.choice(_UPSERT_KINDS)
        skus = rng.sample(["k1", "k2", "k3", "k4", "k5", "k6", "k7"], 5)
        have = skus[:3]
        base_rows = ", ".join(f"('{s}', {rng.randint(3, 40)})" for s in have)
        incoming = rng.sample(skus, rng.randint(3, 4))
        new_rows = ", ".join(f"('{s}', {rng.randint(2, 50)})" for s in incoming)
        sql = _upsert_sql(kind)
        if kind == "nothing":
            sql = sql.replace("RETURNING sku, qty;", "RETURNING sku;")
        code = (
            "CREATE TABLE stock (sku TEXT PRIMARY KEY, qty INTEGER NOT NULL);\n"
            f"INSERT INTO stock VALUES {base_rows};\n\n"
            f"{_MARK}INSERT INTO stock (sku, qty) VALUES {new_rows}\n{sql}"
        )
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if not base or len(alts - {base, None}) < 1:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Таблица stock в PostgreSQL уже заполнена. Выполняется показанный INSERT с ON CONFLICT. "
            "Что вернёт RETURNING — какие строки и с какими значениями, в порядке строк VALUES?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        if mutant is not None:
            code = _upsert_mutate(code, mutant)
            if code is None:
                return None
        return _flat(run_sql_script(code))

    def why(self, sc: Scenario, raw_key: str) -> str:
        what = (
            "DO NOTHING: конфликтующие строки пропускаются и в RETURNING не попадают"
            if "DO NOTHING" in sc.code
            else "DO UPDATE применяется к строкам, ключ которых уже есть; RETURNING отдаёт новые значения вставленных и изменённых строк"
        )
        if "WHERE EXCLUDED" in sc.code:
            what += "; строки, не прошедшие WHERE, не обновляются и в результат не попадают"
        elif "stock.qty + EXCLUDED.qty" in sc.code:
            what += "; новое количество — старое плюс предложенное"
        return f"{what}. Результат: {raw_key}."


# ============================================================== порядок NULL в ORDER BY


def pg_order(sql: str, mutant: str | None = None) -> str:
    """Добавить явные NULLS FIRST/LAST по умолчанию PostgreSQL (ASC → LAST, DESC → FIRST) в ORDER BY одной строки."""
    if mutant == "sqlite_default":
        return sql

    def fix(m: re.Match[str]) -> str:
        terms = []
        for term in m.group(1).split(","):
            t = term.strip()
            if re.search(r"\bNULLS\b", t, re.I):
                terms.append(t)
                continue
            desc = re.search(r"\bDESC\b", t, re.I) is not None
            if mutant == "nulls_smallest":
                nulls = "NULLS FIRST" if not desc else "NULLS LAST"  # NULL как наименьшее значение (SQLite, MySQL)
            elif mutant == "nulls_always_last":
                nulls = "NULLS LAST"
            else:
                nulls = "NULLS FIRST" if desc else "NULLS LAST"  # PostgreSQL: NULL считается наибольшим значением
            terms.append(f"{t} {nulls}")
        return "ORDER BY " + ", ".join(terms)

    return re.sub(r"ORDER BY ([^\n;]+?)(?=\s+LIMIT|\s*;|\n)", fix, sql, count=1)


@register
class PgNullsOrder(SkillFamily):
    id = "pg.nulls_order"
    title = "PostgreSQL: где в ORDER BY окажутся NULL"
    specs = SPECS
    module = MODULE
    observes = ("postgresql",)
    format = "sql_result"
    target_grade = "junior"
    position = 0.5
    time_limit_s = 150
    answer_kind = "tokens"
    language = "sql"
    measures = (
        "Порядок NULL в PostgreSQL: NULL считается больше любого значения, поэтому ASC ставит их в конец, DESC — в начало; "
        "NULLS FIRST/LAST меняет это явно; LIMIT берёт строки уже после сортировки."
    )
    misconceptions = {
        "sqlite_default": "в PostgreSQL NULL считается наибольшим значением: при ORDER BY … DESC он идёт первым, при ASC — последним",
        "nulls_always_last": "NULL не всегда в конце: при DESC PostgreSQL ставит его первым (NULL считается наибольшим значением), если не указано NULLS LAST",
    }
    answer_hint = "Имена из результата подряд через пробел, в порядке строк."
    twin_range = (2, 99)
    min_distractors = 1

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        return [m.span(1) for m in re.finditer(r", (\d+)\)", code.split(_MARK, 1)[0])]

    def make(self, rng: random.Random) -> Scenario:
        names = rng.sample(_WORDS, rng.randint(6, 7))
        rows = []
        for n in names:
            rows.append(f"('{n}', NULL)" if rng.random() < 0.3 else f"('{n}', {rng.randint(10, 99)})")
        if sum("NULL" in r for r in rows) not in (1, 2):
            raise GenerationRetry
        direction = rng.choice(("ASC", "DESC", ""))
        limit = rng.randint(3, 4)
        order = f"ORDER BY score {direction}".rstrip() + ", name"
        code = (
            "CREATE TABLE results (name TEXT, score INTEGER);\n"
            f"INSERT INTO results VALUES {', '.join(rows)};\n\n"
            f"{_MARK}SELECT name FROM results\n{order}\nLIMIT {limit};"
        )
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(alts - {base, None}) < 1:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Таблица results в PostgreSQL. Какие имена вернёт запрос и в каком порядке? "
            "Обратите внимание на NULL в столбце score."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        head, tail = code.split(_MARK, 1)
        return _flat(run_sql_script(head + _MARK + pg_order(tail, mutant)))

    def why(self, sc: Scenario, raw_key: str) -> str:
        desc = bool(re.search(r"score DESC", sc.code))
        where = "в начало (DESC)" if desc else "в конец (ASC по умолчанию)"
        return (
            f"В PostgreSQL NULL считается больше любого значения, поэтому при сортировке он идёт {where}; затем "
            f"LIMIT берёт первые строки уже отсортированного результата. Результат: {raw_key}."
        )


# ============================================================== изоляция транзакций

_STEP = re.compile(r"^(\d+)\) ([AB]): (.+)$")


def run_isolation(code: str, mutant: str | None = None) -> list[str]:
    """Токены вывода: значение каждого SELECT и ERR для каждого сбойного оператора. Модель PostgreSQL (см. docstring модуля)."""
    committed = int(re.search(r"balance = (\d+)", code).group(1))  # type: ignore[union-attr]
    out: list[str] = []
    level = "RC"
    in_tx = False
    aborted = False
    snapshot: int | None = None
    snap_version = 0
    version = 0  # число зафиксированных изменений строки
    own: int | None = None  # значение, записанное в транзакции A (непринятое)
    last_read: int | None = None
    for raw in code.splitlines():
        m = _STEP.match(raw.strip())
        if not m:
            continue
        who, cmd = m.group(2), m.group(3).strip()
        if who == "B":
            am = re.fullmatch(r"ADD (\d+)", cmd)
            if not am:
                raise ScenarioError(f"неизвестная команда сеанса B: {cmd}")
            if own is not None:
                raise ScenarioError("сеанс B ждёт блокировку строки")
            committed += int(am.group(1))
            version += 1
            continue
        if cmd.startswith("BEGIN"):
            level = "RR" if "REPEATABLE READ" in cmd else "RC"
            if mutant == "rc_snapshot":
                level = "RR"
            elif mutant == "rr_per_statement":
                level = "RC"
            in_tx, aborted, snapshot, own, last_read = True, False, None, None, None
            continue
        if not in_tx:
            raise ScenarioError("нет транзакции")
        if cmd == "COMMIT":
            if not aborted and own is not None:
                committed = own
                version += 1
            in_tx, own = False, None
            continue
        if aborted:
            out.append("ERR")
            continue
        if level == "RR" and snapshot is None:
            snapshot, snap_version = committed, version
        if cmd == "SELECT":
            val = own if own is not None else (snapshot if level == "RR" else committed)
            last_read = val
            out.append(str(val))
            continue
        am, sm = re.fullmatch(r"ADD (\d+)", cmd), re.fullmatch(r"SET (\d+)", cmd)
        if am or sm:
            if level == "RR" and own is None and version != snap_version and mutant != "rr_no_error":
                out.append("ERR")  # could not serialize access due to concurrent update; дальше транзакция аварийна
                aborted = True
                continue
            if sm:
                own = int(sm.group(1))
                continue
            if mutant == "add_lost_update" and last_read is not None:
                cur = last_read  # заблуждение: прибавка считается от ранее прочитанного, а не от актуальной строки
            elif own is not None:
                cur = own
            else:
                cur = snapshot if (level == "RR" and mutant != "rr_no_error") else committed
            own = cur + int(am.group(1))
            continue
        raise ScenarioError(f"неизвестная команда: {cmd}")
    if in_tx:
        raise ScenarioError("транзакция не завершена")
    out.append(str(committed))  # итог: SELECT вне транзакции
    return out


@register
class PgIsolation(SkillFamily):
    id = "pg.isolation"
    title = "PostgreSQL: уровни изоляции — что увидит и что запишет транзакция"
    specs = SPECS
    module = MODULE
    observes = ("postgresql",)
    format = "predict_output"
    target_grade = "senior"
    position = -0.4
    time_limit_s = 210
    answer_kind = "tokens"
    language = "text"
    measures = (
        "Транзакции PostgreSQL: READ COMMITTED видит зафиксированное на момент каждого оператора, а UPDATE balance = balance + n "
        "применяется к актуальной строке; REPEATABLE READ держит снимок и отвечает ошибкой сериализации на конфликтующий "
        "UPDATE; после ошибки транзакция аварийна до конца; записанное по устаревшему прочитанному значению теряет чужое изменение."
    )
    misconceptions = {
        "rc_snapshot": "в READ COMMITTED каждый оператор видит зафиксированные на его момент данные: второй SELECT уже увидит чужую фиксацию",
        "rr_per_statement": "в REPEATABLE READ снимок берётся один раз, на первый оператор транзакции, и второй SELECT чужих изменений не увидит",
        "rr_no_error": "в REPEATABLE READ UPDATE строки, изменённой после снимка, завершается ошибкой сериализации, а не тихо применяется",
        "add_lost_update": "UPDATE balance = balance + n вычисляется над актуальной строкой на момент записи, поэтому чужая фиксация не теряется",
    }
    answer_hint = "Токены вывода через пробел: значение каждого SELECT, ERR для каждого сбойного оператора (и для всех операторов после ошибки до COMMIT), в конце — итоговый баланс."
    twin_patterns = (r"balance = (\d+)", r"ADD (\d+)", r"SET (\d+)")
    twin_range = (5, 400)

    def make(self, rng: random.Random) -> Scenario:
        level = rng.choice(("READ COMMITTED", "REPEATABLE READ"))
        bal = rng.randint(100, 500)
        steps = ["A: BEGIN ISOLATION LEVEL " + level, "A: SELECT"]
        b_add = rng.choice((20, 30, 50, 70))
        steps.append(f"B: ADD {b_add}")
        # дальше A делает чтение и запись (по актуальной строке или по числу, прочитанному ранее)
        if rng.random() < 0.6:
            steps.append("A: SELECT")
        if rng.random() < 0.5:
            steps.append(f"A: ADD {rng.choice((10, 15, 25, 40))}")
        else:
            steps.append("A: SET {SETVAL}")
        steps.append("A: SELECT")
        steps.append("A: COMMIT")
        # SET по прочитанному ранее: приложение посчитало «баланс + 10» на первом SELECT
        lines = [f"{i}) {s}" for i, s in enumerate(steps, start=1)]
        text = "\n".join(lines)
        if "{SETVAL}" in text:
            text = text.replace("{SETVAL}", str(bal + rng.choice((10, 20, 30))))
        code = f"Таблица account: одна строка, balance = {bal}. Сеанс B — отдельные команды без транзакции (сразу фиксируются).\n" + text
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(alts - {base, None}) < 2:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "PostgreSQL, таблица account с одной строкой. Сеанс A работает в транзакции, сеанс B выполняет команды вне "
            "транзакции (ADD n — UPDATE account SET balance = balance + n; SET v — UPDATE account SET balance = v). "
            "Шаги идут строго по номерам, ожидания блокировок нет. Что выведут SELECT и какой баланс будет в конце?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        return " ".join(run_isolation(code, mutant))

    def why(self, sc: Scenario, raw_key: str) -> str:
        rr = "REPEATABLE READ" in sc.code
        how = (
            "REPEATABLE READ берёт снимок на первый оператор и держит его до конца; UPDATE строки, изменённой после "
            "снимка, даёт ошибку сериализации, дальше транзакция аварийна, а COMMIT её откатывает"
            if rr
            else "READ COMMITTED на каждый оператор видит зафиксированное к этому моменту; UPDATE balance = balance + n "
            "применяется к актуальной строке и чужую фиксацию не теряет"
            + ("; а запись литерала, посчитанного по старому SELECT, затирает чужую фиксацию" if " SET " in sc.code else "")
        )
        return f"{how}. Вывод: {raw_key}."


__all__ = ["PgIsolation", "PgNullsOrder", "PgUpsertReturning", "numeric_alternatives"]
