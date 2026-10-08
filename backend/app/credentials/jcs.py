"""JSON Canonicalization Scheme (RFC 8785): детерминированная сериализация для подписи eddsa-jcs-2022.

Правила: ключи объектов сортируются по кодовым единицам UTF-16, без пробелов, строки — как в JSON без
экранирования не-ASCII, числа — по алгоритму ECMAScript Number::toString. NaN/Infinity запрещены.
Проверено на контрольных примерах RFC 8785 (tests/test_credentials_jcs.py).
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from typing import Any


def _number(x: float) -> str:
    if math.isnan(x) or math.isinf(x):
        raise ValueError("NaN и Infinity в JCS запрещены")
    if x == 0:
        return "0"  # и 0.0, и -0.0
    sign = "-" if x < 0 else ""
    d = Decimal(repr(abs(x)))  # repr — кратчайшее представление, совпадающее с ECMAScript
    digits = "".join(str(t) for t in d.as_tuple().digits).rstrip("0") or "0"
    k = len(digits)
    n = d.adjusted() + 1  # положение десятичной точки относительно первой цифры
    if k <= n <= 21:
        s = digits + "0" * (n - k)
    elif 0 < n <= 21:
        s = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digits
    else:
        e = n - 1
        exp = f"e{'+' if e >= 0 else '-'}{abs(e)}"
        s = digits[0] + ("." + digits[1:] if k > 1 else "") + exp
    return sign + s


def _utf16_key(s: str) -> list[int]:
    b = s.encode("utf-16-be")
    return [int.from_bytes(b[i : i + 2], "big") for i in range(0, len(b), 2)]


def _ser(v: Any, out: list[str]) -> None:
    if v is None:
        out.append("null")
    elif v is True:
        out.append("true")
    elif v is False:
        out.append("false")
    elif isinstance(v, str):
        out.append(json.dumps(v, ensure_ascii=False))
    elif isinstance(v, int):
        out.append(str(v))
    elif isinstance(v, float):
        out.append(_number(v))
    elif isinstance(v, list | tuple):
        out.append("[")
        for i, item in enumerate(v):
            if i:
                out.append(",")
            _ser(item, out)
        out.append("]")
    elif isinstance(v, dict):
        out.append("{")
        for i, k in enumerate(sorted(v, key=_utf16_key)):
            if not isinstance(k, str):
                raise TypeError("ключи JSON-объекта должны быть строками")
            if i:
                out.append(",")
            out.append(json.dumps(k, ensure_ascii=False))
            out.append(":")
            _ser(v[k], out)
        out.append("}")
    else:
        raise TypeError(f"тип {type(v).__name__} не сериализуется в JSON")


def canonicalize(value: Any) -> bytes:
    out: list[str] = []
    _ser(value, out)
    return "".join(out).encode("utf-8")
