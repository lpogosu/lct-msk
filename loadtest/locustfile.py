"""Нагрузочный сценарий «Взаимно» (Locust, MIT): кандидаты, работодатели, публичные страницы.

    make loadtest-accounts      # аккаунты для сценария (после make demo)
    make loadtest USERS=50      # прогон, отчёт в loadtest/out/

Состав виртуальных пользователей (веса): кандидат — 5, работодатель — 2, гость (публичные страницы) — 3.
Кандидат: вход → опрос (4 шага) → запуск теста → ответы на задания до конца сессии → результат → «мои приглашения».
Работодатель: вход → разбор брифа (parse) → создание брифа → категории и охват вилки → подборка → карточка → банк →
шорт-лист → приглашение. Ответ 409/429 на приглашение (лимиты, активная пара) — штатная бизнес-ошибка, не сбой.
Сбоем считаются 5xx, таймауты и неожиданные 4xx. Учётные записи — loadtest/accounts.json (не в git).
"""

from __future__ import annotations

import json
import os
import queue
import random
import re
from pathlib import Path

from locust import HttpUser, between, task

ACCOUNTS = json.loads(
    Path(os.environ.get("LT_ACCOUNTS", Path(__file__).with_name("accounts.json"))).read_text(encoding="utf-8")
)
# Каждый кандидат проходит полный тест на своём аккаунте (повторный тест ограничен), поэтому аккаунты выдаются по одному
_CANDIDATES: queue.Queue = queue.Queue()
for _a in ACCOUNTS["candidates"]:
    _CANDIDATES.put(_a)
_EMPLOYERS = ACCOUNTS["employers"]

BRIEFS = [
    "Бэкенд в команду платёжного шлюза. Python, FastAPI, PostgreSQL, опыт от 2 лет. Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки",
    "Нужен backend-разработчик Middle: Python, Django, PostgreSQL, Redis. Удалённо. Вилка 200–260 тыс. на руки",
    "Junior backend: Python, SQL, основы HTTP API. Офис, Санкт-Петербург. 90–130 тыс. на руки",
    "Senior backend: Python, FastAPI, асинхронность, очереди, Kubernetes. Удалённо. 330–420 тыс. на руки",
    "Frontend Middle: React, TypeScript, REST. Гибрид, Москва. 200–270 тыс. на руки",
]


def _fake_ip() -> str:
    """У каждого виртуального пользователя свой «клиентский» адрес (X-Forwarded-For): лимиты входа считаются по IP.
    nginx верит заголовку только от доверенных адресов (VZ_REAL_IP_FROM, см. docs/LOAD_TEST.md)."""
    return "10.{}.{}.{}".format(random.randint(1, 250), random.randint(1, 250), random.randint(1, 250))


class _Auth(HttpUser):
    abstract = True
    token: str | None = None
    account: dict | None = None

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.client.headers["X-Forwarded-For"] = _fake_ip()

    def _login(self) -> bool:
        with self.client.post("/api/auth/login", json=self.account, name="POST /api/auth/login", catch_response=True) as r:
            if r.status_code == 200:
                self.token = r.json()["access_token"]
                return True
            r.failure(f"login {r.status_code}")
            return False

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def api(self, method: str, path: str, name: str | None = None, ok: tuple = (200, 201), **kw):
        """Запрос с токеном; повторный вход при 401 (access-токен живёт 15 минут); коды из `ok` — успех."""
        r = None
        for attempt in (0, 1):
            with self.client.request(
                method, path, headers=self._h(), name=name or f"{method} {path}", catch_response=True, **kw
            ) as r:
                if r.status_code == 401 and attempt == 0 and self._login():
                    r.success()
                    continue
                if r.status_code in ok:
                    r.success()
                else:
                    r.failure(f"{r.status_code}: {r.text[:120]}")
                return r
        return r


class Candidate(_Auth):
    weight = 5
    wait_time = between(1, 3)

    def on_start(self) -> None:
        try:
            self.account = _CANDIDATES.get_nowait()
            fresh = True
        except queue.Empty:
            self.account = random.choice(ACCOUNTS["candidates"])  # аккаунты кончились: только просмотр
            fresh = False
        self.done_flow = not (self._login() and fresh)

    @task
    def flow(self) -> None:
        if self.done_flow:
            self.browse()
            return
        self.done_flow = True
        self.api("GET", "/api/candidates/me")
        form = self.api("GET", "/api/survey/form").json()
        techs = [t["code"] for t in form["technologies"]][:4]
        acts = random.sample([a["code"] for a in form["actions"]], 5)
        self.api("PUT", "/api/survey/steps/industries", json={"industries": ["fintech"]})
        self.api("PUT", "/api/survey/steps/actions",
                 json={"actions": {a: random.choice(["sometimes", "regularly"]) for a in acts}})
        self.api("PUT", "/api/survey/steps/stack",
                 json={"technologies": dict.fromkeys(techs, "now"),
                       "experience_months": random.choice([12, 30, 48]), "roles": [], "self_assessment": {}})
        self.api("PUT", "/api/survey/steps/specialization", json={"chosen_spec": "backend_python"})
        self.api("GET", "/api/tests/briefing", params={"specialization": "backend_python"})
        r = self.api("POST", "/api/tests/sessions",
                     json={"specialization": "backend_python", "grade": "middle", "consent_test_logging": True})
        if r.status_code != 201:
            return
        sid = r.json()["id"]
        for _ in range(40):  # потолок на случай бесконечной сессии
            nxt = self.api("GET", f"/api/tests/sessions/{sid}/next", name="GET /api/tests/sessions/{id}/next")
            item = nxt.json().get("item") if nxt.status_code == 200 else None
            if not item:
                break
            self.wait()  # «читает задание»
            ans = self.api("POST", f"/api/tests/sessions/{sid}/answer", name="POST /api/tests/sessions/{id}/answer",
                           ok=(200, 201, 409, 422), json={"seq": item["seq"], "answer": self._answer(item)})
            if ans.status_code == 200 and ans.json().get("finished"):
                break
        self.api("GET", f"/api/tests/sessions/{sid}/result", name="GET /api/tests/sessions/{id}/result")
        self.api("GET", "/api/tests/qualification")

    @staticmethod
    def _answer(item: dict):
        mode, opts, lines = item.get("answer_mode"), item.get("options") or [], item.get("lines") or []
        if mode == "choice" and opts:
            return random.choice(opts)["id"]
        if mode == "line_choice" and opts:
            return {"line": random.randint(1, max(1, len(lines))), "fix": random.choice(opts)["id"]}
        if mode == "order" and lines:
            idx = list(range(len(lines)))
            random.shuffle(idx)
            return idx
        return random.choice(["A", "1", "[1, 2]", "None"])

    def browse(self) -> None:
        self.api("GET", "/api/auth/me")
        self.api("GET", "/api/candidates/me")
        self.api("GET", "/api/tests/qualification")
        self.api("GET", "/api/invitations/inbox")
        self.api("GET", "/api/vacancies")


class Employer(_Auth):
    weight = 2
    wait_time = between(2, 6)

    def on_start(self) -> None:
        self.account = random.choice(_EMPLOYERS)
        self._login()

    @task(3)
    def brief_and_selection(self) -> None:
        text = random.choice(BRIEFS)
        self.api("POST", "/api/needs/parse", json={"text": text})
        r = self.api("POST", "/api/needs", json={"text": text})
        if r.status_code != 201:
            return
        nid = r.json()["id"]
        self.api("GET", f"/api/needs/{nid}/categories", name="GET /api/needs/{id}/categories")
        self.api("GET", f"/api/needs/{nid}/check", name="GET /api/needs/{id}/check")
        v = self.api("POST", f"/api/needs/{nid}/versions", name="POST /api/needs/{id}/versions", json={})
        items = v.json().get("items", []) if v.status_code == 201 else []
        self.api("GET", f"/api/needs/{nid}/selection", name="GET /api/needs/{id}/selection")
        if items:
            it = random.choice(items)
            self.api("GET", f"/api/needs/{nid}/candidates/{it['ref']}", name="GET /api/needs/{id}/candidates/{ref}")
            self.api("PUT", f"/api/needs/{nid}/shortlist/{it['ref']}", name="PUT /api/needs/{id}/shortlist/{ref}")
            # 409/429 — лимиты и «активная пара»: штатный отказ бизнес-правил
            self.api("POST", "/api/invitations", ok=(200, 201, 409, 422, 429),
                     json={"need_id": nid, "candidate": it["ref"]})

    @task(3)
    def bank(self) -> None:
        self.api("GET", "/api/bank", params={"page": random.randint(1, 5)}, name="GET /api/bank")
        self.api("GET", "/api/bank/summary")

    @task(2)
    def dashboard(self) -> None:
        self.api("GET", "/api/needs")
        self.api("GET", "/api/invitations")
        self.api("GET", "/api/vacancies/mine")


class Guest(HttpUser):
    weight = 3
    wait_time = between(1, 5)

    def on_start(self) -> None:
        self.client.headers["X-Forwarded-For"] = _fake_ip()
        r = self.client.get("/", name="GET / (index.html)")
        self.assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', r.text) if r.status_code == 200 else []

    @task(3)
    def landing(self) -> None:
        self.client.get("/", name="GET / (index.html)")
        for a in self.assets:
            self.client.get(a, name="GET /assets/*")

    @task(2)
    def spa_route(self) -> None:
        self.client.get(random.choice(["/login", "/register", "/candidate/fsp/callback"]), name="GET /<spa-route>")

    @task(2)
    def health(self) -> None:
        self.client.get("/api/health", name="GET /api/health")
        self.client.get("/api/fsp/availability", name="GET /api/fsp/availability")

    @task(1)
    def docs(self) -> None:
        self.client.get("/api/docs", name="GET /api/docs")
