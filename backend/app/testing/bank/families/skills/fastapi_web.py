"""fastapi_core: поведение FastAPI на уровне навыка (проверочный набор по вакансии, навык «FastAPI»).

Ключ — РЕАЛЬНОЕ исполнение показанного кода в FastAPI через TestClient (starlette, httpx; они уже в зависимостях
бэкенда): ни модели, ни ручных ответов. Исполняется только наш собственный сгенерированный код и только офлайн, при
генерации пула (spec-testing §3.8), в рантайме теста код не запускается. Дистракторы — исполнение того же кода с
типичным заблуждением, внесённым в текст (мутант) или в слой обработки ошибок.
Наблюдаем только устойчивое поведение: коды ответа валидации и зависимости (порядок вызовов, кеш Depends, закрытие
зависимостей с yield) — не то, что менялось между версиями FastAPI.
"""

from __future__ import annotations

import random
import re

from app.testing.bank.base import register
from app.testing.bank.families.backend_python._common import exec_py
from app.testing.bank.families.skills._common import (
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    SkillFamily,
)

MODULE = "fastapi_core"

# ============================================================== коды ответа: валидация, заголовки, пути

_HEAD = '''from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

app = FastAPI()
'''

_MODEL = '''

class Order(BaseModel):
    sku: str = Field(min_length={minlen})
    qty: int = Field(ge=1, le={qmax})


@app.post("/orders"{status})
def create(order: Order):
    return {{"sku": order.sku}}
'''

_READ = '''

@app.get("/orders/{{order_id}}")
def read(order_id: int, limit: int = Query(5, le={lmax}), x_token: str = Header()):
    if x_token != "secret":
        raise HTTPException(status_code=401)
    if order_id > {oid}:
        raise HTTPException(status_code=404)
    return {{"id": order_id}}
'''

_TAIL = '''

client = TestClient(app)
calls = [
{calls}
]
for method, url, body, headers in calls:
    print(client.request(method, url, json=body, headers=headers).status_code)
'''

_OK = '{"x-token": "secret"}'


def run_app(code: str, mutant: str | None = None) -> str:
    """Выполнить код приложения (с мутантом) и вернуть напечатанные коды ответа."""
    if mutant == "validation_400":
        code = code.replace(
            "client = TestClient(app)",
            "from fastapi.exceptions import RequestValidationError\n"
            "from fastapi.responses import JSONResponse\n\n\n"
            "@app.exception_handler(RequestValidationError)\n"
            "def bad_request(request, exc):\n"
            "    return JSONResponse(status_code=400, content={})\n\n\n"
            "client = TestClient(app)",
        )
    elif mutant == "strict_qty":
        code = code.replace("Field(ge=1", "Field(strict=True, ge=1")
    elif mutant == "header_optional_401":
        code = code.replace("x_token: str = Header()", "x_token: str | None = Header(None)")
    elif mutant == "query_le_ignored":
        code = re.sub(r"Query\((\d+), le=\d+\)", r"Query(\1)", code)
    elif mutant == "sku_unchecked":
        code = re.sub(r"Field\(min_length=\d+\)", "Field()", code)
    return exec_py(code)


def _calls(rng: random.Random, minlen: int, qmax: int, lmax: int, oid: int, has_model: bool, has_read: bool) -> list[str]:
    ok_sku = "x" * (minlen + rng.randint(0, 2))
    short = "x" * rng.randint(0, minlen - 1)
    pool: list[tuple[str, str]] = []
    if has_model:
        pool += [
            ("post_ok", f'("POST", "/orders", {{"sku": "{ok_sku}", "qty": {rng.randint(1, qmax)}}}, None)'),
            ("post_short", f'("POST", "/orders", {{"sku": "{short}", "qty": 1}}, None)'),
            ("post_big", f'("POST", "/orders", {{"sku": "{ok_sku}", "qty": {qmax + rng.randint(1, 5)}}}, None)'),
            ("post_str", f'("POST", "/orders", {{"sku": "{ok_sku}", "qty": "{rng.randint(1, qmax)}"}}, None)'),
            ("post_frac", f'("POST", "/orders", {{"sku": "{ok_sku}", "qty": {rng.randint(1, qmax)}.5}}, None)'),
            ("post_missing", f'("POST", "/orders", {{"sku": "{ok_sku}"}}, None)'),
        ]
    if has_read:
        pool += [
            ("get_ok", f'("GET", "/orders/{rng.randint(1, oid)}", None, {_OK})'),
            ("get_no_header", f'("GET", "/orders/{rng.randint(1, oid)}", None, None)'),
            ("get_bad_token", f'("GET", "/orders/{rng.randint(1, oid)}", None, {{"x-token": "guess"}})'),
            ("get_abc", f'("GET", "/orders/abc", None, {_OK})'),
            ("get_big_id", f'("GET", "/orders/{oid + rng.randint(1, 50)}", None, {_OK})'),
            ("get_limit", f'("GET", "/orders/{rng.randint(1, oid)}?limit={lmax + rng.randint(1, 30)}", None, {_OK})'),
            ("get_limit_ok", f'("GET", "/orders/{rng.randint(1, oid)}?limit={rng.randint(1, lmax)}", None, {_OK})'),
        ]
    pool += [
        ("typo_path", f'("GET", "/order/{rng.randint(1, oid)}", None, {_OK})'),
        ("wrong_method", f'("DELETE", "/orders/{rng.randint(1, oid)}", None, {_OK})'),
    ]
    picked = rng.sample(pool, 6)
    return [c for _, c in picked]


@register
class FastapiStatusCodes(SkillFamily):
    id = "fastapi.status_codes"
    title = "FastAPI: коды ответа валидации, заголовков и путей"
    specs = SPECS
    module = MODULE
    observes = ("fastapi", "pydantic")
    format = "predict_output"
    target_grade = "middle"
    position = -0.3
    time_limit_s = 180
    answer_kind = "ints"
    language = "python"
    measures = (
        "Что отвечает FastAPI: 422 на ошибки валидации тела, query и path (в том числе на отсутствующий обязательный "
        "заголовок), мягкое приведение типов pydantic, 401/404 из HTTPException, объявленный status_code, 404 на неизвестный "
        "путь и 405 на неверный метод."
    )
    misconceptions = {
        "validation_400": "ошибки валидации запроса FastAPI отдаёт со статусом 422, а не 400",
        "strict_qty": "pydantic по умолчанию приводит типы: строка «3» для поля int принимается, а дробное 2.5 — нет",
        "header_optional_401": "обязательный заголовок, которого нет в запросе, даёт 422 ещё до тела функции — до проверки токена дело не доходит",
        "query_le_ignored": "ограничения Query(…, le=…) проверяются: значение выше предела даёт 422",
        "sku_unchecked": "ограничения Field(min_length=…) проверяются автоматически: слишком короткая строка даёт 422",
    }
    answer_hint = "Коды ответа по порядку вызовов через пробел (как напечатает программа)."
    twin_patterns = (r"min_length=(\d+)", r"le=(\d+)", r"order_id > (\d+)")
    twin_range = (2, 120)

    def make(self, rng: random.Random) -> Scenario:
        has_model = rng.random() < 0.8
        has_read = rng.random() < 0.8 or not has_model
        minlen, qmax, lmax, oid = rng.randint(2, 5), rng.choice((5, 10, 20)), rng.choice((10, 20, 50)), rng.choice((100, 200, 500))
        status = ", status_code=201" if rng.random() < 0.6 else ""
        body = _HEAD
        if has_model:
            body += _MODEL.format(minlen=minlen, qmax=qmax, status=status)
        if has_read:
            body += _READ.format(lmax=lmax, oid=oid)
        calls = ",\n".join("    " + c for c in _calls(rng, minlen, qmax, lmax, oid, has_model, has_read))
        code = body + _TAIL.format(calls=calls)
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(set(base.split())) < 3 or len(alts - {base, None}) < 2:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Показано приложение FastAPI и серия запросов через TestClient. Что напечатает программа — "
            "HTTP-статусы ответов по порядку запросов?"
        )

    @staticmethod
    def _applies(code: str, mutant: str) -> bool:
        """Мутант, который не может изменить ответ этого текста, не исполняем: каждое исполнение — приложение и TestClient."""
        if mutant == "strict_qty":
            return bool(re.search(r'"qty": "\d+"', code))
        if mutant == "header_optional_401":
            return "Header()" in code and bool(re.search(r'"/orders/\d+[^"]*", None, None\)', code))
        if mutant == "query_le_ignored":
            return "Query(" in code and "limit=" in code
        if mutant == "sku_unchecked":
            return "min_length" in code
        return True

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        if mutant is not None and not self._applies(code, mutant):
            return None
        out = run_app(code, mutant)
        if not re.fullmatch(r"\d{3}( \d{3})*|(\d{3}\n)*\d{3}", out.strip()):
            raise ScenarioError("в выводе не только коды")
        return " ".join(out.split())

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            f"FastAPI проверяет запрос до вызова функции: ошибки типов и ограничений тела, query и path дают 422, "
            f"отсутствующий обязательный заголовок — тоже 422 (проверка токена внутри функции не начинается), "
            f"HTTPException из функции даёт свой код, неизвестный путь — 404, неверный метод — 405. "
            f"Результат по порядку запросов: {raw_key}."
        )


# ============================================================== зависимости: порядок, кеш, закрытие

_DEPS_HEAD = '''from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
log = []
'''

_NAMES = ("db", "cache", "auth", "settings", "tenant", "limiter")


def _dep_src(name: str, yields: bool, subs: list[str], value: int) -> str:
    params = ", ".join(f"{s}=Depends({s})" for s in subs)
    if yields:
        return (
            f"\n\ndef {name}({params}):\n"
            f'    log.append("{name}:open")\n'
            f"    yield {value}\n"
            f'    log.append("{name}:close")\n'
        )
    return f'\n\ndef {name}({params}):\n    log.append("{name}")\n    return {value}\n'


def run_depends(code: str, mutant: str | None = None) -> str:
    if mutant == "no_cache":
        code = re.sub(r"Depends\((\w+)\)", r"Depends(\1, use_cache=False)", code)
    elif mutant == "cache_always":
        code = code.replace(", use_cache=False", "")
    elif mutant == "decorator_last":
        m = re.search(r', dependencies=\[Depends\((\w+)\)\]', code)
        if m:
            code = code.replace(m.group(0), "")
            code = re.sub(r"def handler\((.*)\):", lambda mm: f"def handler({mm.group(1)}, _extra=Depends({m.group(1)})):", code)
    out = exec_py(code)
    lines = out.split(" ") if out else []
    if mutant == "close_fifo":
        opens = [t for t in lines if t.endswith(":open")]
        closes = [t.replace(":open", ":close") for t in opens if t.replace(":open", ":close") in lines]
        it = iter(closes)
        lines = [next(it) if t.endswith(":close") else t for t in lines]
    elif mutant == "close_early":
        closes = [t for t in lines if t.endswith(":close")]
        rest = [t for t in lines if not t.endswith(":close")]
        i = next((k for k, t in enumerate(rest) if t.startswith("handler")), len(rest))
        lines = rest[:i] + closes + rest[i:]
    return " ".join(lines)


@register
class FastapiDependsLog(SkillFamily):
    id = "fastapi.depends_log"
    title = "FastAPI: порядок и кеш зависимостей Depends, закрытие yield"
    specs = SPECS
    module = MODULE
    observes = ("fastapi",)
    format = "predict_output"
    target_grade = "senior"
    position = -0.5
    time_limit_s = 210
    answer_kind = "output"
    language = "python"
    measures = (
        "Внедрение зависимостей FastAPI: порядок вызова вложенных Depends, одно выполнение зависимости на запрос "
        "(кеш use_cache), зависимости из dependencies=[…] декоратора идут первыми, код после yield выполняется в обратном порядке."
    )
    misconceptions = {
        "no_cache": "зависимость, указанная в нескольких местах одного запроса, выполняется один раз: результат берётся из кеша запроса",
        "cache_always": "use_cache=False отключает кеш для этого параметра: зависимость выполняется заново",
        "decorator_last": "зависимости из dependencies=[…] декоратора выполняются до зависимостей параметров функции",
        "close_fifo": "код после yield выполняется в обратном порядке открытия, как стек (последняя открытая закрывается первой)",
        "close_early": "код после yield выполняется после тела функции-обработчика, а не сразу после последнего использования",
    }
    answer_hint = "Записи log через пробел, как напечатает print(\" \".join(log)), например: db:open handler:3 db:close."
    twin_patterns = (r"(?:return|yield) (\d+)",)
    twin_range = (2, 40)

    def make(self, rng: random.Random) -> Scenario:
        names = rng.sample(_NAMES, 4)
        yields = set(rng.sample(names, 2))
        subs: dict[str, list[str]] = {}
        for i, n in enumerate(names):
            earlier = names[:i]
            subs[n] = rng.sample(earlier, min(len(earlier), rng.choice((0, 1, 1, 2)))) if earlier else []
        body = _DEPS_HEAD
        for n in names:
            body += _dep_src(n, n in yields, subs[n], rng.randint(2, 9))
        params = rng.sample(names, rng.choice((2, 3)))
        dyn = [n for n in names if n in yields and n in params]
        nocache = rng.choice(dyn) if dyn and rng.random() < 0.5 else None
        sig = ", ".join(
            f"p{i}=Depends({n}{', use_cache=False' if n == nocache else ''})" for i, n in enumerate(params)
        )
        rest = [n for n in names if n not in params]
        deco = ""
        if rest and rng.random() < 0.6:
            deco = f", dependencies=[Depends({rng.choice(rest)})]"
        total = " + ".join(f"p{i}" for i in range(len(params)))
        body += (
            f'\n\n@app.get("/x"{deco})\ndef handler({sig}):\n    log.append(f"handler:{{{total}}}")\n    return {{}}\n'
            '\n\nclient = TestClient(app)\nclient.get("/x")\nprint(" ".join(log))\n'
        )
        code = body
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
            "Показано приложение FastAPI с зависимостями. Один запрос GET /x через TestClient. "
            "Что напечатает программа — записи списка log по порядку?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        return run_depends(code, mutant)

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Сначала FastAPI разрешает зависимости из dependencies=[…] декоратора, затем параметры функции по порядку, "
            "каждую зависимость — вместе с её вложенными. Одна и та же зависимость в пределах запроса выполняется один "
            "раз (кеш), если не указан use_cache=False. Код после yield идёт после обработчика в обратном порядке. "
            f"Итог: {raw_key}."
        )
