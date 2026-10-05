"""ops.nginx.location: какой location обслужит запрос.

Точный алгоритм nginx (ngx_http_core_module, «location»): 1) запрос без строки запроса сверяется с точными `= /uri`;
совпало — всё; 2) иначе выбирается самый длинный совпавший префикс (обычный и `^~`); если он с `^~` — всё; 3) иначе
по порядку в файле проверяются регулярные `~` (с учётом регистра) и `~*` (без учёта), побеждает ПЕРВОЕ совпавшее;
4) иначе используется запомненный самый длинный префикс. Регулярные выражения — подмножество PCRE, общее с Python re.
"""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    OpsFamily,
    Scenario,
    ScenarioError,
    numeric_alternatives,
)

_LOC_RE = re.compile(r"^\s*location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{", re.M)
_REQ_RE = re.compile(r"^(?:GET|POST|HEAD)\s+(\S+)\s*$", re.M)
_BODIES = {
    "=": "return 200;",
    "": "proxy_pass http://app;",
    "^~": "root /srv/www;",
    "~": "fastcgi_pass php;",
    "~*": "expires 7d;",
}


def parse_blocks(code: str) -> list[tuple[str, str]]:
    blocks = [(m.group(1) or "", m.group(2)) for m in _LOC_RE.finditer(code)]
    if not blocks:
        raise ScenarioError("нет location")
    return blocks


def parse_requests(code: str) -> list[str]:
    reqs = [m.group(1) for m in _REQ_RE.finditer(code)]
    if not reqs:
        raise ScenarioError("нет запросов")
    return reqs


def match_location(
    path: str,
    blocks: list[tuple[str, str]],
    *,
    prefix_beats_regex: bool = False,
    regex_beats_caret: bool = False,
    first_prefix: bool = False,
    longest_regex: bool = False,
    tilde_insensitive: bool = False,
) -> int:
    """Номер (с 1) location, обслуживающего путь. Флаги — модели заблуждений."""
    for i, (mod, pat) in enumerate(blocks):
        if mod == "=" and path == pat:
            return i + 1
    best: int | None = None
    for i, (mod, pat) in enumerate(blocks):
        if mod in ("", "^~") and path.startswith(pat):
            if first_prefix:
                best = i if best is None else best
            elif best is None or len(pat) > len(blocks[best][1]):
                best = i
    if best is not None and blocks[best][0] == "^~" and not regex_beats_caret:
        return best + 1
    if not (prefix_beats_regex and best is not None):
        hits = []
        for i, (mod, pat) in enumerate(blocks):
            if mod in ("~", "~*"):
                flags = re.I if (mod == "~*" or tilde_insensitive) else 0
                if re.search(pat, path, flags):
                    hits.append(i)
        if hits:
            if longest_regex:
                return max(hits, key=lambda i: len(blocks[i][1])) + 1
            return hits[0] + 1
    if best is None:
        raise ScenarioError("нет подходящего location")
    return best + 1


_REGEX_POOL = [("~", r"\.php$"), ("~*", r"\.(jpg|jpeg|png|gif)$"), ("~*", r"\.(css|js)$"), ("~", r"\.xml$"),
               ("~", r"^/admin/.*\.html$"), ("~*", r"^/docs/.*\.pdf$")]
_CARET_POOL = ["/static/v{n}/", "/assets/v{n}/", "/media/v{n}/", "/downloads/v{n}/"]
_PREFIX_POOL = ["/docs/", "/admin/", "/files/", "/shop/"]
_EXACT_POOL = ["/health", "/login", "/robots.txt", "/status"]


@register
class NginxLocation(OpsFamily):
    id = "ops.nginx.location"
    title = "nginx: какой location обслужит запрос"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "senior"
    position = 0.1
    time_limit_s = 180
    is_anchor = True
    answer_kind = "ints"
    language = "text"
    measures = (
        "Порядок выбора location: точное совпадение, самый длинный префикс, ^~ останавливает поиск, регулярные "
        "по порядку в файле (~ с регистром, ~* без), запасной префикс."
    )
    misconceptions = {
        "prefix_beats_regex": "регулярное выражение перекрывает обычный префикс: после выбора самого длинного префикса "
        "nginx проверяет регулярные location и берёт первое совпавшее",
        "regex_beats_caret": "префикс с ^~ останавливает поиск: регулярные location после него не проверяются",
        "first_prefix": "из префиксных location побеждает самый длинный совпавший, а не первый в файле",
        "longest_regex": "среди регулярных location побеждает первое совпавшее по порядку в файле, а не самое длинное "
        "выражение",
        "tilde_insensitive": "~ сравнивает с учётом регистра; без учёта — только ~*",
    }
    answer_hint = "Введите номера блоков location (как в комментариях) для запросов по порядку, через пробел."

    def make(self, rng: random.Random) -> Scenario:
        ver_block, ver_other = rng.randint(1, 4), rng.randint(1, 4)
        blocks: list[tuple[str, str]] = [("", "/"), ("", "/api/"), ("", f"/api/v{ver_block}/")]
        caret_ver, caret_other = rng.randint(1, 4), rng.randint(1, 4)
        blocks.append(("^~", rng.choice(_CARET_POOL).format(n=caret_ver)))
        blocks.append(("=", rng.choice(_EXACT_POOL)))
        blocks += rng.sample(_REGEX_POOL, 2)
        if rng.random() < 0.6:
            blocks.append(("", rng.choice(_PREFIX_POOL)))
        rng.shuffle(blocks)
        caret = next(p for m, p in blocks if m == "^~")  # например /static/v2/
        exact = next(p for m, p in blocks if m == "=")
        caret_other_path = caret.replace(f"v{caret_ver}", f"v{caret_other}")
        pool = [
            f"/api/v{ver_block}/items.json?page=next", f"/api/v{ver_other}/users/me", "/api/orders/list",
            f"{caret}css/site.css", f"{caret}img/Logo.PNG", f"{caret_other_path}app.js", f"{caret_other_path}run.php",
            "/images/photo.jpg", "/images/PHOTO.JPG", "/shop/cart.php", "/admin/index.php", "/admin/report.html",
            "/docs/guide.pdf", "/docs/Guide.PDF", "/index.html", "/", exact, f"{exact}x", "/app.js", "/files/data.csv",
        ]
        reqs = rng.sample(pool, 6)
        # Всегда есть запросы, чей ответ зависит от цифры версии: в префиксе /api/vN/ и в ^~-префиксе.
        a, b = rng.sample(range(6), 2)
        reqs[a] = f"/api/v{rng.choice([ver_block, ver_other])}/ping"
        reqs[b] = f"{rng.choice([caret, caret_other_path])}main.js"
        lines = ["server {", "    listen 80;"]
        for i, (mod, pat) in enumerate(blocks, start=1):
            head = f"location {mod + ' ' if mod else ''}{pat}"
            lines.append(f"    {head} {{ {_BODIES[mod]} }}  # {i}")
        lines.append("}")
        lines.append("")
        lines += [f"GET {r}" for r in reqs]
        code = "\n".join(lines)
        sc = Scenario(code)
        try:
            answers = [match_location(r.split("?")[0], blocks) for r in reqs]
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(set(answers)) < 4:
            raise GenerationRetry
        return sc

    def prompt(self, sc: Scenario) -> str:
        return (
            "Показана конфигурация nginx (номер блока — в комментарии справа) и шесть запросов. Строка запроса "
            "(после ?) при выборе location не учитывается. Какой блок location обслужит каждый запрос? "
            "Назовите номера в порядке запросов."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        flags: dict[str, Any] = {
            None: {}, "prefix_beats_regex": {"prefix_beats_regex": True}, "regex_beats_caret": {"regex_beats_caret": True},
            "first_prefix": {"first_prefix": True}, "longest_regex": {"longest_regex": True},
            "tilde_insensitive": {"tilde_insensitive": True},
        }[mutant]
        blocks = parse_blocks(code)
        return " ".join(str(match_location(r.split("?")[0], blocks, **flags)) for r in parse_requests(code))

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Алгоритм nginx: сначала точное совпадение (=); затем запоминается самый длинный совпавший префикс, и если он "
            "с ^~, поиск заканчивается; иначе по порядку в файле проверяются регулярные (~ — с учётом регистра, ~* — без) "
            "и побеждает первое совпавшее; если регулярные не подошли, берётся запомненный префикс."
        )

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for m in _LOC_RE.finditer(code):
            if m.group(1) in (None, "=", "^~"):  # в регулярных выражениях цифры — часть шаблона ([0-9])
                spans += [(m.start(2) + d.start(), m.start(2) + d.end()) for d in re.finditer(r"\d+", m.group(2))]
        for m in _REQ_RE.finditer(code):
            path = m.group(1).split("?")[0]
            spans += [(m.start(1) + d.start(), m.start(1) + d.end()) for d in re.finditer(r"\d+", path)]
        return spans

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: Any) -> list[str]:
        old = code[span[0] : span[1]]
        lo = 1 if len(old) == 1 else 10 ** (len(old) - 1)
        return numeric_alternatives(old, rng, p, lo, 10 ** len(old) - 1)
