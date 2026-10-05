"""http.status.* — семантика кодов ответа HTTP на сценарии запросов к REST API (RFC 9110)."""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.shared._common import SPECS, explain, text_distractors

_RESOURCES = (
    ("notes", "Заметки"),
    ("tasks", "Задачи"),
    ("documents", "Документы"),
    ("projects", "Проекты"),
    ("reports", "Отчёты"),
    ("boards", "Доски"),
)
_USERS = ("anna", "boris", "vera", "gleb", "dina", "egor", "lena", "oleg")
_ADMINS = ("root", "admin", "ops")
_TITLES = (
    "План релиза", "Ретро спринта", "Бюджет Q3", "Онбординг", "Список закупок", "Миграция БД",
    "Дизайн-ревью", "Отчёт по SLA", "Нагрузочный тест", "Чек-лист релиза", "Карта рисков", "Roadmap",
)

HINTS = {
    "auth_swap": "401 Unauthorized — «не знаем, кто вы» (нет или неверны учётные данные); "
    "403 Forbidden — «знаем, кто вы, но нельзя».",
    "success_200": "успешные ответы различаются: создание ресурса — 201 Created, успех без тела — 204 No Content.",
    "conditional_confusion": "условные запросы: If-None-Match совпал с текущим ETag → 304 Not Modified; "
    "If-Match не совпал → 412 Precondition Failed (а не 200 и не 409).",
    "method_404": "ресурс существует, но метод для него не поддерживается → 405 Method Not Allowed, не 404.",
    "all_400": "не всякая ошибка клиента — 400: конфликт с состоянием ресурса — 409, неподдерживаемый "
    "Content-Type — 415.",
}

_CODE_RE = re.compile(r"\b[1-5]\d\d\b")


def _etag(rng: random.Random) -> str:
    return f'"{rng.getrandbits(16):04x}"'


@register
class StatusCodeScenario(Family):
    id = "http.status.scenario"
    title = "Коды ответа HTTP: сценарий из пяти запросов"
    specs = SPECS
    module = "http_api"
    format = "predict_output"
    target_grade = "junior"
    position = 0.0
    c = 0.0
    time_limit_s = 150
    is_anchor = True
    enemies = ("http.cache.revalidation",)  # оба проверяют условные запросы и 304

    def normalize(self, answer: Any) -> Any:
        if isinstance(answer, list | tuple):
            answer = " ".join(str(x) for x in answer)
        if not isinstance(answer, str):
            return answer
        codes = _CODE_RE.findall(answer)
        return " ".join(codes) if codes else answer.strip()

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        res, res_title = rng.choice(_RESOURCES)
        base = f"/api/v1/{res}"
        u1, u2, u3 = rng.sample(_USERS, 3)
        admin = rng.choice(_ADMINS)
        titles = rng.sample(_TITLES, 5)
        ids = rng.sample(range(100, 999), 4)
        records = [  # (id, автор, title, etag)
            (ids[0], u1, titles[0], _etag(rng)),
            (ids[1], u2, titles[1], _etag(rng)),
            (ids[2], u1, titles[2], _etag(rng)),
        ]
        absent_id = ids[3]
        new_title = titles[3]

        def tok(user: str) -> str:
            return f"Authorization: Bearer tok_{user}"

        def own() -> tuple[int, str, str, str]:
            return rng.choice(records)

        # Сценарии: (код, строки запроса, пояснение). Каждый запрос независим и выполняется
        # против исходного состояния таблицы — порядок проверок важен (сначала аутентификация).
        def unauth() -> tuple[int, list[str], str]:
            rid = own()[0]
            return 401, [f"GET {base}/{rid}"], "нет заголовка Authorization — сервер не знает, кто это → 401"

        def forbidden() -> tuple[int, list[str], str]:
            rid, author, _, _ = own()
            stranger = rng.choice([u for u in (u1, u2, u3) if u != author])
            method = rng.choice(("GET", "DELETE"))
            return 403, [f"{method} {base}/{rid}", tok(stranger)], (
                f"{stranger} аутентифицирован, но не автор записи {rid} и не администратор → 403"
            )

        def created() -> tuple[int, list[str], str]:
            user = rng.choice((u1, u2, u3))
            return 201, [
                f"POST {base}", tok(user), "Content-Type: application/json", f'{{"title": "{new_title}"}}'
            ], "новая запись создана → 201 Created"

        def not_found() -> tuple[int, list[str], str]:
            user = rng.choice((u1, u2))
            return 404, [f"GET {base}/{absent_id}", tok(user)], f"записи {absent_id} нет → 404"

        def deleted() -> tuple[int, list[str], str]:
            rid, author, _, _ = own()
            who = rng.choice((author, admin))
            return 204, [f"DELETE {base}/{rid}", tok(who)], (
                f"удаление разрешено ({'автор' if who == author else 'администратор'}), тело пустое → 204 No Content"
            )

        def put_ok() -> tuple[int, list[str], str]:
            rid, author, title, etag = own()
            return 200, [
                f"PUT {base}/{rid}", tok(author), f"If-Match: {etag}", "Content-Type: application/json",
                f'{{"title": "{title} v2"}}',
            ], "ETag совпал, автор обновил запись, в ответе — обновлённая запись → 200 OK"

        def method() -> tuple[int, list[str], str]:
            rid, author, _, _ = own()
            if rng.random() < 0.5:
                return 405, [f"DELETE {base}", tok(admin)], "коллекция не поддерживает DELETE → 405 Method Not Allowed"
            return 405, [
                f"PATCH {base}/{rid}", tok(author), "Content-Type: application/json", '{"title": "Черновик"}'
            ], "запись не поддерживает PATCH → 405 Method Not Allowed"

        def precondition() -> tuple[int, list[str], str]:
            rid, author, title, etag = own()
            stale = _etag(rng)
            while stale == etag:
                stale = _etag(rng)
            return 412, [
                f"PUT {base}/{rid}", tok(author), f"If-Match: {stale}", "Content-Type: application/json",
                f'{{"title": "{title} v2"}}',
            ], f"If-Match {stale} не совпадает с текущим ETag {etag} → 412 Precondition Failed"

        def not_modified() -> tuple[int, list[str], str]:
            rid, author, _, etag = own()
            return 304, [f"GET {base}/{rid}", tok(author), f"If-None-Match: {etag}"], (
                "If-None-Match совпал с текущим ETag — у клиента актуальная копия → 304 Not Modified"
            )

        def conflict() -> tuple[int, list[str], str]:
            dup = rng.choice(records)[2]
            user = rng.choice((u1, u2, u3))
            return 409, [
                f"POST {base}", tok(user), "Content-Type: application/json", f'{{"title": "{dup}"}}'
            ], f"title «{dup}» уже занят, запрос конфликтует с состоянием → 409 Conflict"

        def media() -> tuple[int, list[str], str]:
            user = rng.choice((u1, u2, u3))
            return 415, [f"POST {base}", tok(user), "Content-Type: text/plain", f"title={new_title}"], (
                "тело не в application/json → 415 Unsupported Media Type"
            )

        basic = [unauth, forbidden, created, not_found, deleted, put_ok]
        advanced = [method, precondition, not_modified, conflict, media]
        # 3 базовых + 2 продвинутых — одинаковая трудность у всех клонов
        chosen = rng.sample(basic, 3) + rng.sample(advanced, 2)
        rng.shuffle(chosen)
        scenarios = [f() for f in chosen]
        codes = [s[0] for s in scenarios]
        key = " ".join(map(str, codes))

        def mapped(mapping: dict[int, int]) -> str:
            return " ".join(str(mapping.get(c, c)) for c in codes)

        distractors = text_distractors(
            key,
            [
                (mapped({401: 403, 403: 401}), "auth_swap"),
                (mapped({201: 200, 204: 200}), "success_200"),
                (mapped({304: 200, 412: 409}), "conditional_confusion"),
                (mapped({405: 404}), "method_404"),
                (mapped({409: 400, 415: 400}), "all_400"),
            ],
            self.normalize,
        )

        table = [f"{'id':<5} {'автор':<7} {'title':<22} ETag"]
        for rid, author, title, etag in records:
            quoted = f'"{title}"'
            table.append(f"{rid:<5} {author:<7} {quoted:<22} {etag}")
        reqs = []
        for i, (_, lines, _) in enumerate(scenarios, 1):
            reqs.append(f"{i}) {lines[0]}")
            reqs += [f"   {line}" for line in lines[1:]]
            reqs.append("")
        code = "Записи в базе:\n" + "\n".join(table) + "\n\nЗапросы:\n" + "\n".join(reqs).rstrip()
        prompt = (
            f"Сервис «{res_title}» отдаёт REST API и строго следует семантике кодов ответа RFC 9110.\n"
            f"• Аутентификация — заголовок Authorization: Bearer <токен>; tok_{u1}, tok_{u2}, tok_{u3} — "
            f"обычные пользователи, tok_{admin} — администратор.\n"
            "• Читать, изменять и удалять запись может только её автор или администратор; существование "
            "чужих записей не скрывается.\n"
            f"• Коллекция {base} поддерживает GET и POST, запись {base}/{{id}} — GET, PUT и DELETE.\n"
            "• Тело запроса принимается только в application/json; поле title должно быть уникальным.\n"
            "• Успешный PUT возвращает обновлённую запись, успешный DELETE — пустое тело; поддерживаются "
            "условные запросы по ETag.\n\n"
            "Каждый запрос выполняется независимо от остальных, против состояния из таблицы. "
            "Какой код ответа получит каждый из пяти запросов?"
        )
        body = "\n".join(f"{i}) {s[0]}: {s[2]}." for i, s in enumerate(scenarios, 1)) + f"\nОтвет: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code,
                language="text",
                answer_mode="text",
                answer_hint="Пять трёхзначных кодов через пробел — для запросов 1–5 по порядку.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
