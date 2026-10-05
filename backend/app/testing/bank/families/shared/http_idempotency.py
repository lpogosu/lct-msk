"""http.idempotency.* — повторы запросов после таймаута: идемпотентность методов и Idempotency-Key."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.shared._common import SPECS, explain, text_distractors, unlookalike

_PREFIXES = ("TEA", "CUP", "MUG", "PEN", "BAG", "LED", "CAP", "BOX", "INK", "PAD", "KIT", "FAN")
_BASE = "/api/v1/cart/items"
_PAIR = re.compile(r"([A-Za-z]{3})\s*-?\s*(\d{3})\s*(?:=|:|×|x|\*|\s)\s*(\d+)")

HINTS = {
    "retries_ignored": "повтор неидемпотентного запроса — это второе выполнение: POST без Idempotency-Key "
    "добавил товар дважды.",
    "idempotency_key_ignored": "сервер, поддерживающий Idempotency-Key, на повтор с тем же ключом возвращает "
    "сохранённый ответ и операцию не выполняет.",
    "put_as_add": "PUT и PATCH в формате JSON Merge Patch задают значение, а не прибавляют: повтор "
    "даёт тот же результат.",
}


@dataclass(frozen=True)
class _Op:
    kind: str  # post | post_key | put | patch | delete
    sku: str
    qty: int
    retried: bool
    idem_key: str = ""


def _simulate(initial: dict[str, int], ops: list[_Op], mode: str) -> dict[str, int]:
    """Состояние корзины после журнала. mode: correct | retries_ignored | idempotency_key_ignored | put_as_add."""
    cart = dict(initial)
    seen_keys: set[str] = set()
    for op in ops:
        runs = 2 if op.retried and mode != "retries_ignored" else 1
        for _ in range(runs):
            if op.kind == "post":
                cart[op.sku] = cart.get(op.sku, 0) + op.qty
            elif op.kind == "post_key":
                if mode == "idempotency_key_ignored" or op.idem_key not in seen_keys:
                    cart[op.sku] = cart.get(op.sku, 0) + op.qty
                seen_keys.add(op.idem_key)
            elif op.kind == "put":
                cart[op.sku] = cart.get(op.sku, 0) + op.qty if mode == "put_as_add" else op.qty
            elif op.kind == "patch":
                if op.sku in cart:  # PATCH отсутствующей позиции → 404, корзина не меняется
                    cart[op.sku] = cart[op.sku] + op.qty if mode == "put_as_add" else op.qty
            elif op.kind == "delete":
                cart.pop(op.sku, None)  # повторный DELETE → 404, состояние то же
    return cart


def _fmt(cart: dict[str, int]) -> str:
    return " ".join(f"{s}={q}" for s, q in sorted(cart.items()) if q > 0)


@register
class IdempotencyRetryCart(Family):
    id = "http.idempotency.retry_cart"
    title = "Идемпотентность: корзина после автоповторов запросов"
    specs = (*SPECS, "qa")
    module = "http_api"
    format = "predict_output"
    target_grade = "middle"
    position = -0.5
    c = 0.0
    time_limit_s = 150

    def normalize(self, answer: Any) -> Any:
        if not isinstance(answer, str):
            return answer
        pairs = _PAIR.findall(unlookalike(answer))
        if not pairs:
            return answer.strip()
        cart: dict[str, int] = {}
        for letters, digits, qty in pairs:
            cart[f"{letters.upper()}-{digits}"] = int(qty)
        return _fmt(cart)

    def _make_ops(self, rng: random.Random, skus: list[str], initial: dict[str, int]) -> list[_Op]:
        kinds = ["post+", "post_key+", "put+", rng.choice(("patch+", "delete+"))]
        kinds.append(rng.choice(("post", "put", "patch", "delete", "post_key")))
        rng.shuffle(kinds)
        ops: list[_Op] = []
        for kind in kinds:
            retried = kind.endswith("+")
            kind = kind.rstrip("+")
            state = _simulate(initial, ops, "correct")
            if kind in ("patch", "delete"):
                present = sorted(state)
                if not present:
                    raise ValueError("пустая корзина")
                sku = rng.choice(present)
            else:
                sku = rng.choice(skus)
            if kind in ("put", "patch"):
                qty = rng.choice([q for q in range(1, 7) if q != state.get(sku)])
            else:
                qty = rng.randint(1, 4)
            idem = f"{rng.getrandbits(32):08x}" if kind == "post_key" else ""
            ops.append(_Op(kind, sku, qty, retried, idem))
        return ops

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        while True:
            skus = [f"{p}-{rng.randint(100, 999)}" for p in rng.sample(_PREFIXES, 4)]
            initial = {skus[0]: rng.randint(1, 3), skus[1]: rng.randint(1, 3)}
            try:
                ops = self._make_ops(rng, skus, initial)
            except ValueError:
                continue
            final = _simulate(initial, ops, "correct")
            alt = {m: _simulate(initial, ops, m) for m in ("retries_ignored", "idempotency_key_ignored", "put_as_add")}
            # Экземпляр годен, только если он различает оба главных заблуждения
            if final and all(_fmt(alt[m]) != _fmt(final) for m in ("retries_ignored", "idempotency_key_ignored")):
                break

        key = _fmt(final)
        distractors = text_distractors(key, [(_fmt(alt[m]), m) for m in alt], self.normalize)

        lines = ["Начальная корзина: " + ", ".join(f"{s} × {q}" for s, q in initial.items()), ""]
        for i, op in enumerate(ops, 1):
            if op.kind in ("post", "post_key"):
                lines.append(f"{i}) POST {_BASE}")
                if op.kind == "post_key":
                    lines.append(f"   Idempotency-Key: {op.idem_key}")
                lines.append("   Content-Type: application/json")
                lines.append(f'   {{"sku": "{op.sku}", "qty": {op.qty}}}')
            elif op.kind == "put":
                lines += [f"{i}) PUT {_BASE}/{op.sku}", "   Content-Type: application/json", f'   {{"qty": {op.qty}}}']
            elif op.kind == "patch":
                lines += [
                    f"{i}) PATCH {_BASE}/{op.sku}", "   Content-Type: application/merge-patch+json",
                    f'   {{"qty": {op.qty}}}',
                ]
            else:
                lines.append(f"{i}) DELETE {_BASE}/{op.sku}")
            lines.append("   ⇒ сервер получил 2 раза (повтор после таймаута)" if op.retried else "   ⇒ сервер получил 1 раз")
            lines.append("")
        code = "\n".join(lines).rstrip()

        prompt = (
            "Мобильное приложение работает с корзиной через REST API. Если ответ не пришёл за 3 секунды, "
            "клиент автоматически повторяет тот же запрос. По журналу сервера видно, какие запросы дошли "
            "дважды — сервер получил обе копии и обработал каждую по контракту; запросы шли строго по порядку.\n\n"
            f"Контракт: POST {_BASE} прибавляет qty к позиции (создаёт её, если позиции нет). Остальные "
            "методы работают по своей семантике из RFC 9110 (PUT на отсутствующую позицию создаёт её); "
            "PATCH принимает JSON Merge Patch (RFC 7396). Для POST сервер поддерживает заголовок "
            "Idempotency-Key.\n\n"
            "Какой станет корзина после всех запросов из журнала?"
        )
        steps = []
        for i, op in enumerate(ops, 1):
            after = _fmt(_simulate(initial, ops[:i], "correct")) or "пусто"
            why = {
                "post": "POST не идемпотентен — каждая копия добавляет товар" if op.retried else "добавление",
                "post_key": "второй запрос с тем же Idempotency-Key не выполняется" if op.retried else "добавление",
                "put": "PUT задаёт количество — повтор ничего не меняет" if op.retried else "PUT задаёт количество",
                "patch": "Merge Patch задаёт поле qty — повтор ничего не меняет" if op.retried else "PATCH задаёт qty",
                "delete": "позиция удалена, повторный DELETE вернёт 404" if op.retried else "позиция удалена",
            }[op.kind]
            steps.append(f"{i}) {why} → {after}")
        body = "\n".join(steps) + f"\nИтог: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code,
                language="text",
                answer_mode="text",
                answer_hint="Позиции через пробел в виде АРТИКУЛ=количество, например ABC-123=2 XYZ-777=1. "
                "Порядок не важен, удалённые позиции не указывайте.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
