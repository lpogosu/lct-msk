"""http.cors.* — какие запросы fetch() вызовут CORS preflight (Fetch Standard, «простые» запросы)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import SPECS, ChoiceFamily, build_choice, explain

_SIMPLE_METHODS = {"GET", "HEAD", "POST"}
_SAFELISTED = {"accept", "accept-language", "content-language", "content-type"}
_SIMPLE_CT = {"application/x-www-form-urlencoded", "multipart/form-data", "text/plain"}


@dataclass(frozen=True)
class _Req:
    kind: str
    method: str
    paths: tuple[str, ...]
    headers: tuple[tuple[str, str], ...] = ()  # (имя, JS-выражение значения)
    body: str = ""
    credentials: bool = False
    comment: str = ""
    content_type: str = field(default="")  # фактический Content-Type, если задан заголовком

    def code(self, path: str) -> str:
        opts: list[str] = []
        if self.method != "GET":
            opts.append(f'method: "{self.method}"')
        if self.headers:
            hs = ", ".join(f"{_js_key(n)}: {v}" for n, v in self.headers)
            opts.append(f"headers: {{ {hs} }}")
        if self.body:
            opts.append(f"body: {self.body}")
        if self.credentials:
            opts.append('credentials: "include"')
        head = f"// {self.comment}\n" if self.comment else ""
        if not opts:
            return f"{head}fetch(`${{API}}{path}`);"
        inner = "".join(f"  {o},\n" for o in opts)
        return f"{head}fetch(`${{API}}{path}`, {{\n{inner}}});"


def _js_key(name: str) -> str:
    return name if name.isidentifier() else f'"{name}"'


def _ct(value: str) -> tuple[tuple[str, str], ...]:
    return (("Content-Type", f'"{value}"'),)


_POOL = (
    _Req("get_plain", "GET", ("/v1/products", "/v1/news", "/v1/categories")),
    _Req("get_auth", "GET", ("/v1/profile", "/v1/orders", "/v1/notifications"),
         headers=(("Authorization", "`Bearer ${token}`"),)),
    _Req("get_custom", "GET", ("/v1/search?q=lamp", "/v1/stock"),
         headers=(("X-Request-Id", "crypto.randomUUID()"),)),
    _Req("get_accept", "GET", ("/v1/rates", "/v1/cities"), headers=(("Accept", '"application/json"'),)),
    _Req("get_credentials", "GET", ("/v1/me", "/v1/cart"), credentials=True),
    _Req("get_cache_control", "GET", ("/v1/prices", "/v1/balance"), headers=(("Cache-Control", '"no-cache"'),)),
    _Req("post_json", "POST", ("/v1/orders", "/v1/feedback"), headers=_ct("application/json"),
         body="JSON.stringify(order)", content_type="application/json"),
    _Req("post_form", "POST", ("/v1/upload", "/v1/avatar"), body="formData",
         comment="formData — объект FormData с файлом", content_type="multipart/form-data"),
    _Req("post_text", "POST", ("/v1/analytics", "/v1/logs"), headers=_ct("text/plain"),
         body="JSON.stringify(event)", content_type="text/plain"),
    _Req("post_urlenc", "POST", ("/v1/subscribe", "/v1/callback"), body="new URLSearchParams({ email })",
         content_type="application/x-www-form-urlencoded"),
    _Req("post_urlenc_cred", "POST", ("/v1/session",), body="new URLSearchParams({ login, password })",
         credentials=True, content_type="application/x-www-form-urlencoded"),
    _Req("delete", "DELETE", ("/v1/cart/items/42", "/v1/sessions/current")),
    _Req("put_json", "PUT", ("/v1/profile", "/v1/settings"), headers=_ct("application/json"),
         body="JSON.stringify(profile)", content_type="application/json"),
    _Req("patch_json", "PATCH", ("/v1/orders/17", "/v1/tasks/9"), headers=_ct("application/json"),
         body="JSON.stringify(patch)", content_type="application/json"),
    _Req("head", "HEAD", ("/v1/files/report.pdf", "/v1/health")),
)


def _preflight(r: _Req, mode: str = "correct") -> bool:
    """Нужен ли preflight. mode — модель кандидата с заблуждением."""
    names = {n.lower() for n, _ in r.headers}
    if mode == "only_methods":
        return r.method not in _SIMPLE_METHODS
    if mode == "any_header" and names:
        return True
    if mode == "post_always" and r.method == "POST":
        return True
    if mode == "credentials_preflight" and r.credentials:
        return True
    safelisted = set(_SAFELISTED)
    simple_ct = set(_SIMPLE_CT)
    if mode == "auth_simple":
        safelisted.add("authorization")
    if mode == "json_simple":
        simple_ct.add("application/json")
    if r.method not in _SIMPLE_METHODS:
        return True
    if names - safelisted:
        return True
    return "content-type" in names and r.content_type not in simple_ct


_REASON = {
    "get_plain": "GET без своих заголовков — простой запрос",
    "get_auth": "заголовок Authorization не входит в список CORS-safelisted → preflight",
    "get_custom": "собственный заголовок X-Request-Id → preflight",
    "get_accept": "Accept — safelisted-заголовок, запрос остаётся простым",
    "get_credentials": "credentials: \"include\" сам по себе preflight не вызывает (но ответ должен разрешить "
    "credentials)",
    "get_cache_control": "Cache-Control не входит в safelisted-заголовки → preflight",
    "post_json": "Content-Type: application/json не входит в три «простых» типа → preflight",
    "post_form": "FormData отправляется как multipart/form-data — простой POST",
    "post_text": "Content-Type: text/plain — «простой» тип, preflight не нужен",
    "post_urlenc": "URLSearchParams → application/x-www-form-urlencoded — простой POST",
    "post_urlenc_cred": "x-www-form-urlencoded + credentials — всё ещё простой запрос",
    "delete": "метод DELETE не входит в GET/HEAD/POST → preflight",
    "put_json": "метод PUT (и тип application/json) → preflight",
    "patch_json": "метод PATCH → preflight",
    "head": "HEAD — простой метод без заголовков",
}

HINTS = {
    "json_simple": "application/json — не «простой» Content-Type: простыми считаются только "
    "application/x-www-form-urlencoded, multipart/form-data и text/plain.",
    "post_always": "POST сам по себе preflight не вызывает — важны заголовки и Content-Type.",
    "credentials_preflight": "credentials: \"include\" не делает запрос «непростым»; он лишь требует "
    "Access-Control-Allow-Credentials в ответе.",
    "auth_simple": "Authorization не входит в CORS-safelisted заголовки — с ним всегда будет preflight.",
    "any_header": "не любой заголовок вызывает preflight: Accept, Accept-Language, Content-Language и "
    "Content-Type с простым значением разрешены.",
    "only_methods": "preflight вызывают не только методы: «непростые» заголовки и Content-Type тоже.",
}
_MODES = tuple(HINTS)


def _fmt(nums: list[int]) -> str:
    return ", ".join(map(str, nums)) if nums else "ни для одного"


@register
class CorsPreflight(ChoiceFamily):
    id = "http.cors.preflight"
    title = "CORS: какие fetch-запросы вызовут preflight"
    specs = SPECS
    module = "http_api"
    format = "predict_output"
    target_grade = "middle"
    position = 0.5
    c = 0.25
    time_limit_s = 120

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        while True:
            reqs = rng.sample(_POOL, 5)
            key_set = [i for i, r in enumerate(reqs, 1) if _preflight(r)]
            if not 2 <= len(key_set) <= 3:
                continue
            alt: dict[str, str] = {}
            for m in _MODES:
                alt[m] = _fmt([i for i, r in enumerate(reqs, 1) if _preflight(r, m)])
            key_text = _fmt(key_set)
            if len({v for v in alt.values() if v != key_text}) >= 3:
                break

        modes = list(_MODES)
        rng.shuffle(modes)
        wrong = [(alt[m], m) for m in modes]
        options, key, distractors = build_choice(rng, key_text, wrong, 4)

        tld = rng.choice(("example.com", "example.org", "example.net"))
        app_host = f"{rng.choice(('app', 'my', 'lk', 'shop', 'cabinet'))}.{tld}"
        api_host = f"{rng.choice(('api', 'backend', 'gw'))}.{tld}"
        blocks = [f'const API = "https://{api_host}";']
        for i, r in enumerate(reqs, 1):
            blocks.append(f"// {i}\n" + r.code(rng.choice(r.paths)))
        code = "\n\n".join(blocks)
        prompt = (
            f"Страница https://{app_host} обращается к API на другом origin — https://{api_host}. "
            "Для каких из пяти запросов браузер перед основным запросом отправит предварительный "
            "запрос OPTIONS (CORS preflight)?"
        )
        body = "\n".join(f"{i}) {_REASON[r.kind]}." for i, r in enumerate(reqs, 1))
        body += f"\nPreflight будет у запросов: {key_text}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(prompt=prompt, code=code, language="javascript", options=options, answer_mode="choice"),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
