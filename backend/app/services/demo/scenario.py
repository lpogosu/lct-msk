"""Сценарий демо-мира: компании, брифы с версиями подборки и шортлистами, приглашения во всех статусах, вакансии, отклики,
задачи работодателей. Всё строится публичным API приложения (app.services.demo.apidriver) — теми же роутами, что у фронтенда.

История идёт «из прошлого в настоящее»: общие часы платформы ставятся на момент события (clock_at), поэтому у приглашений
настоящие сроки и журнал переходов, а «истёкшие» истекают по правилам движка, а не правкой статуса. Живые состояния
(«отправлено», «просмотрено», «Да, если…» без ответа) создаются за несколько часов до «сейчас»: срок ответа не истёк.

Анна и Илья в историю не попадают: по сценарию §11 их приглашает и открывает вживую работодатель «Северный код».
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.settings import Settings
from app.models.company import Company
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact import sweep
from app.services.demo import accounts, autoresponder, personas
from app.services.demo.apidriver import Api, ApiError
from app.services.demo.cfg import section
from app.services.demo.clockctl import clock_at
from app.services.demo.models import DemoPersona
from app.services.matching import features as matching_features
from app.services.matching import resolve_ref

log = logging.getLogger("vzaimno.demo.scenario")


@dataclass
class Pick:
    candidate_id: Any
    ref: str
    pseudonym: str


@dataclass
class Counters:
    needs: int = 0
    versions: int = 0
    pins: int = 0
    invitations: int = 0
    vacancies: int = 0
    applications: int = 0
    employer_tasks: int = 0
    skipped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("needs", "versions", "pins", "invitations", "vacancies", "applications", "employer_tasks", "skipped")}


class Scenario:
    def __init__(self, db: Session, settings: Settings, api: Api, say: Callable[[str], None] = lambda _m: None) -> None:
        self.db, self.settings, self.api, self.say = db, settings, api, say
        self.base: datetime = clock.now()
        self.companies: dict[str, tuple[Company, User]] = {}
        self.need_ids: dict[str, str] = {}
        self.need_company: dict[str, str] = {}
        self.ranked: dict[str, list[Pick]] = {}
        self.pinned: dict[str, set[Any]] = {}
        self.vacancy_ids: dict[str, str] = {}
        self.used_invite: set[Any] = set()
        self.used_apply: set[Any] = set()
        self.invitations: list[dict[str, Any]] = []
        self.counters = Counters()

    # ------------------------------------------------------------------ время

    def at(self, hours_ago: float):  # noqa: ANN201 — контекстный менеджер часов
        return clock_at(self.base - timedelta(hours=hours_ago))

    # ------------------------------------------------------------------ компании

    def companies_up(self) -> None:
        for cfg in section("companies"):
            company, hr, _ = personas.ensure_company(self.db, self.settings, cfg)
            self.companies[cfg["key"]] = (company, hr)
        self.db.commit()

    def hr(self, company_key: str) -> User:
        return self.companies[company_key][1]

    # ------------------------------------------------------------------ брифы, версии, шортлисты

    def needs_up(self) -> None:
        h = section("invitation_hours_ago")
        matching_features.ensure_fresh(self.db)
        self.db.commit()
        early = float(h["early"]) + float(h["step_hours"])
        for cfg in section("needs"):
            hr = self.hr(cfg["company"])
            with self.at(early):
                need = self.api.post("/api/needs", hr, {"text": cfg["text"].strip(), "title": cfg["title"]})
                nid = need["id"]
                self.need_ids[cfg["key"]] = nid
                self.need_company[cfg["key"]] = cfg["company"]
                self.counters.needs += 1
                try:
                    self.api.post(f"/api/needs/{nid}/versions", hr, {})
                    self.counters.versions += 1
                except ApiError as e:
                    self.counters.skipped.append(f"версия брифа {cfg['key']}: {e.code or e.status}")
                    continue
                self.ranked[cfg["key"]] = self._synthetic_ranking(cfg["key"], hr)
                self.pinned[cfg["key"]] = set()
                for pick in self.ranked[cfg["key"]][: int(cfg["shortlist"])]:
                    self._pin(cfg["key"], hr, pick)
        self.db.commit()

    def _synthetic_ranking(self, need_key: str, hr: User, pages: int = 6) -> list[Pick]:
        """Кандидаты подборки по рангу — только синтетические (персоны сценария остаются для живого показа)."""
        nid = self.need_ids[need_key]
        company = self.companies[self.need_company[need_key]][0]
        out: list[Pick] = []
        for page in range(1, pages + 1):
            try:
                v = self.api.get(f"/api/needs/{nid}/selection", hr, params={"page": page})
            except ApiError:
                break
            items = v.get("items") or []
            if not items:
                break
            for it in items:
                cid = resolve_ref(self.db, company.id, it["ref"])
                if cid is None:
                    continue
                persona = self.db.get(DemoPersona, cid)
                if persona is not None and persona.kind == "synthetic":
                    out.append(Pick(cid, it["ref"], it["pseudonym"]))
            if page >= int(v.get("pages") or 1):
                break
        return out

    def _pin(self, need_key: str, hr: User, pick: Pick) -> None:
        if pick.candidate_id in self.pinned.setdefault(need_key, set()):
            return
        self.api.put(f"/api/needs/{self.need_ids[need_key]}/shortlist/{pick.ref}", hr, {})
        self.pinned[need_key].add(pick.candidate_id)
        self.counters.pins += 1

    def _next_pick(self, need_key: str, used: set[Any]) -> Pick | None:
        for p in self.ranked.get(need_key, []):
            if p.candidate_id not in used:
                used.add(p.candidate_id)
                return p
        return None

    # ------------------------------------------------------------------ приглашения

    def _send(self, entry: dict[str, Any], hours_ago: float, *, response_days: int | None = None) -> dict[str, Any] | None:
        need_key = entry["need"]
        hr = self.hr(entry["company"])
        pick = self._next_pick(need_key, self.used_invite)
        if pick is None:
            self.counters.skipped.append(f"приглашение {entry['status']} ({need_key}): нет свободных кандидатов в подборке")
            return None
        with self.at(hours_ago):
            self._pin(need_key, hr, pick)
            body: dict[str, Any] = {"candidate": pick.ref, "need_id": self.need_ids[need_key]}
            if response_days:
                body["response_days"] = response_days
            try:
                res = self.api.post("/api/invitations", hr, body)
            except ApiError as e:
                self.counters.skipped.append(f"приглашение {entry['status']} ({need_key}): {e.code or e.status} {e.body.get('message') if isinstance(e.body, dict) else ''}")
                return None
        sent = (res.get("sent") or [None])[0]
        if sent is None:
            self.counters.skipped.append(f"приглашение {entry['status']} ({need_key}): отклонено {res.get('rejected')}")
            return None
        self.counters.invitations += 1
        rec = {"id": sent["id"], "candidate_id": pick.candidate_id, "entry": entry, "hr": hr, "terms": sent["salary"]}
        self.invitations.append(rec)
        return rec

    def _cand(self, rec: dict[str, Any]) -> User:
        return self.db.get(User, rec["candidate_id"])

    def _counter_salary(self, rec: dict[str, Any]) -> int:
        to = int(rec["terms"]["to"] if "to" in rec["terms"] else rec["terms"].get("salary_to"))
        step = autoresponder.responder.counter_step()
        x = autoresponder.round_up(to * 1.15, step)
        return min(x, int(autoresponder.responder.counter_max_ratio() * to) // step * step)

    def invitations_up(self) -> None:
        h = section("invitation_hours_ago")
        late, middle, early, step = float(h["late"]), float(h["middle"]), float(h["early"]), float(h["step_hours"])
        for entry in section("invitation_plan"):
            status = entry["status"]
            try:
                getattr(self, f"_inv_{status}")(entry, late=late, middle=middle, early=early, step=step)
            except ApiError as e:
                self.counters.skipped.append(f"приглашение {status}: {e.code or e.status}")
                self.db.rollback()
            self.db.commit()

    def _inv_sent(self, entry: dict[str, Any], *, late: float, **_: Any) -> None:
        self._send(entry, late)

    def _inv_viewed(self, entry: dict[str, Any], *, late: float, step: float, **_: Any) -> None:
        rec = self._send(entry, late + step)
        if rec:
            with self.at(late + step / 2):
                self.api.post(f"/api/invitations/{rec['id']}/view", self._cand(rec))

    def _inv_countered(self, entry: dict[str, Any], *, late: float, step: float, **_: Any) -> None:
        rec = self._send(entry, late + step)
        if rec:
            with self.at(late + step / 2):
                self.api.post(
                    f"/api/invitations/{rec['id']}/counter", self._cand(rec),
                    {"salary_from": self._counter_salary(rec), "comment": str(section("autoresponder")["counter_comment"])},
                )

    def _inv_accepted(self, entry: dict[str, Any], *, middle: float, step: float, **_: Any) -> None:
        rec = self._send(entry, middle)
        if rec:
            with self.at(middle - step):
                self.api.post(f"/api/invitations/{rec['id']}/accept", self._cand(rec), {"consent": True})

    def _inv_accepted_hired(self, entry: dict[str, Any], *, early: float, step: float, **_: Any) -> None:
        rec = self._send(entry, early)
        if not rec:
            return
        cand, hr = self._cand(rec), rec["hr"]
        with self.at(early - step):
            self.api.post(f"/api/invitations/{rec['id']}/accept", cand, {"consent": True})
        with self.at(early - 4 * step):
            self.api.post(f"/api/invitations/{rec['id']}/contacted", hr)
        with self.at(early - 8 * step):
            self.api.post(f"/api/invitations/{rec['id']}/interview", hr)
        with self.at(early - 16 * step):
            self.api.post(f"/api/invitations/{rec['id']}/outcome", hr, {"outcome": "hired"})

    def _inv_declined(self, entry: dict[str, Any], *, middle: float, step: float, **_: Any) -> None:
        rec = self._send(entry, middle)
        if rec:
            with self.at(middle - step):
                self.api.post(f"/api/invitations/{rec['id']}/decline", self._cand(rec), {"reason": entry.get("reason", "other")})

    def _inv_counter_declined(self, entry: dict[str, Any], *, middle: float, step: float, **_: Any) -> None:
        rec = self._send(entry, middle)
        if not rec:
            return
        with self.at(middle - step):
            self.api.post(
                f"/api/invitations/{rec['id']}/counter", self._cand(rec),
                {"salary_from": self._counter_salary(rec), "comment": str(section("autoresponder")["counter_comment"])},
            )
        with self.at(middle - 3 * step):
            self.api.post(f"/api/invitations/{rec['id']}/counter/decline", rec["hr"], {"reason": "salary"})

    def _inv_withdrawn(self, entry: dict[str, Any], *, middle: float, step: float, **_: Any) -> None:
        rec = self._send(entry, middle)
        if rec:
            with self.at(middle - step):
                self.api.post(f"/api/invitations/{rec['id']}/withdraw", rec["hr"], {"reason": "position_closed"})

    def _inv_expired(self, entry: dict[str, Any], *, early: float, **_: Any) -> None:
        self._send(entry, early, response_days=3)

    def _inv_consent_revoked(self, entry: dict[str, Any], *, middle: float, step: float, **_: Any) -> None:
        rec = self._send(entry, middle)
        if not rec:
            return
        cand = self._cand(rec)
        with self.at(middle - step):
            self.api.post(f"/api/invitations/{rec['id']}/accept", cand, {"consent": True})
        with self.at(middle - 6 * step):
            self.api.post(f"/api/invitations/{rec['id']}/revoke-consent", cand)

    # ------------------------------------------------------------------ вакансии и отклики

    def vacancies_up(self) -> None:
        h = section("invitation_hours_ago")
        early = float(h["early"])
        for cfg in section("needs"):
            if not cfg.get("publish_vacancy") or cfg["key"] not in self.need_ids:
                continue
            hr = self.hr(cfg["company"])
            with self.at(early):
                try:
                    res = self.api.post("/api/vacancies", hr, {"need_id": self.need_ids[cfg["key"]]})
                except ApiError as e:
                    self.counters.skipped.append(f"вакансия {cfg['key']}: {e.code or e.status}")
                    continue
            self.vacancy_ids[cfg["key"]] = res["vacancy"]["id"]
            self.counters.vacancies += 1
        self.db.commit()

    def applications_up(self) -> None:
        late, middle, early = (float(section("invitation_hours_ago")[k]) for k in ("late", "middle", "early"))
        for entry in section("applications_plan"):
            key = entry["vacancy"]
            if "who" in entry or key not in self.vacancy_ids:  # «who» — живые персоны сценария: заранее не откликаются
                continue
            pick = self._next_pick(key, self.used_apply | self.used_invite)
            if pick is None:
                self.counters.skipped.append(f"отклик {entry['status']} ({key}): нет свободных кандидатов")
                continue
            self.used_apply.add(pick.candidate_id)
            cand = self.db.get(User, pick.candidate_id)
            hr = self.hr(self.need_company[key])
            status = entry["status"]
            hours = {"submitted": late, "viewed": 60.0, "interested": 100.0, "rejected": 100.0, "withdrawn": 60.0,
                     "no_response": early}[status]
            try:
                with self.at(hours):
                    app = self.api.post("/api/applications", cand, {"vacancy_id": self.vacancy_ids[key], "consent": True})
                aid = app["id"]
                self.counters.applications += 1
                if status in ("viewed", "interested", "rejected"):
                    with self.at(hours - 10):
                        self.api.get(f"/api/applications/{aid}", hr)
                if status == "interested":
                    with self.at(hours - 20):
                        self.api.post(f"/api/applications/{aid}/interest", hr)
                elif status == "rejected":
                    with self.at(hours - 20):
                        self.api.post(f"/api/applications/{aid}/reject", hr, {"reason": entry.get("reason", "other")})
                elif status == "withdrawn":
                    with self.at(hours - 10):
                        self.api.post(f"/api/applications/{aid}/withdraw", cand)
            except ApiError as e:
                self.counters.skipped.append(f"отклик {status} ({key}): {e.code or e.status}")
                self.db.rollback()
            self.db.commit()

    # ------------------------------------------------------------------ задачи работодателей

    def tasks_up(self) -> None:
        for t in section("tasks")["employer"]:
            hr = self.hr(t["company"])
            body: dict[str, Any] = {
                "kind": t["kind"], "specialization": t["specialization"], "grade": t["grade"], "not_real_work_ack": True,
                "title": t["title"], "tags": list(t.get("tags", [])),
            }
            try:
                if t["kind"] == "approach":
                    body["prompt"] = t["prompt"].strip()
                    body["time_limit_min"] = int(t["time_limit_min"])
                else:
                    fams = self.api.get("/api/tasks/catalog", hr, params={"specialization": t["specialization"], "module": t.get("module")})
                    if not fams:
                        self.counters.skipped.append(f"задача «{t['title']}»: в каталоге нет семейств модуля {t.get('module')}")
                        continue
                    body["family_id"] = fams[0]["family_id"]
                self.api.post("/api/tasks", hr, body)
                self.counters.employer_tasks += 1
            except ApiError as e:
                self.counters.skipped.append(f"задача «{t['title']}»: {e.code or e.status}")
                self.db.rollback()
        self.db.commit()

    # ------------------------------------------------------------------ итог

    def settle(self) -> dict[str, Any]:
        """Сроки применяются при чтении; sweep делает это для всех сразу (истёкшие приглашения, «нет ответа» по откликам)."""
        res = sweep(self.db)
        self.db.commit()
        return res

    def run(self) -> Counters:
        self.say("компании и HR-аккаунты")
        self.companies_up()
        self.say("брифы, версии подборки, шортлисты")
        self.needs_up()
        self.say("приглашения во всех статусах")
        self.invitations_up()
        self.say("вакансии и отклики")
        self.vacancies_up()
        self.applications_up()
        self.say("задачи работодателей")
        self.tasks_up()
        self.settle()
        return self.counters


def hr_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role == UserRole.employer.value, User.is_demo.is_(True))))


__all__ = ["Scenario", "accounts"]
