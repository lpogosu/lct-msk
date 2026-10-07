"""Хантфлоу-совместимый адаптер (Huntflow API v2): наши сущности → кандидат (applicant), вакансия, смена этапа.

Режимы:
- эмулятор (токена нет): запросы НЕ уходят в сеть, а записываются в «Журнал ATS» ровно в том виде (метод, URL, заголовки
  без секрета, тело), в каком ушли бы в реальный Хантфлоу; ответы синтетические и детерминированные (id — хеш от
  компании и сущности), поэтому последующие запросы ссылаются на «возвращённые» id так же, как в реальном режиме;
- реальный режим (задан токен и, при необходимости, адрес): те же запросы отправляются через HTTP-транспорт
  (в тестах подменяется set_transport — реальных обращений нет). Нужны только base URL и токен.

Формы запросов — по публичной документации Huntflow API v2 (создание кандидата `POST /accounts/{id}/applicants`,
добавление на вакансию и смена этапа `POST /accounts/{id}/applicants/{applicant_id}/vacancy`, вакансия
`POST /accounts/{id}/vacancies`, этапы `GET /accounts/{id}/vacancies/statuses`). Соответствие полей проверено по
описанию схем, но НЕ против живого сервиса: см. docs/ATS_INTEGRATION.md («что реально, что эмулировано»).

Контакты кандидата попадают в запрос только при действующем раскрытии и только те поля, что кандидат разрешил.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integration import AtsLog, AtsSettings
from app.services.contact import webhooks
from app.services.contact.common import cfg, now

from . import settings as ats_settings

# (метод, url, заголовки, тело JSON | None, таймаут) → (HTTP-код, разобранный ответ)
Transport = Callable[[str, str, Mapping[str, str], Any, float], tuple[int, Any]]


class AdapterError(Exception):
    """Запрос в ATS не удалось построить или выполнить (понятная причина — для журнала)."""


def http_transport(method: str, url: str, headers: Mapping[str, str], body: Any, timeout: float) -> tuple[int, Any]:
    p = urlparse(url)
    webhooks._check_resolved(p.hostname or "", p.port or (443 if p.scheme == "https" else 80))  # SSRF: как у вебхуков
    r = httpx.request(method, url, headers=dict(headers), json=body, timeout=timeout, follow_redirects=False)
    try:
        data: Any = r.json()
    except ValueError:
        data = {"text": r.text[:300]}
    return r.status_code, data


_transport: Transport = http_transport


def set_transport(transport: Transport | None) -> Transport:
    """Подменить отправку в реальном режиме (тесты). None — вернуть HTTP. Возвращает прежнюю."""
    global _transport
    prev = _transport
    _transport = transport or http_transport
    return prev


# --- чистые функции соответствия полей (тестируются без БД) -----------------------------------------------


def split_name(full_name: str | None) -> dict[str, str]:
    """«Фамилия Имя Отчество» (3 слова) или «Имя Фамилия» (2 слова); одно слово — имя."""
    parts = (full_name or "").split()
    if len(parts) >= 3:
        return {"last_name": parts[0], "first_name": parts[1], "middle_name": " ".join(parts[2:])}
    if len(parts) == 2:
        return {"first_name": parts[0], "last_name": parts[1]}
    return {"first_name": parts[0]} if parts else {"first_name": "Кандидат"}


def money_text(salary_from: int | None, salary_to: int | None, net: bool) -> str | None:
    if not salary_from and not salary_to:
        return None
    basis = "на руки" if net else "до вычета НДФЛ"
    return f"{salary_from}-{salary_to} руб. {basis}"


def applicant_payload(contacts: Mapping[str, Any], *, position: str, money: str | None, note: str) -> dict[str, Any]:
    """Тело POST /accounts/{id}/applicants. contacts — только раскрытые поля (full_name, email, phone, telegram)."""
    body: dict[str, Any] = split_name(contacts.get("full_name"))
    if contacts.get("phone"):
        body["phone"] = contacts["phone"]
    if contacts.get("email"):
        body["email"] = contacts["email"]
    if contacts.get("telegram"):
        body["social"] = [{"social_type": "TELEGRAM", "value": str(contacts["telegram"]).lstrip("@")}]
    body["position"] = position
    if money:
        body["money"] = money
    body["externals"] = [{"data": {"body": note}, "auth_type": "NATIVE"}]
    return body


def vacancy_payload(*, title: str, company: str, description: str, money: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"position": title, "company": company, "body": description}
    if money:
        body["money"] = money
    return body


def attach_payload(
    *, vacancy_id: int, status_id: int, comment: str | None = None, rejection_reason: int | None = None, employment_date: str | None = None
) -> dict[str, Any]:
    """Тело POST /accounts/{id}/applicants/{applicant_id}/vacancy: вакансия + этап (+ причина отказа / дата выхода)."""
    body: dict[str, Any] = {"vacancy": vacancy_id, "status": status_id}
    if comment:
        body["comment"] = comment
    if rejection_reason is not None:
        body["rejection_reason"] = rejection_reason
    if employment_date:
        body["employment_date"] = employment_date
        body["fill_quota"] = None
    return body


def fake_id(company_id: uuid.UUID, entity_type: str, entity_id: str) -> int:
    """Детерминированный «id из ATS» в эмуляторе."""
    h = hashlib.sha256(f"{company_id}:{entity_type}:{entity_id}".encode()).digest()
    return 100000 + int.from_bytes(h[:4], "big") % 900000


# --- адаптер ---------------------------------------------------------------------------------------------------


class HuntflowAdapter:
    def __init__(self, db: Session, company_id: uuid.UUID, row: AtsSettings, company_name: str | None = None) -> None:
        self.db = db
        self.company_id = company_id
        self.company_name = company_name or "Компания"
        hf = cfg().ats.huntflow
        self.token = ats_settings.token_of(row)
        self.mode = "real" if self.token else "emulator"
        self.base_url = (row.base_url or hf.default_base_url).rstrip("/")
        self.account_id = row.account_id or str(hf.emulator_account_id)
        self.hf = hf
        self._stages: dict[str, int] | None = None

    # -- журнал и выполнение запроса --

    def _headers(self) -> dict[str, str]:
        auth = "Bearer ***" if self.token else "Bearer <токен не задан: эмулятор>"
        return {"Authorization": auth, "Content-Type": "application/json"}

    def _find_done(self, key: str) -> AtsLog | None:
        return self.db.scalar(
            select(AtsLog).where(AtsLog.company_id == self.company_id, AtsLog.idempotency_key == key, AtsLog.direction == "out")
        )

    def _call(
        self, kind: str, method: str, path: str, body: Any, *, entity_type: str | None = None, entity_id: str | None = None,
        key: str | None = None, emulated: Any = None, log: bool = True,
    ) -> tuple[int, Any]:
        """Один запрос. Повтор с тем же key не создаёт новый (идемпотентность по журналу)."""
        if key is not None:
            done = self._find_done(key)
            if done is not None and done.status in ("recorded", "sent"):
                return done.response_code or 200, done.response
        url = f"{self.base_url}{path}"
        req = {"headers": self._headers(), "body": body}
        if self.mode == "emulator":
            code, data = 200, {"emulated": True, **(emulated if isinstance(emulated, dict) else {})}
            status = "recorded"
        else:
            try:
                code, data = _transport(method, url, {**self._headers(), "Authorization": f"Bearer {self.token}"}, body, float(self.hf.timeout_s))
                status = "sent" if 200 <= code < 300 else "failed"
            except Exception as e:  # noqa: BLE001 — сеть/SSRF-блок: действие пользователя не ломаем, причина в журнале
                code, data, status = 0, {"error": f"{type(e).__name__}: {e}"[:300]}, "failed"
        if log:
            self.db.add(
                AtsLog(
                    company_id=self.company_id, created_at=now(), direction="out", mode=self.mode, kind=kind, method=method, url=url,
                    request=json.loads(json.dumps(req, ensure_ascii=False, default=str)), status=status,
                    response_code=code or None, response=data if isinstance(data, dict) else {"data": data},
                    entity_type=entity_type, entity_id=entity_id,
                    idempotency_key=key if status in ("recorded", "sent") else None,
                )
            )
            self.db.flush()
        if status == "failed":
            raise AdapterError(f"Хантфлоу ответил кодом {code or '—'} на {kind}")
        return code, data

    # -- справочники --

    def stage_id(self, stage: str) -> int:
        """id этапа воронки по нашему коду (new/contacted/interview/offer/hired/rejected)."""
        if self.mode == "emulator":
            return self.hf.emulator_stage_ids[stage]
        if self._stages is None:
            _, data = self._call("statuses.list", "GET", f"/accounts/{self.account_id}/vacancies/statuses", None, log=False)
            names = {str(i.get("name", "")).strip().lower(): int(i["id"]) for i in (data.get("items") or []) if "id" in i}
            resolved: dict[str, int] = {}
            for code, name in self.hf.stage_names.items():
                if name.lower() in names:
                    resolved[code] = names[name.lower()]
            self._stages = resolved
        if stage not in self._stages:
            raise AdapterError(f"В воронке Хантфлоу нет этапа «{self.hf.stage_names.get(stage, stage)}» — создайте его или переименуйте")
        return self._stages[stage]

    def rejection_reason_id(self, reason: str | None) -> int | None:
        if self.mode == "emulator" and reason:
            return self.hf.emulator_rejection_reasons.get(reason, self.hf.emulator_rejection_reasons.get("other"))
        return None  # в реальном режиме причину отказа несёт комментарий: id причин у каждой организации свои

    # -- найденные «внешние id» --

    def external_id(self, kind: str, entity_type: str, entity_id: str) -> int | None:
        row = self.db.scalar(
            select(AtsLog)
            .where(
                AtsLog.company_id == self.company_id, AtsLog.direction == "out", AtsLog.kind == kind,
                AtsLog.entity_type == entity_type, AtsLog.entity_id == entity_id, AtsLog.status.in_(("recorded", "sent")),
            )
            .order_by(AtsLog.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        v = (row.response or {}).get("id")
        return int(v) if v is not None else None

    # -- операции --

    def check(self) -> dict[str, Any]:
        """«Проверить подключение»: GET /accounts (эмулятор — записывает запрос и отвечает сам)."""
        try:
            _, data = self._call(
                "check", "GET", "/accounts", None,
                emulated={"items": [{"id": int(self.account_id), "name": self.company_name}]},
            )
        except AdapterError as e:
            return {"ok": False, "mode": self.mode, "message": str(e)}
        if self.mode == "emulator":
            return {"ok": True, "mode": "emulator", "message": "Эмулятор: запрос записан в «Журнал ATS», в сеть ничего не отправлено. Добавьте токен — запросы пойдут в Хантфлоу."}
        return {"ok": True, "mode": "real", "message": f"Подключение к {self.base_url} работает"}

    def ensure_vacancy(self, key_type: str, key: str, *, title: str, description: str, money: str | None) -> int:
        existing = self.external_id("vacancy.create", key_type, key)
        if existing is not None:
            return existing
        body = vacancy_payload(title=title, company=self.company_name, description=description, money=money)
        _, data = self._call(
            "vacancy.create", "POST", f"/accounts/{self.account_id}/vacancies", body, entity_type=key_type, entity_id=key,
            key=f"hf:vacancy:{key}", emulated={"id": fake_id(self.company_id, key_type, key)},
        )
        return int(data["id"])

    def ensure_applicant(self, entity_type: str, entity_id: str, contacts: Mapping[str, Any], *, position: str, money: str | None) -> int:
        existing = self.external_id("applicant.create", entity_type, entity_id)
        if existing is not None:
            return existing
        body = applicant_payload(contacts, position=position, money=money, note="Источник: платформа «Взаимно» (взаимное согласие, контакты переданы кандидатом)")
        _, data = self._call(
            "applicant.create", "POST", f"/accounts/{self.account_id}/applicants", body, entity_type=entity_type, entity_id=entity_id,
            key=f"hf:applicant:{entity_type}:{entity_id}", emulated={"id": fake_id(self.company_id, "applicant", entity_id)},
        )
        return int(data["id"])

    def move(
        self, entity_type: str, entity_id: str, *, applicant_id: int, vacancy_id: int, stage: str, event_key: str,
        comment: str | None = None, reason: str | None = None, employment_date: str | None = None,
    ) -> None:
        body = attach_payload(
            vacancy_id=vacancy_id, status_id=self.stage_id(stage), comment=comment,
            rejection_reason=self.rejection_reason_id(reason) if stage == "rejected" else None, employment_date=employment_date,
        )
        self._call(
            f"applicant.stage.{stage}", "POST", f"/accounts/{self.account_id}/applicants/{applicant_id}/vacancy", body,
            entity_type=entity_type, entity_id=entity_id, key=f"hf:move:{event_key}",
            emulated={"id": fake_id(self.company_id, "log", event_key), "applicant": applicant_id, "vacancy": vacancy_id},
        )


def adapter_for(db: Session, company_id: uuid.UUID, company_name: str | None = None) -> HuntflowAdapter | None:
    row = ats_settings.get(db, company_id)
    if row is None or row.ats_type != "huntflow":
        return None
    return HuntflowAdapter(db, company_id, row, company_name)


def today_iso(at: datetime | None = None) -> str:
    return (at or now()).date().isoformat()
