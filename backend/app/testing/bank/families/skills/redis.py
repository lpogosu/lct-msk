"""redis_core: семантика Redis на уровне навыка (проверочный набор по вакансии, навык «Redis»).

Ключ считает эмулятор `RedisSim` по ПОКАЗАННОМУ сеансу. Эмулятор реализует только документированные правила
(redis.io → команды SET, EXPIRE, TTL, INCR, PERSIST, SETNX, DEL), без недокументированных деталей:
- SET без EX/KEEPTTL сбрасывает срок жизни ключа; INCR срок не трогает;
- EXPIRE на отсутствующем ключе возвращает 0; TTL: −2 — ключа нет, −1 — ключ без срока;
- ключ с истёкшим сроком для команд уже не существует (удаляется лениво, но это неотличимо снаружи);
- SET … NX и SETNX пишут только в отсутствующий ключ; DEL по ключу, который уже занял другой клиент, удаляет чужой ключ.
Моменты подбираются так, чтобы ни одно действие не приходилось на секунду истечения срока (граница ничьей не нужна).
Реального Redis в банке нет (и не нужен): правила проверены контрольными примерами в tests/test_bank_skills.py.
"""

from __future__ import annotations

import random
import re

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import numeric_alternatives
from app.testing.bank.families.skills._common import (
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    SkillFamily,
)

MODULE = "redis_core"

_TIME_LINE = re.compile(r"^\(\+(\d+) с\)$")


class RedisSim:
    """Одна база Redis с виртуальными часами. mutant включает типичное заблуждение (для дистракторов)."""

    def __init__(self, mutant: str | None = None) -> None:
        self.now = 0
        self.val: dict[str, str] = {}
        self.exp: dict[str, int] = {}
        self.first_ttl: dict[str, int] = {}
        self.m = mutant

    def _alive(self, k: str) -> bool:
        if k in self.exp and self.exp[k] == self.now:
            raise ScenarioError("действие в секунду истечения срока: граница не определена")
        if k in self.exp and self.exp[k] < self.now and self.m != "ignore_ttl":
            self._drop(k)
        return k in self.val

    def _drop(self, k: str) -> None:
        self.val.pop(k, None)
        self.exp.pop(k, None)
        self.first_ttl.pop(k, None)

    def wait(self, sec: int) -> None:
        self.now += sec

    def set(self, k: str, v: str, ex: int | None = None, nx: bool = False) -> str | None:
        existed = self._alive(k)
        if nx and (existed and self.m != "nx_ignored"):
            return None
        keep = self.m == "set_keeps_ttl" and k in self.exp
        old_exp, old_first = self.exp.get(k), self.first_ttl.get(k)
        self.val[k] = v
        self.exp.pop(k, None)
        self.first_ttl.pop(k, None)
        if ex is not None:
            self.exp[k] = self.now + ex
            self.first_ttl[k] = ex
        elif keep and old_exp is not None:
            self.exp[k] = old_exp
            if old_first is not None:
                self.first_ttl[k] = old_first
        return "OK"

    def setnx(self, k: str, v: str) -> int:
        return 1 if self.set(k, v, nx=True) == "OK" else 0

    def incr(self, k: str) -> int:
        if self._alive(k):
            n = int(self.val[k]) + 1
            self.val[k] = str(n)
            if self.m == "incr_clears_ttl":
                self.exp.pop(k, None)
                self.first_ttl.pop(k, None)
            return n
        self.val[k] = "1"
        return 1

    def expire(self, k: str, sec: int) -> int:
        if not self._alive(k):
            return 1 if self.m == "expire_missing_ok" else 0
        if sec <= 0:
            self._drop(k)
            return 1
        self.exp[k] = self.now + sec
        self.first_ttl[k] = sec
        return 1

    def ttl(self, k: str) -> int:
        if not self._alive(k):
            return -2
        if k not in self.exp:
            return -1
        if self.m == "ttl_static":
            return self.first_ttl[k]
        return self.exp[k] - self.now

    def persist(self, k: str) -> int:
        if self._alive(k) and k in self.exp:
            self.exp.pop(k)
            self.first_ttl.pop(k, None)
            return 1
        return 0

    def delete(self, k: str) -> int:
        if self._alive(k):
            self._drop(k)
            return 1
        return 0

    def exists(self, k: str) -> int:
        if self.m == "lazy_visible":  # «пока никто не прочитал, ключ жив»: заблуждение о ленивом удалении
            return 1 if k in self.val else 0
        return 1 if self._alive(k) else 0

    def run(self, line: str) -> str | int | None:
        parts = line.split()
        cmd, args = parts[0].upper(), parts[1:]
        if cmd == "SET":
            ex, nx, i = None, False, 2
            while i < len(args):
                if args[i].upper() == "EX":
                    ex = int(args[i + 1])
                    i += 2
                elif args[i].upper() == "NX":
                    nx = True
                    i += 1
                else:
                    raise ScenarioError(f"неизвестный флаг SET: {args[i]}")
            return self.set(args[0], args[1], ex, nx)
        if cmd == "SETNX":
            return self.setnx(args[0], args[1])
        if cmd == "INCR":
            return self.incr(args[0])
        if cmd == "EXPIRE":
            return self.expire(args[0], int(args[1]))
        if cmd == "TTL":
            return self.ttl(args[0])
        if cmd == "PERSIST":
            return self.persist(args[0])
        if cmd == "DEL":
            return self.delete(args[0])
        if cmd == "EXISTS":
            return self.exists(args[0])
        raise ScenarioError(f"неизвестная команда: {cmd}")


def run_session(code: str, mutant: str | None = None) -> list[tuple[str, str | int | None]]:
    """Ответы помеченных «?» команд сеанса: [(команда, ответ)]."""
    sim = RedisSim(mutant)
    out: list[tuple[str, str | int | None]] = []
    for raw in code.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _TIME_LINE.match(line)
        if m:
            sim.wait(int(m.group(1)))
        elif line.startswith("redis> "):
            sim.run(line[7:])
        elif line.startswith("? "):
            out.append((line[2:], sim.run(line[2:])))
        else:
            raise ScenarioError(f"не команда: {line}")
    if not out:
        raise ScenarioError("нет помеченных команд")
    return out


_KEYS = ("session", "cart", "otp", "token", "hits", "cache", "job", "user")


def _key(rng: random.Random, used: set[str]) -> str:
    while True:
        k = f"{rng.choice(_KEYS)}:{rng.randint(10, 99)}"
        if k not in used:
            used.add(k)
            return k


def _block(rng: random.Random, kind: str, used: set[str]) -> list[str]:
    k = _key(rng, used)
    n = rng.choice((30, 45, 60, 90, 120, 300, 600))
    a = rng.choice([x for x in (10, 15, 20, 25, 40, 50) if x < n - 5])
    if kind == "set_resets":
        return [
            f"redis> SET {k} v1 EX {n}",
            f"(+{a} с)",
            f"? TTL {k}",
            f"redis> SET {k} v2",
            "? TTL " + k,
        ]
    if kind == "incr_keeps":
        return [
            f"redis> INCR {k}",
            f"redis> EXPIRE {k} {n}",
            f"(+{a} с)",
            f"redis> INCR {k}",
            f"? TTL {k}",
            f"? INCR {k}",
        ]
    if kind == "expire_missing":
        return [f"? EXPIRE {k} {n}", f"? TTL {k}", f"redis> SET {k} v1", f"? TTL {k}"]
    if kind == "expiry":
        d = rng.choice((5, 7, 12, 20))
        return [
            f"redis> SET {k} v1 EX {a}",
            f"(+{a + d} с)",
            f"? EXISTS {k}",
            f"? SETNX {k} v2",
            f"? TTL {k}",
        ]
    # persist
    return [
        f"redis> SET {k} v1 EX {n}",
        f"redis> PERSIST {k}",
        f"(+{a} с)",
        f"? TTL {k}",
        f"redis> EXPIRE {k} {n}",
        f"? TTL {k}",
    ]


_KINDS = ("set_resets", "incr_keeps", "expire_missing", "expiry", "persist")


@register
class RedisTtlSession(SkillFamily):
    id = "redis.ttl_session"
    title = "Redis: ответы TTL, EXPIRE и INCR по сеансу redis-cli"
    specs = SPECS
    module = MODULE
    observes = ("redis",)
    format = "predict_output"
    target_grade = "junior"
    position = 0.3
    time_limit_s = 150
    answer_kind = "ints"
    language = "text"
    measures = (
        "Срок жизни ключа в Redis: SET без EX сбрасывает TTL, INCR его не трогает, EXPIRE на отсутствующем ключе "
        "возвращает 0, истёкший ключ не существует, PERSIST снимает срок."
    )
    misconceptions = {
        "set_keeps_ttl": "SET без EX перезаписывает ключ и сбрасывает срок жизни: TTL становится −1",
        "incr_clears_ttl": "INCR меняет только значение: срок жизни ключа остаётся прежним",
        "expire_missing_ok": "EXPIRE на отсутствующем ключе ничего не создаёт и возвращает 0",
        "ttl_static": "TTL показывает сколько секунд осталось сейчас, а не срок, заданный при установке",
        "lazy_visible": "ключ с истёкшим сроком для команд уже не существует, даже если Redis ещё не успел его удалить из памяти",
    }
    answer_hint = "Ответы помеченных команд через пробел, по порядку (целые числа, в том числе −1 и −2)."
    twin_patterns = (r"\bEX (\d+)", r"\bEXPIRE \S+ (\d+)", r"\(\+(\d+) с\)")
    twin_range = (5, 700)

    def make(self, rng: random.Random) -> Scenario:
        used: set[str] = set()
        kinds = rng.sample(_KINDS, 3)
        lines: list[str] = []
        for kd in kinds:
            lines += _block(rng, kd, used)
        code = "\n".join(lines)
        try:
            self.solve(Scenario(code), code)
        except ScenarioError as e:
            raise GenerationRetry from e
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Сеанс redis-cli на пустой базе. Между командами проходит время (строки «(+N с)»). Что вернут команды, "
            "помеченные «?», по порядку? Напомним: TTL возвращает −2, если ключа нет, и −1, если у ключа нет срока жизни."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        return " ".join(str(v) for _, v in run_session(code, mutant))

    def why(self, sc: Scenario, raw_key: str) -> str:
        steps = "; ".join(f"{cmd} → {val}" for cmd, val in run_session(sc.code))
        return (
            "Разберём сеанс по часам. " + steps + ". Правила: SET без EX сбрасывает срок, INCR и повторный EXPIRE "
            "работают только со своим ключом, TTL показывает остаток, а истёкший ключ для команд уже не существует."
        )


# ============================================================== блокировка SET NX EX


_EV = re.compile(r"^(\d+)\)\s+t=(\d+)\s+(\w+):\s+(.+)$")


def run_lock(code: str, mutant: str | None = None) -> list[tuple[int, str, bool | None]]:
    """[(момент t, клиент, получил блокировку?)]; для DEL/release — None."""
    sim = RedisSim("ignore_ttl" if mutant == "no_expiry" else ("nx_ignored" if mutant == "nx_ignored" else None))
    out: list[tuple[int, str, bool | None]] = []  # (момент t, клиент, получил блокировку?)
    last_t = -1
    for raw in code.splitlines():
        m = _EV.match(raw.strip())
        if not m:
            continue
        t, who, cmd = int(m.group(2)), m.group(3), m.group(4).strip()
        if t <= last_t:
            raise ScenarioError("время не растёт")
        last_t = t
        sim.now = t
        parts = cmd.split()
        if parts[0] == "SET":
            ex = int(parts[parts.index("EX") + 1])
            res = sim.set(parts[1], parts[2], ex, nx="NX" in parts)
            out.append((t, who, res == "OK"))
        elif parts[0] == "DEL":
            safe = mutant == "del_is_safe" and sim._alive(parts[1]) and sim.val[parts[1]] != who
            if not safe:
                sim.delete(parts[1])
            out.append((t, who, None))
        elif parts[0] == "RELEASE":
            # release_if_owner: Lua compare-and-delete — удаляет только если в ключе значение этого клиента
            if mutant == "release_unconditional" or (sim._alive(parts[1]) and sim.val[parts[1]] == who):
                sim.delete(parts[1])
            out.append((t, who, None))
        else:
            raise ScenarioError(f"неизвестная команда: {cmd}")
    if not out:
        raise ScenarioError("нет событий")
    return out


@register
class RedisLockOwnership(SkillFamily):
    id = "redis.lock_ownership"
    title = "Redis: блокировка через SET NX EX — кто её получит"
    specs = SPECS
    module = MODULE
    observes = ("redis",)
    format = "predict_output"
    target_grade = "senior"
    position = -0.3
    time_limit_s = 180
    answer_kind = "ints"
    language = "text"
    measures = (
        "Распределённая блокировка на SET key value NX EX: NX пишет только в свободный ключ, срок освобождает ключ "
        "сам, безусловный DEL снимает чужую блокировку, release по владельцу (compare-and-delete) — нет."
    )
    misconceptions = {
        "nx_ignored": "NX не даёт перезаписать занятый ключ: пока блокировка жива, SET … NX возвращает nil",
        "no_expiry": "срок EX освобождает блокировку сам: после него ключа нет, и следующий SET … NX проходит",
        "del_is_safe": "безусловный DEL удаляет ключ, даже если им уже владеет другой клиент: проверки владельца нет",
        "release_unconditional": "release по владельцу удаляет ключ, только если в нём значение этого клиента; чужую блокировку он не трогает",
    }
    answer_hint = "Моменты t тех попыток SET … NX, которые вернули OK, через пробел по порядку."
    twin_patterns = (r"\bEX (\d+)", r"\bt=(\d+)")
    twin_range = (3, 90)

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: object) -> list[str]:
        old = code[span[0] : span[1]]
        if code[max(0, span[0] - 2) : span[0]] != "t=":
            return super().twin_values(sc, code, span, rng, p)
        # момент события: строго между соседними событиями, чтобы порядок не нарушился
        starts = [m.start(1) for m in re.finditer(r"\bt=(\d+)", code)]
        times = [int(x) for x in re.findall(r"\bt=(\d+)", code)]
        i = starts.index(span[0])
        lo = times[i - 1] + 1 if i else 2
        hi = times[i + 1] - 1 if i + 1 < len(times) else times[i] * 2
        return numeric_alternatives(old, rng, p, max(lo, 2), max(hi, lo + 1))

    def make(self, rng: random.Random) -> Scenario:
        clients = rng.sample(("A", "B", "C", "D"), 3)
        ttl = rng.choice((8, 10, 12, 15, 20))
        key = rng.choice(("lock:report", "lock:import", "lock:invoice", "lock:cron"))
        t = 0
        lines = []
        for i in range(1, rng.randint(6, 8) + 1):
            t += rng.randint(2, ttl)
            who = rng.choice(clients)
            r = rng.random()
            if r < 0.55:
                cmd = f"SET {key} {who} NX EX {ttl}"
            elif r < 0.8:
                cmd = f"DEL {key}"
            else:
                cmd = f"RELEASE {key}"
            lines.append(f"{i}) t={t} {who}: {cmd}")
        code = "\n".join(lines)
        self._check(code)
        return Scenario(code)

    def _check(self, code: str) -> None:
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        n_ok = len(base.split()) if base else 0
        if not 2 <= n_ok <= 4 or len(alts - {base, None}) < 2:
            raise GenerationRetry
        if "DEL" not in code and "RELEASE" not in code:
            raise GenerationRetry

    def prompt(self, sc: Scenario) -> str:
        return (
            "Несколько клиентов берут одну блокировку в Redis. Строки — события по порядку, t — секунды от начала. "
            "SET … NX EX n — взять блокировку (ответ OK или nil). DEL — безусловно удалить ключ. "
            "RELEASE — снять блокировку только если в ключе значение этого клиента (compare-and-delete). "
            "В какие моменты t попытка SET … NX вернёт OK? Ответьте моментами через пробел."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        return " ".join(str(t) for t, _, got in run_lock(code, mutant) if got)

    def why(self, sc: Scenario, raw_key: str) -> str:
        parts = []
        for t, who, got in run_lock(sc.code):
            if got is None:
                parts.append(f"t={t}: {who} снимает ключ")
            else:
                parts.append(f"t={t}: {who} — {'OK' if got else 'nil'}")
        return (
            "Идём по событиям: " + "; ".join(parts) + ". NX не перезаписывает живой ключ, срок EX освобождает его сам, "
            "а безусловный DEL снимает блокировку, которую мог уже взять другой клиент."
        )


# ============================================================== окно ограничителя INCR + EXPIRE


def parse_window(code: str) -> dict[str, object]:
    limit = re.search(r"n > (\d+)", code)
    win = re.search(r"r\.expire\(\"rl:u1\", (\d+)\)", code)
    times = re.search(r"запросы \(с\): ([\d ]+)$", code, re.M)
    if not (limit and win and times):
        raise ScenarioError("нет лимита, окна или запросов")
    ts = [int(x) for x in times.group(1).split()]
    if ts != sorted(ts):
        raise ScenarioError("моменты запросов не возрастают")
    always = not re.search(r"if n == 1:\s*\n\s+r\.expire", code)
    return {
        "limit": int(limit.group(1)),
        "window": int(win.group(1)),
        "times": [int(x) for x in times.group(1).split()],
        "always": always,
    }


def run_window(code: str, mutant: str | None = None) -> list[int]:
    case = parse_window(code)
    limit, window, times = case["limit"], case["window"], case["times"]
    always = bool(case["always"])
    if mutant == "other_mode":
        always = not always
    rejected: list[int] = []
    if mutant == "sliding":
        for i, t in enumerate(times, start=1):
            seen = [u for u in times[:i] if u > t - window]
            if len(seen) > limit:
                rejected.append(i)
        return rejected
    sim = RedisSim()
    count_rejected = mutant != "rejected_not_counted"
    for i, t in enumerate(times, start=1):
        sim.now = t
        n = sim.incr("rl:u1")
        if always or n == 1:
            sim.expire("rl:u1", window)
        if n > limit:
            rejected.append(i)
            if not count_rejected:
                sim.val["rl:u1"] = str(n - 1)
    if mutant == "never_resets":
        # счётчик не обнуляется: после limit запросов отклоняется всё
        return [i for i in range(1, len(times) + 1) if i > limit]
    return rejected


_WINDOW_TEMPLATE = """лимит: {limit} запроса в окне {window} с
на каждый запрос:
    n = r.incr("rl:u1")
{expire}
    if n > {limit}:
        вернуть 429
запросы (с): {times}"""


@register
class RedisFixedWindow(SkillFamily):
    id = "redis.fixed_window"
    title = "Redis: окно ограничителя на INCR и EXPIRE"
    specs = SPECS
    module = MODULE
    observes = ("redis",)
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    time_limit_s = 180
    answer_kind = "ints"
    language = "text"
    measures = (
        "Счётчик запросов на INCR + EXPIRE: окно открывает первый INCR, EXPIRE при каждом запросе продлевает окно и "
        "не даёт счётчику обнулиться, отклонённые запросы тоже увеличивают счётчик."
    )
    misconceptions = {
        "other_mode": "EXPIRE на каждом запросе продлевает окно, и счётчик не обнуляется, пока запросы идут чаще окна; EXPIRE только при n == 1 открывает окно один раз, — это разные ограничители",
        "sliding": "это не скользящее окно: счётчик считает запросы с момента открытия окна, а не за последние N секунд",
        "never_resets": "ключ с EXPIRE исчезает по истечении срока, и следующий INCR начинает новое окно",
        "rejected_not_counted": "INCR выполняется до проверки лимита, поэтому отклонённые запросы тоже увеличивают счётчик",
    }
    answer_hint = "Моменты (секунды) отклонённых запросов через пробел, по порядку; если отклонённых нет — 0."
    twin_range = (2, 60)

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        # двойник — в моментах запросов: лимит и окно связаны с текстом кода и с формулировкой разбора
        m = re.search(r"запросы \(с\): ([\d ]+)$", code, re.M)
        if not m:
            return []
        return [(m.start(1) + x.start(), m.start(1) + x.end()) for x in re.finditer(r"\d+", m.group(1))]

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: object) -> list[str]:
        old = code[span[0] : span[1]]
        times = [int(x) for x in parse_window(code)["times"]]
        starts = [m.start() for m in re.finditer(r"\d+", code[re.search(r"запросы \(с\): ", code).end() :])]
        base = re.search(r"запросы \(с\): ", code).end()
        i = [base + x for x in starts].index(span[0])
        lo = times[i - 1] if i else 0
        hi = times[i + 1] if i + 1 < len(times) else times[i] + 15
        return numeric_alternatives(old, rng, p, max(lo, 2), max(hi, lo + 2))

    def make(self, rng: random.Random) -> Scenario:
        limit = rng.choice((2, 3, 4))
        window = rng.choice((5, 6, 8, 10, 12))
        always = rng.random() < 0.5
        t, times = rng.randint(0, 9), []
        for _ in range(rng.randint(8, 11)):
            times.append(t)
            t += rng.choice((1, 1, 2, 3, window - 1, window, window + rng.randint(1, 3)))
        expire = (
            f'    r.expire("rl:u1", {window})'
            if always
            else f'    if n == 1:\n        r.expire("rl:u1", {window})'
        )
        code = _WINDOW_TEMPLATE.format(limit=limit, window=window, expire=expire, times=" ".join(map(str, times)))
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        n_rej = len(base.split()) if base else 0
        if base == "0" or not 2 <= n_rej <= 6 or len(alts - {base, None}) < 2:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Ограничитель частоты запросов хранит счётчик в Redis (r — клиент redis-py). Ключ rl:u1 создаётся первым "
            "INCR. Запросы приходят в указанные секунды. В какие секунды запрос получит 429? Ответьте моментами через пробел."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        case = parse_window(code)
        rej = run_window(code, mutant)
        return " ".join(str(case["times"][i - 1]) for i in rej) if rej else "0"

    def why(self, sc: Scenario, raw_key: str) -> str:
        case = parse_window(sc.code)
        mode = (
            "EXPIRE вызывается на каждом запросе, поэтому окно продлевается и счётчик не обнуляется, пока запросы идут чаще окна"
            if case["always"]
            else "EXPIRE вызывается только при n == 1: окно открывает первый запрос, через окно ключ исчезает и счётчик начинается заново"
        )
        return f"{mode}. Отклоняются запросы, на которых INCR вернул значение выше лимита {case['limit']}: секунды {raw_key}."
