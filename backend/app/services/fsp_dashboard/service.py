"""Агрегаты для панели ФСП «Кадровый резерв» (GET /api/fsp/dashboard).

Принципы (docs/FSP_INTEGRATION.md, «Панель ФСП: кадровый резерв»; DECISIONS п. 31.4):
- наружу уходят только счётчики и медианы; id, имена, контакты, названия компаний в ответ не попадают;
- любая ячейка с людьми меньше k_min скрыта (kanon.py); спрос считается в компаниях, порог k_min_companies;
- стадии воронки вложены (каждая следующая — подмножество предыдущей), поэтому числа не растут слева направо;
- отсутствие истории ФСП — не штраф: сравнение «ФСП / без ФСП» — описательное, в Score ничего не меняется.
Все пороги и справочники — в config/fsp_dashboard.yaml.
"""

from __future__ import annotations

import csv
import io
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.models.candidate import CandidateProfile
from app.models.contact import Invitation, OfferTerms, Vacancy
from app.models.enums import (
    NeedStatus,
    QualificationStatus,
    SearchMode,
    SessionStatus,
    UserRole,
    UserStatus,
    VacancyStatus,
)
from app.models.fsp import FspAchievementCache, FspLink
from app.models.matching import ImpressionLog, Need
from app.models.testing import Qualification, TestSession
from app.models.users import User
from app.services.demo.models import DemoPersona
from app.services.fsp_dashboard.kanon import Cell, fold_small_rows, mask, mask_partition
from app.services.profile.regions import REGIONS
from app.services.testing import labels

SYNTHETIC_NOTE = (
    "Демо-данные синтетические: участники, тесты, приглашения и найм сгенерированы для демонстрации; реестр ФСП — мок. "
    "Числа показывают, как панель работает, а не состояние реального кадрового резерва."
)
TESTED_STATUSES = (SessionStatus.decided.value, SessionStatus.final.value, SessionStatus.extension_offered.value)
GRADE_ORDER = ("intern", "junior", "middle", "senior")
REGION_STAGES = ("linked", "confirmed", "invited", "mutual", "hired")


def cfg() -> dict[str, Any]:
    return load_config("fsp_dashboard")


@dataclass
class Cand:
    """Кандидат только внутри расчёта; наружу не уходит."""

    id: uuid.UUID
    fsp: bool
    region: str | None
    searchable: bool
    linked_at: datetime | None
    disciplines: set[str] = field(default_factory=set)
    tested: bool = False
    quals: set[tuple[str, str]] = field(default_factory=set)
    shown: bool = False
    invited: bool = False
    mutual: bool = False
    hired: bool = False


@dataclass
class Inv:
    cand: uuid.UUID
    company: uuid.UUID
    created_at: datetime
    accepted_at: datetime | None
    hired: bool
    closed_at: datetime | None
    updated_at: datetime
    grade: str | None
    offer_mid_net: float | None


def _district_of() -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for d in cfg()["districts"]:
        for code in d["regions"]:
            out[str(code)] = (d["code"], d["name"])
    return out


# --------------------------------------------------------------------------- выборки


def _load_candidates(db: Session) -> dict[uuid.UUID, Cand]:
    guests = select(DemoPersona.user_id).where(DemoPersona.kind == "staged", DemoPersona.key == "jury_guest")
    rows = db.execute(
        select(User.id, CandidateProfile.region_code, CandidateProfile.search_mode, FspLink.id, FspLink.linked_at)
        .select_from(User)
        .outerjoin(CandidateProfile, CandidateProfile.user_id == User.id)
        .outerjoin(FspLink, FspLink.user_id == User.id)
        .where(User.role == UserRole.candidate.value, User.status == UserStatus.active.value, User.id.not_in(guests))
    ).all()
    cands = {
        uid: Cand(id=uid, fsp=link_id is not None, region=region, searchable=mode != SearchMode.hidden.value, linked_at=linked_at)
        for uid, region, mode, link_id, linked_at in rows
    }
    for uid, disc in db.execute(
        select(FspAchievementCache.user_id, FspAchievementCache.discipline)
        .where(FspAchievementCache.kind == "result", FspAchievementCache.hidden.is_(False), FspAchievementCache.discipline.is_not(None))
        .distinct()
    ):
        if uid in cands and disc:
            cands[uid].disciplines.add(disc)
    for (uid,) in db.execute(
        select(TestSession.candidate_id).where(TestSession.status.in_(TESTED_STATUSES), TestSession.demo_assisted.is_(False)).distinct()
    ):
        if uid in cands:
            cands[uid].tested = True
    for uid, spec, grade in db.execute(
        select(Qualification.candidate_id, Qualification.specialization, Qualification.grade).where(
            Qualification.status == QualificationStatus.confirmed.value, Qualification.grade.is_not(None)
        )
    ):
        if uid in cands:
            cands[uid].quals.add((spec, grade))
    for (uid,) in db.execute(select(ImpressionLog.candidate_id).distinct()):
        if uid in cands:
            cands[uid].shown = True
    return cands


def _load_invitations(db: Session, cands: dict[uuid.UUID, Cand]) -> list[Inv]:
    net_k = 1.0 - float(cfg()["salary"]["ndfl_rate"])
    need_grade = dict(db.execute(select(Need.id, Need.grade)).all())
    vac_grade = dict(db.execute(select(Vacancy.id, Vacancy.grade)).all())
    first_offer = {
        iid: (lo, hi, net)
        for iid, lo, hi, net in db.execute(
            select(OfferTerms.invitation_id, OfferTerms.salary_from, OfferTerms.salary_to, OfferTerms.salary_net).where(OfferTerms.ver == 1)
        )
    }
    out: list[Inv] = []
    for r in db.execute(
        select(
            Invitation.id, Invitation.candidate_id, Invitation.company_id, Invitation.created_at, Invitation.accepted_at,
            Invitation.outcome, Invitation.closed_at, Invitation.updated_at, Invitation.need_id, Invitation.vacancy_id,
        )
    ):
        if r.candidate_id not in cands:
            continue
        offer = first_offer.get(r.id)
        mid = None
        if offer is not None:
            mid = (offer[0] + offer[1]) / 2
            if not offer[2]:  # вилка «до НДФЛ» → приводим к «на руки»
                mid *= net_k
        out.append(
            Inv(
                cand=r.candidate_id, company=r.company_id, created_at=r.created_at, accepted_at=r.accepted_at,
                hired=r.outcome == "hired", closed_at=r.closed_at, updated_at=r.updated_at,
                grade=need_grade.get(r.need_id) or vac_grade.get(r.vacancy_id), offer_mid_net=mid,
            )
        )
    for inv in out:
        c = cands[inv.cand]
        c.invited = True
        c.mutual = c.mutual or inv.accepted_at is not None
        c.hired = c.hired or inv.hired
    return out


# --------------------------------------------------------------------------- воронка


def _stage_counts(group: list[Cand]) -> dict[str, int]:
    """Вложенные стадии. «Попал в подборки» — показан в подборке ИЛИ получил приглашение (приглашение можно
    отправить и из банка, минуя подборку, — такой кандидат не должен выпасть из воронки)."""
    conds = {
        "tested": lambda c: c.tested,
        "confirmed": lambda c: bool(c.quals),
        "shortlisted": lambda c: c.shown or c.invited,
        "invited": lambda c: c.invited,
        "mutual": lambda c: c.mutual,
        "hired": lambda c: c.hired,
    }
    cur = list(group)
    out = {"linked": len(cur)}
    for key, cond in conds.items():
        cur = [c for c in cur if cond(c)]
        out[key] = len(cur)
    return out


def _rate(n: int, prev: int | None, cell: Cell, k: int) -> float | None:
    """Доля от предыдущей стадии, %. Скрытая ячейка и скрытый знаменатель долю не показывают."""
    if prev is None or prev < k or cell.suppressed:
        return None
    return round(100.0 * n / prev, 1)


def _funnel(fsp: list[Cand], other: list[Cand], k: int) -> list[dict[str, Any]]:
    a, b = _stage_counts(fsp), _stage_counts(other)
    out: list[dict[str, Any]] = []
    prev_a: int | None = None
    prev_b: int | None = None
    for st in cfg()["funnel"]["stages"]:
        key = st["key"]
        ca, cb = mask(a[key], k), mask(b[key], k)
        out.append(
            {
                "key": key, "label": st["label"], "fsp": ca, "other": cb,
                "fsp_rate": _rate(a[key], prev_a, ca, k), "other_rate": _rate(b[key], prev_b, cb, k),
            }
        )
        prev_a, prev_b = a[key], b[key]
    return out


def _stage_cells(group: list[Cand], k: int) -> dict[str, Cell]:
    return {key: mask(n, k) for key, n in _stage_counts(group).items()}


# --------------------------------------------------------------------------- разрезы


def _by_discipline(fsp: list[Cand], k: int) -> list[dict[str, Any]]:
    short_labels = {d["short"]: d["label"] for d in load_config("fsp")["disciplines"]}
    rows = []
    for short in cfg()["disciplines_order"]:
        group = [c for c in fsp if short in c.disciplines]
        rows.append({"key": short, "label": short_labels.get(short, short), "stages": _stage_cells(group, k)})
    return rows


def _by_district(fsp: list[Cand], k: int, complementary: bool) -> list[dict[str, Any]]:
    dist = _district_of()
    unknown = cfg()["unknown_district"]
    names = {d["code"]: d["name"] for d in cfg()["districts"]} | {unknown["code"]: unknown["name"]}
    groups: dict[str, list[Cand]] = defaultdict(list)
    for c in fsp:
        groups[dist.get(c.region or "", (unknown["code"], unknown["name"]))[0]].append(c)
    codes = [d["code"] for d in cfg()["districts"]] + [unknown["code"]]
    stages = [s["key"] for s in cfg()["funnel"]["stages"]]
    counts = {code: _stage_counts(groups.get(code, [])) for code in codes}
    # вторичное подавление — отдельно по каждой стадии: строки округов взаимоисключающие
    per_stage = {st: mask_partition({code: counts[code][st] for code in codes}, k, complementary=complementary) for st in stages}
    rows = [
        {"key": code, "label": names[code], "stages": {st: per_stage[st][code] for st in stages}}
        for code in sorted(codes, key=lambda c: -counts[c]["linked"])
        if groups.get(code)
    ]
    return rows


def _by_region(fsp: list[Cand], k: int) -> dict[str, Any]:
    by_region: dict[str, list[Cand]] = defaultdict(list)
    for c in fsp:
        by_region[c.region or ""].append(c)
    sizes = {code: len(g) for code, g in by_region.items() if code}
    shown, _ = fold_small_rows(sizes, k)

    def row(key: str, label: str, group: list[Cand]) -> dict[str, Any]:
        cells = _stage_cells(group, k)
        return {"key": key, "label": label, "stages": {s: cells[s] for s in REGION_STAGES}}

    rows = [row(code, REGIONS.get(code, code), by_region[code]) for code in sorted(shown, key=lambda c: -shown[c])]
    rest = [c for code, g in by_region.items() if code not in shown for c in g]
    return {
        "rows": rows,
        "other": row("other", "Прочие регионы и регион не указан", rest) if rest else None,
        "regions_total": len(sizes),
    }


def _spec_grade(fsp: list[Cand], k: int) -> dict[str, Any]:
    cnt: dict[tuple[str, str], int] = defaultdict(int)
    for c in fsp:
        for q in c.quals:
            cnt[q] += 1
    specs = sorted({s for s, _ in cnt}, key=labels.spec_name)
    grades = [g for g in GRADE_ORDER if any(gg == g for _, gg in cnt)]
    return {
        "grades": [{"key": g, "label": labels.grade_name(g)} for g in grades],
        "rows": [
            {"key": s, "label": labels.spec_name(s), "cells": {g: mask(cnt.get((s, g), 0), k) for g in grades}} for s in specs
        ],
    }


# --------------------------------------------------------------------------- время, компании, зарплаты, спрос


def _week_start(dt: datetime) -> datetime:
    return (dt - timedelta(days=dt.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _series(fsp: list[Cand], invs: list[Inv], cands: dict[uuid.UUID, Cand], now: datetime, weeks: int, k: int) -> dict[str, Any]:
    start = _week_start(now) - timedelta(weeks=weeks - 1)

    def bucket(dt: datetime | None) -> int | None:
        if dt is None:
            return None
        if dt.tzinfo is None and now.tzinfo is not None:
            dt = dt.replace(tzinfo=now.tzinfo)
        i = (_week_start(dt.astimezone(now.tzinfo)) - start).days // 7
        return i if 0 <= i < weeks else None

    series: dict[str, list[int]] = {key: [0] * weeks for key in ("linked", "invited", "mutual", "hired", "invited_all")}
    for c in fsp:
        i = bucket(c.linked_at)
        if i is not None:
            series["linked"][i] += 1
    for inv in invs:
        i = bucket(inv.created_at)
        if i is not None:
            series["invited_all"][i] += 1
        if not cands[inv.cand].fsp:
            continue
        hired_at = (inv.closed_at or inv.updated_at) if inv.hired else None
        for key, dt in (("invited", inv.created_at), ("mutual", inv.accepted_at), ("hired", hired_at)):
            j = bucket(dt)
            if j is not None:
                series[key][j] += 1
    return {
        "weeks": [(start + timedelta(weeks=i)).date().isoformat() for i in range(weeks)],
        "series": {key: [mask(n, k) for n in vals] for key, vals in series.items()},
    }


def _companies(invs: list[Inv], cands: dict[uuid.UUID, Cand], k: int) -> dict[str, Cell]:
    mine = [i for i in invs if cands[i.cand].fsp]
    return {
        "invited": mask(len({i.company for i in mine}), k),
        "mutual": mask(len({i.company for i in mine if i.accepted_at is not None}), k),
        "hired": mask(len({i.company for i in mine if i.hired}), k),
    }


def _salary(invs: list[Inv], cands: dict[uuid.UUID, Cand], k: int) -> dict[str, Any]:
    ndfl = float(cfg()["salary"]["ndfl_rate"])
    gross_k = 1.0 / (1.0 - ndfl)
    rows = []
    for grade in GRADE_ORDER:
        row: dict[str, Any] = {"key": grade, "label": labels.grade_name(grade)}
        seen = False
        for cohort, is_fsp in (("fsp", True), ("other", False)):
            sel = [i for i in invs if i.grade == grade and i.offer_mid_net is not None and cands[i.cand].fsp == is_fsp]
            people = len({i.cand for i in sel})
            cell = mask(people, k)
            seen = seen or people > 0
            med = None
            if people >= k:
                med = round(statistics.median(i.offer_mid_net for i in sel if i.offer_mid_net is not None), -3)
            row[cohort] = {
                "n": cell,
                "median_net": int(med) if med is not None else None,
                "median_gross": int(round(med * gross_k, -3)) if med is not None else None,
            }
        if seen:
            rows.append(row)
    return {
        "rows": rows,
        "ndfl_rate": ndfl,
        "basis": (
            "Медиана середины вилки из первой оферты в приглашении, руб./мес. Основная база — «на руки»; «до НДФЛ» пересчитана "
            f"по ставке {round(ndfl * 100)} %. Вилки, указанные «до НДФЛ», приведены к «на руки» тем же коэффициентом."
        ),
    }


def _demand_supply(
    db: Session, cands: dict[uuid.UUID, Cand], k: int, k_companies: int, since: datetime
) -> list[dict[str, Any]]:
    d = cfg()["demand"]
    demand: dict[tuple[str, str], set[uuid.UUID]] = defaultdict(set)
    for cid, spec, grade in db.execute(
        select(Need.company_id, Need.specialization, Need.grade).where(
            Need.status.in_((NeedStatus.active.value, NeedStatus.paused.value, NeedStatus.closed.value)),
            Need.created_at >= since,
            Need.specialization.is_not(None),
            Need.grade.is_not(None),
        )
    ):
        demand[(spec, grade)].add(cid)
    for cid, spec, grade in db.execute(
        select(Vacancy.company_id, Vacancy.specialization, Vacancy.grade).where(
            Vacancy.status == VacancyStatus.published.value, Vacancy.specialization.is_not(None), Vacancy.grade.is_not(None)
        )
    ):
        demand[(spec, grade)].add(cid)
    supply_fsp: dict[tuple[str, str], int] = defaultdict(int)
    supply_all: dict[tuple[str, str], int] = defaultdict(int)
    for c in cands.values():
        if not c.searchable:  # «не ищу» — не предложение на рынке
            continue
        for q in c.quals:
            supply_all[q] += 1
            if c.fsp:
                supply_fsp[q] += 1
    order = sorted(
        set(demand) | set(supply_fsp),
        key=lambda t: (labels.spec_name(t[0]), GRADE_ORDER.index(t[1]) if t[1] in GRADE_ORDER else 9),
    )
    rows = []
    for spec, grade in order:
        comp = len(demand.get((spec, grade), ()))
        sup = supply_fsp.get((spec, grade), 0)
        comp_cell = Cell(n=None, text=f"<{k_companies}", suppressed=True) if 0 < comp < k_companies else Cell(n=comp, text=str(comp))
        sup_cell = mask(sup, k)
        if comp == 0:
            status = "no_demand"
        elif comp_cell.suppressed:
            status = "few_data"
        else:
            upper = (k - 1) if sup_cell.suppressed else sup  # скрытое число оцениваем сверху, не раскрывая его
            ratio = upper / comp
            if ratio < float(d["athletes_per_company_ok"]):
                status = "deficit"
            elif sup_cell.suppressed:
                status = "few_data"
            else:
                status = "surplus" if ratio > float(d["athletes_per_company_surplus"]) else "balance"
        rows.append(
            {
                "spec": spec, "spec_label": labels.spec_name(spec), "grade": grade, "grade_label": labels.grade_name(grade),
                "demand_companies": comp_cell, "supply_fsp": sup_cell, "supply_all": mask(supply_all.get((spec, grade), 0), k),
                "status": status,
            }
        )
    return rows


# --------------------------------------------------------------------------- сборка


def build_dashboard(db: Session, *, weeks: int | None = None) -> dict[str, Any]:
    c = cfg()
    k = int(c["privacy"]["k_min"])
    kc = int(c["privacy"]["k_min_companies"])
    now = clock.now()
    n_weeks = int(weeks or c["series"]["weeks"])
    cands = _load_candidates(db)
    invs = _load_invitations(db, cands)
    fsp = [x for x in cands.values() if x.fsp]
    other = [x for x in cands.values() if not x.fsp]
    synthetic = bool(
        db.scalar(select(FspLink.id).where((FspLink.is_demo.is_(True)) | (FspLink.data_source == "fsp_mock")).limit(1))
    )
    return {
        "as_of": now.isoformat(),
        "k_min": k,
        "synthetic": synthetic,
        "note": SYNTHETIC_NOTE if synthetic else None,
        "funnel": _funnel(fsp, other, k),
        "by_discipline": _by_discipline(fsp, k),
        "by_district": _by_district(fsp, k, bool(c["privacy"]["complementary_suppression"])),
        "by_region": _by_region(fsp, k),
        "spec_grade": _spec_grade(fsp, k),
        "timeline": _series(fsp, invs, cands, now, n_weeks, k),
        "companies": _companies(invs, cands, k),
        "salary": _salary(invs, cands, k),
        "demand_supply": _demand_supply(db, cands, k, kc, now - timedelta(weeks=n_weeks)),
        "privacy_note": (
            f"Ячейки, где меньше {k} человек, скрыты («<{k}»); спрос работодателей скрыт, пока в категории меньше {kc} компаний. "
            "В панели нет имён, контактов, идентификаторов и названий компаний."
        ),
    }


# --------------------------------------------------------------------------- CSV


def _text(v: Any) -> str:
    if isinstance(v, Cell):
        return v.text
    return "" if v is None else str(v)


def to_csv(data: dict[str, Any]) -> str:
    """Плоская выгрузка «раздел;строка;показатель;значение»: скрытые ячейки остаются «<k». Разделитель «;», UTF-8 с BOM
    (Excel открывает без «кракозябр»)."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["раздел", "строка", "показатель", "значение"])
    stage_label = {s["key"]: s["label"] for s in cfg()["funnel"]["stages"]}
    for s in data["funnel"]:
        w.writerow(["воронка", s["label"], "участники ФСП", _text(s["fsp"])])
        w.writerow(["воронка", s["label"], "остальные кандидаты", _text(s["other"])])
    groups = [("дисциплина", data["by_discipline"]), ("федеральный округ", data["by_district"]), ("регион", data["by_region"]["rows"])]
    if data["by_region"]["other"]:
        groups.append(("регион", [data["by_region"]["other"]]))
    for section, rows in groups:
        for r in rows:
            for key, cell in r["stages"].items():
                w.writerow([section, r["label"], stage_label.get(key, key), _text(cell)])
    for r in data["spec_grade"]["rows"]:
        for g in data["spec_grade"]["grades"]:
            w.writerow(["специализация × грейд", r["label"], g["label"], _text(r["cells"][g["key"]])])
    tl = data["timeline"]
    series_name = {
        "linked": "привязали ФСП ID", "invited": "приглашения спортсменам ФСП", "mutual": "«Взаимно»",
        "hired": "наняты", "invited_all": "приглашения всем кандидатам",
    }
    for key, cells in tl["series"].items():
        for week, cell in zip(tl["weeks"], cells, strict=True):
            w.writerow([f"неделя с {week}", series_name[key], "", _text(cell)])
    company_name = {"invited": "компании, пригласившие спортсменов ФСП", "mutual": "компании со «Взаимно»", "hired": "компании, нанявшие спортсменов ФСП"}
    for key, name in company_name.items():
        w.writerow(["компании", name, "", _text(data["companies"][key])])
    for r in data["salary"]["rows"]:
        for cohort, name in (("fsp", "ФСП"), ("other", "остальные")):
            x = r[cohort]
            w.writerow(["медиана оферты", r["label"], f"{name}: кандидатов", _text(x["n"])])
            w.writerow(["медиана оферты", r["label"], f"{name}: на руки, руб.", _text(x["median_net"])])
            w.writerow(["медиана оферты", r["label"], f"{name}: до НДФЛ, руб.", _text(x["median_gross"])])
    status = {"deficit": "не хватает", "balance": "баланс", "surplus": "избыток", "few_data": "мало данных", "no_demand": "нет спроса"}
    for r in data["demand_supply"]:
        cat = f"{r['spec_label']} · {r['grade_label']}"
        w.writerow(["спрос и предложение", cat, "компаний со спросом", _text(r["demand_companies"])])
        w.writerow(["спрос и предложение", cat, "спортсменов ФСП с категорией", _text(r["supply_fsp"])])
        w.writerow(["спрос и предложение", cat, "оценка", status[r["status"]]])
    w.writerow(["примечание", data["privacy_note"], "", ""])
    if data.get("note"):
        w.writerow(["примечание", data["note"], "", ""])
    return "﻿" + buf.getvalue()
