"""Контрольные суммы ИНН (10/12 цифр) и ОГРН (13/15 цифр) — алгоритмы ФНС. Чистые функции без обращений к сети."""

from __future__ import annotations

_INN10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _digits(value: str | None, lengths: tuple[int, ...]) -> list[int] | None:
    v = (value or "").strip()
    if not v.isascii() or not v.isdigit() or len(v) not in lengths:
        return None
    return [int(c) for c in v]


def _control(digits: list[int], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights, strict=False)) % 11 % 10


def inn_valid(inn: str | None) -> bool:
    """ИНН юрлица (10 цифр): контрольная цифра по весам 2,4,10,3,5,9,4,6,8. ИП/физлицо (12): две контрольные цифры."""
    d = _digits(inn, (10, 12))
    if d is None:
        return False
    if len(d) == 10:
        return _control(d, _INN10) == d[9]
    return _control(d, _INN12_1) == d[10] and _control(d, _INN12_2) == d[11]


def ogrn_valid(ogrn: str | None) -> bool:
    """ОГРН (13): последняя цифра = (первые 12 цифр mod 11) mod 10. ОГРНИП (15): (первые 14 mod 13) mod 10."""
    d = _digits(ogrn, (13, 15))
    if d is None:
        return False
    body = int("".join(map(str, d[:-1])))
    mod = 11 if len(d) == 13 else 13
    return body % mod % 10 == d[-1]
