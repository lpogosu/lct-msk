"""Библиотека «тестируемых функций» для семейств QA: короткие чистые функции с константами-порогами.

Тест-модуль собирается вокруг функции: аргументы подбираются у порогов, ожидаемое значение берётся ИСПОЛНЕНИЕМ функции
(a затем при необходимости портится), поэтому проходит тест или падает — решает мини-раннер, а не генератор.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.testing.bank.families.backend_python._common import tpl


@dataclass(frozen=True)
class Sut:
    key: str
    template: str
    names: tuple[str, ...]  # варианты имени функции
    consts: Callable[[random.Random], dict[str, Any]]
    args: Callable[[random.Random, dict[str, Any]], tuple[Any, ...]]
    cases: tuple[str, ...]  # слова для имён тестов: test_<функция>_<слово>

    def source(self, rng: random.Random) -> tuple[str, str, dict[str, Any]]:
        consts = self.consts(rng)
        name = rng.choice(self.names)
        return tpl(self.template, fn=name, **consts), name, consts


def _discount_consts(rng: random.Random) -> dict[str, Any]:
    t2 = rng.randint(3, 6)
    t1 = t2 + rng.randint(3, 6)
    p1 = rng.choice([70, 75, 80])
    return {"t1": t1, "t2": t2, "p1": p1, "p2": rng.choice([90, 92, 95])}


def _discount_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    qty = rng.choice([1, 2, c["t2"] - 1, c["t2"], c["t2"] + 1, c["t1"] - 1, c["t1"], c["t1"] + 3])
    return (rng.randrange(100, 2100, 50), max(qty, 1))


def _ship_consts(rng: random.Random) -> dict[str, Any]:
    return {"base": rng.randrange(150, 400, 10), "w1": rng.randint(5, 15), "per": rng.randint(8, 30)}


def _ship_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    return (rng.choice([1, 3, c["w1"], c["w1"] + 1, c["w1"] + 7, c["w1"] + 20]), rng.choice([True, False]))


def _fee_consts(rng: random.Random) -> dict[str, Any]:
    a = rng.randrange(500, 2000, 100)
    return {"a": a, "b": a + rng.randrange(1000, 5000, 100), "f1": rng.randint(5, 30), "r": rng.randint(2, 6),
            "f2": rng.randrange(100, 400, 10)}


def _fee_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    return (rng.choice([c["a"] - 100, c["a"], c["a"] + 100, c["b"] - 100, c["b"], c["b"] + 500]),)


def _pwd_consts(rng: random.Random) -> dict[str, Any]:
    return {"n": rng.randint(6, 10)}


_WORDS = ["kedr", "volna", "Lipa", "Zorka", "mir", "tulip", "Berezka", "orion", "Pion7", "sneg", "luna", "Taiga"]


def _pwd_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    w = rng.choice(_WORDS)
    if rng.random() < 0.5:
        w += str(rng.randint(1, 99))
    if rng.random() < 0.5:
        w += rng.choice(_WORDS)[: rng.randint(1, 4)].lower()
    return (w,)


def _clamp_consts(rng: random.Random) -> dict[str, Any]:
    lo = rng.randint(-20, 10)
    return {"lo": lo, "hi": lo + rng.randint(10, 60)}


def _clamp_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    return (rng.choice([c["lo"] - 5, c["lo"], c["lo"] + 3, c["hi"] - 2, c["hi"], c["hi"] + 9]),)


def _pages_consts(rng: random.Random) -> dict[str, Any]:
    return {"size": rng.randint(5, 25)}


def _pages_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    s = c["size"]
    return (rng.choice([0, 1, s - 1, s, s + 1, 2 * s, 3 * s + 2, 7 * s - 1]),)


def _tax_consts(rng: random.Random) -> dict[str, Any]:
    return {"rate": rng.choice([8, 10, 12, 13, 18, 20]), "free": rng.randrange(100, 1000, 50)}


def _tax_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    return (rng.choice([c["free"] - 10, c["free"], c["free"] + 10, c["free"] * 3, 2000, 5400]),)


def _score_consts(rng: random.Random) -> dict[str, Any]:
    return {"pass_": rng.randrange(50, 71, 5), "bonus": rng.randint(3, 10)}


def _score_args(rng: random.Random, c: dict[str, Any]) -> tuple[Any, ...]:
    return (rng.choice([c["pass_"] - 1, c["pass_"], c["pass_"] + 2, 100, 0, 88]), rng.choice([0, 1, 2, 3]))


_CASES = ("basic", "regular", "extra", "second", "sample", "typical", "main", "alt", "extended")

SUTS: tuple[Sut, ...] = (
    Sut(
        "discount",
        """
        def $fn(price, qty):
            if qty >= $t1:
                return price * $p1 // 100
            if qty >= $t2:
                return price * $p2 // 100
            return price
        """,
        ("apply_discount", "bulk_price", "price_with_discount"),
        _discount_consts, _discount_args,
        _CASES,
    ),
    Sut(
        "shipping",
        """
        def $fn(weight, express):
            cost = $base
            if weight > $w1:
                cost += (weight - $w1) * $per
            if express:
                cost *= 2
            return cost
        """,
        ("shipping_cost", "delivery_price", "parcel_cost"),
        _ship_consts, _ship_args,
        _CASES,
    ),
    Sut(
        "fee",
        """
        def $fn(amount):
            if amount < $a:
                return $f1
            if amount < $b:
                return amount * $r // 100
            return $f2
        """,
        ("transfer_fee", "commission", "payment_fee"),
        _fee_consts, _fee_args,
        _CASES,
    ),
    Sut(
        "password",
        """
        def $fn(pwd):
            score = 0
            if len(pwd) >= $n:
                score += 1
            if any(ch.isdigit() for ch in pwd):
                score += 1
            if pwd != pwd.lower():
                score += 1
            return ["weak", "medium", "strong", "strong"][score]
        """,
        ("password_level", "strength", "pwd_rating"),
        _pwd_consts, _pwd_args,
        _CASES,
    ),
    Sut(
        "clamp",
        """
        def $fn(x):
            return max($lo, min(x, $hi))
        """,
        ("clamp_value", "limit_range", "bound"),
        _clamp_consts, _clamp_args,
        _CASES,
    ),
    Sut(
        "pages",
        """
        def $fn(total):
            return (total + $size - 1) // $size
        """,
        ("page_count", "pages_needed", "total_pages"),
        _pages_consts, _pages_args,
        _CASES,
    ),
    Sut(
        "tax",
        """
        def $fn(amount):
            if amount <= $free:
                return 0
            return (amount - $free) * $rate // 100
        """,
        ("income_tax", "vat_amount", "duty"),
        _tax_consts, _tax_args,
        _CASES,
    ),
    Sut(
        "score",
        """
        def $fn(points, retries):
            total = points - retries * $bonus
            return "pass" if total >= $pass_ else "fail"
        """,
        ("exam_result", "grade_exam", "check_score"),
        _score_consts, _score_args,
        _CASES,
    ),
)

SUT_BY_KEY = {s.key: s for s in SUTS}


def lit(value: Any) -> str:
    """Python-литерал значения для вставки в тест."""
    return repr(value)
