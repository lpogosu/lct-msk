"""http.pagination.* — арифметика пагинации limit/offset в JSON API (spec-testing §3.8)."""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import (
    SPECS,
    TextIntsFamily,
    explain,
    norm_int_seq,
    text_distractors,
)

_ENTITIES = (
    ("/api/v1/products", "товаров каталога"),
    ("/api/v1/orders", "заказов"),
    ("/api/v1/reviews", "отзывов"),
    ("/api/v1/vacancies", "вакансий"),
    ("/api/v1/messages", "сообщений"),
    ("/api/v1/tickets", "обращений в поддержку"),
    ("/api/v1/transactions", "операций по счёту"),
    ("/api/v1/events", "событий журнала"),
)
_LIMITS = (10, 12, 15, 20, 24, 25, 30, 40, 50)
# Нумерация пунктов из формулировки («1) 400 2) 12 3) 39»): номер пункта — не часть ответа.
# Только перед числом — иначе «(400, 12, 3)» потеряло бы последнее значение.
_ENUM = re.compile(r"\b[1-3]\s*\)\s*(?=\d)")

HINTS = {
    "offset_page_times_limit": "offset = страница × limit: первая страница начинается с offset = 0, "
    "поэтому так пропускается целая страница.",
    "offset_one_based": "offset — это сколько записей пропустить, а не номер первой записи; он считается с 0.",
    "pages_floor": "число страниц округлено вниз (total // limit) — потеряна неполная последняя страница; "
    "нужно округлять вверх.",
    "last_page_modulo": "размер последней страницы взят как total % limit; при total, кратном limit, это 0, "
    "а на самом деле последняя страница полная.",
}


@register
class PaginationOffsetMath(TextIntsFamily):
    id = "http.pagination.offset_math"
    title = "Пагинация limit/offset: смещение, число страниц, хвост"
    specs = SPECS
    module = "http_api"
    format = "predict_output"
    target_grade = "intern"
    position = 0.0
    time_limit_s = 90
    is_anchor = True

    def normalize(self, answer: Any) -> Any:
        if isinstance(answer, str):
            answer = _ENUM.sub(" ", answer)
        return norm_int_seq(answer)

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        path, noun = rng.choice(_ENTITIES)
        limit = rng.choice(_LIMITS)
        n_pages = rng.randint(4, 40)
        # ~15% экземпляров с полной последней страницей — проверка заблуждения «total % limit»
        tail = limit if rng.random() < 0.15 else rng.randint(1, limit - 1)
        total = (n_pages - 1) * limit + tail
        page = rng.randint(2, n_pages - 1)

        # точный решатель: только из того, что видит кандидат (total, limit, page)
        offset = (page - 1) * limit
        pages = -(-total // limit)
        last = total - (pages - 1) * limit
        key = f"{offset} {pages} {last}"

        distractors = text_distractors(
            key,
            [
                (f"{page * limit} {pages} {last}", "offset_page_times_limit"),
                (f"{offset + 1} {pages} {last}", "offset_one_based"),
                (f"{offset} {total // limit} {last}", "pages_floor"),
                (f"{offset} {pages} {total % limit}", "last_page_modulo"),
            ],
            norm_int_seq,
        )

        code = (
            f"GET {path}?limit={limit}&offset=0\n"
            "\n"
            "HTTP/1.1 200 OK\n"
            "Content-Type: application/json\n"
            "\n"
            "{\n"
            f'  "total": {total},\n'
            f'  "limit": {limit},\n'
            '  "offset": 0,\n'
            f'  "items": [ … {limit} объектов … ]\n'
            "}"
        )
        prompt = (
            f"Клиент листает список {noun} через API с пагинацией limit/offset. Ниже — первый запрос и "
            f"ответ сервера. Страницы нумеруются с 1, на каждой странице по {limit} записей.\n\n"
            f"Ответьте тремя числами: 1) какой offset передать, чтобы получить страницу {page}; "
            "2) сколько всего страниц; 3) сколько записей вернёт последняя страница."
        )
        body = (
            f"offset = (страница − 1) × limit = ({page} − 1) × {limit} = {offset}. "
            f"Страниц: ⌈{total} / {limit}⌉ = {pages}. "
            f"На последней странице: {total} − ({pages} − 1) × {limit} = {last}. Ответ: {key}."
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code,
                language="text",
                answer_mode="text",
                answer_hint="Три целых числа через пробел в порядке: offset, число страниц, "
                "число записей на последней странице.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
