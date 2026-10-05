"""qa_testing, проверки ответа HTTP по контракту: какие проверки не пройдут.

Показан исполняемый Python: ответ API литералом и список проверок «имя, фактическое, ожидаемое»; цикл печатает строку
на каждое расхождение. Ключ — исполнение этого кода (общий мутатор двойников работает: предмет — python, ответ — текст),
дистракторы — исполнение кода с типичной ошибкой чтения (остановка на первом расхождении, перепутанный порядок значений,
инвертированное условие, сумма без количества).
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.testing.bank.base import Instance, register
from app.testing.bank.families.backend_python._common import try_exec_py
from app.testing.bank.families.qa._common import MODULE, SPECS, BankFamily, GenerationRetry

_LOOP = (
    "for name, actual, expected in checks:\n"
    "    if actual != expected:\n"
    "        print(name, expected, actual)"
)


def _lit(obj: Any, ind: int = 0) -> str:
    """Литерал Python в стиле black: dict — по ключу на строке, список словарей — по элементу на строке."""
    pad = " " * ind
    if isinstance(obj, dict) and len(obj) <= 3 and all(not isinstance(v, dict | list) for v in obj.values()):
        return "{" + ", ".join(f"{json.dumps(k, ensure_ascii=False)}: {_lit(v)}" for k, v in obj.items()) + "}"
    if isinstance(obj, dict):
        inner = ",\n".join(f"{pad}    {json.dumps(k, ensure_ascii=False)}: {_lit(v, ind + 4)}" for k, v in obj.items())
        return "{\n" + inner + ",\n" + pad + "}"
    if isinstance(obj, list):
        if all(not isinstance(x, dict | list) for x in obj):
            return "[" + ", ".join(_lit(x) for x in obj) + "]"
        inner = ",\n".join(f"{pad}    {_lit(x, ind + 4)}" for x in obj)
        return "[\n" + inner + ",\n" + pad + "]"
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    return repr(obj)


@dataclass(frozen=True)
class _Check:
    name: str
    actual: str  # выражение Python
    expected: str
    breaker: Callable[[random.Random, dict[str, Any]], None]  # портит ответ так, чтобы проверка не прошла


def _orders(rng: random.Random) -> tuple[dict[str, Any], list[_Check]]:
    n = rng.randint(2, 4)
    items = [{"sku": f"{rng.choice('ABCDKMT')}-{rng.randint(10, 99)}", "qty": rng.randint(1, 5),
              "price": rng.randrange(50, 950, 10)} for _ in range(n)]
    if len({i["sku"] for i in items}) < n:
        raise GenerationRetry
    oid = rng.randint(1000, 9999)
    body = {
        "id": oid,
        "total": sum(i["qty"] * i["price"] for i in items),
        "units": sum(i["qty"] for i in items),
        "count": n,
        "currency": "RUB",
        "items": items,
    }
    resp = {"status": 201, "headers": {"Content-Type": "application/json", "Location": f"/orders/{oid}"}, "body": body}

    def brk_status(r: random.Random, d: dict[str, Any]) -> None:
        d["status"] = r.choice([200, 202, 204])

    def brk_loc(r: random.Random, d: dict[str, Any]) -> None:
        d["headers"]["Location"] = f"/orders/{oid + r.choice([-3, -1, 1, 7])}"

    def brk_total(r: random.Random, d: dict[str, Any]) -> None:
        d["body"]["total"] += r.choice([-90, -50, -10, 10, 40, 120])

    def brk_count(r: random.Random, d: dict[str, Any]) -> None:
        d["body"]["count"] += r.choice([-1, 1])

    def brk_cur(r: random.Random, d: dict[str, Any]) -> None:
        d["body"]["currency"] = r.choice(["USD", "EUR", "rub"])

    def brk_ct(r: random.Random, d: dict[str, Any]) -> None:
        d["headers"]["Content-Type"] = r.choice(["text/plain", "application/xml", "text/html"])

    def brk_units(r: random.Random, d: dict[str, Any]) -> None:
        d["body"]["units"] += r.choice([-1, 1, 2])

    pool = [
        _Check("status", 'response["status"]', "201", brk_status),
        _Check("location", 'response["headers"]["Location"]', '"/orders/" + str(body["id"])', brk_loc),
        _Check("total", 'body["total"]', 'sum(i["qty"] * i["price"] for i in body["items"])', brk_total),
        _Check("count", 'body["count"]', 'len(body["items"])', brk_count),
        _Check("currency", 'body["currency"]', '"RUB"', brk_cur),
        _Check("content_type", 'response["headers"]["Content-Type"]', '"application/json"', brk_ct),
        _Check("units", 'body["units"]', 'sum(i["qty"] for i in body["items"])', brk_units),
    ]
    return resp, pool


def _listing(rng: random.Random) -> tuple[dict[str, Any], list[_Check]]:
    limit = rng.choice([5, 10, 20, 25])
    total = rng.randint(limit + 3, 300)
    offset = rng.randrange(0, total - 1, limit)
    size = min(limit, total - offset)
    ids = sorted(rng.sample(range(100, 9000), size))
    nxt = offset + limit if offset + limit < total else None
    body = {"total": total, "limit": limit, "offset": offset, "data": ids, "next": nxt}
    resp = {"status": 200, "headers": {"Content-Type": "application/json"}, "body": body}

    def brk_status(r: random.Random, d: dict[str, Any]) -> None:
        d["status"] = r.choice([206, 201, 204])

    def brk_size(r: random.Random, d: dict[str, Any]) -> None:
        if len(d["body"]["data"]) < 2:
            raise GenerationRetry
        d["body"]["data"].pop(r.randrange(len(d["body"]["data"])))

    def brk_next(r: random.Random, d: dict[str, Any]) -> None:
        d["body"]["next"] = (d["body"]["next"] or 0) + r.choice([-limit, 1, limit])

    def brk_sorted(r: random.Random, d: dict[str, Any]) -> None:
        data = d["body"]["data"]
        if len(data) < 3:
            raise GenerationRetry
        i = r.randrange(len(data) - 1)
        data[i], data[i + 1] = data[i + 1], data[i]

    def brk_total(r: random.Random, d: dict[str, Any]) -> None:
        d["body"]["total"] = max(d["body"]["offset"] + 1, d["body"]["total"] + r.choice([-4, -2, 3]))

    pool = [
        _Check("status", 'response["status"]', "200", brk_status),
        _Check("size", 'len(body["data"])', 'min(body["limit"], body["total"] - body["offset"])', brk_size),
        _Check("next", 'body["next"]',
               'body["offset"] + body["limit"] if body["offset"] + body["limit"] < body["total"] else None',
               brk_next),
        _Check("sorted", 'body["data"]', 'sorted(body["data"])', brk_sorted),
        _Check("total", 'body["total"]', f"{total}", brk_total),
    ]
    return resp, pool


@register
class ApiContractChecks(BankFamily):
    id = "qa.api.contract_checks"
    title = "Проверки ответа API: какие не пройдут"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "middle"
    position = -0.4
    time_limit_s = 150
    answer_kind = "output"
    measures = (
        "Сверка ответа API с контрактом: производные поля (сумма, счётчик, Location, пагинация) считаются из тела "
        "ответа, расхождение — это пара «ожидали / получили»."
    )
    misconceptions = {
        "stop_at_first": "отчёт печатает ВСЕ расхождения: цикл не прерывается после первого",
        "swapped_values": "в строке отчёта сначала ожидаемое значение, потом фактическое — порядок аргументов print",
        "inverted_condition": "печатаются проверки, которые НЕ прошли (actual != expected), а не прошедшие",
        "ignore_quantity": "итог заказа — сумма qty × price по всем позициям, а не сумма цен",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        domain = rng.choice(["orders", "listing"])
        resp, pool = (_orders if domain == "orders" else _listing)(rng)
        checks = rng.sample(pool, rng.randint(5, len(pool)) if domain == "orders" else rng.randint(4, len(pool)))
        broken = rng.sample(checks, rng.randint(2, 3))
        for c in broken:
            c.breaker(rng, resp)
        lines = [
            f"response = {_lit(resp)}",
            "body = response[\"body\"]",
            "checks = [",
            *(f'    ("{c.name}", {c.actual}, {c.expected}),' for c in checks),
            "]",
            _LOOP,
        ]
        code = "\n".join(lines)
        key = try_exec_py(code)
        if not key:
            raise GenerationRetry
        wrongs = [
            (try_exec_py(code.replace("        print(name, expected, actual)", "        print(name, expected, actual)\n        break")),
             "stop_at_first"),
            (try_exec_py(code.replace("print(name, expected, actual)", "print(name, actual, expected)")), "swapped_values"),
            (try_exec_py(code.replace("if actual != expected:", "if actual == expected:")), "inverted_condition"),
        ]
        if 'i["qty"] * i["price"]' in code:
            wrongs.append((try_exec_py(code.replace('i["qty"] * i["price"]', 'i["price"]')), "ignore_quantity"))
        return self.text_instance(
            seed,
            prompt=(
                "Ответ API проверяют скриптом по контракту. Что напечатает скрипт? Для каждой непройденной проверки "
                "выводится строка: имя проверки, ожидаемое и фактическое значение."
            ),
            code=code,
            language="python",
            raw_key=key,
            wrongs=wrongs,
            why=(
                "Скрипт считает ожидаемые значения из самого ответа (сумма позиций, число элементов, адрес по id, "
                "размер страницы) и печатает строку «имя ожидаемое фактическое» для каждой проверки, где значения "
                "различаются; цикл проходит по всем проверкам, а не останавливается на первой."
            ),
            answer_hint=(
                "Введите вывод скрипта: по строке на непройденную проверку (имя, ожидаемое, фактическое); "
                "строки можно разделять пробелом или переводом строки."
            ),
        )

