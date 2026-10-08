"""Агрегаты рынка из сырого кэша открытых вакансий → data/market/*.json и config/market.yaml.

Хранятся ТОЛЬКО производные величины: квантили зарплат (направление × грейд), частоты навыков, PMI-соседи,
частоты нераспознанных терминов. Сырые тексты и любые поля вакансий в выходы не попадают.
Запуск: `cd backend && uv run python scripts/market/aggregate.py` (после fetch_trudvsem.py).
Классификация направления — по заголовку правилами ниже, независимо от парсера брифа (чтобы не было круга
«парсер размечает данные для самого себя»).
"""
from __future__ import annotations

import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path

import yaml

from app.core.configs import load_config
from app.matching.skills import get_catalog, normalize_text

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "market" / "_raw" / "vacancies.jsonl"
OUT = ROOT / "data" / "market"
CONFIG_OUT = ROOT / "config" / "market.yaml"

DIRECTION_RULES: list[tuple[str, str]] = [
    ("qa", r"тестиров|\bqa\b|\baqa\b|тестер|sdet|качеств[ау]"),
    ("devops", r"devops|девопс|\bsre\b|системн\w+ администратор|сисадмин|инженер по инфраструктур|администратор (linux|сет)"),
    ("data_ml", r"data scien|дата сайен|machine learn|\bml\b|аналитик данных|data engineer|инженер данных|нейросет|\bbi\b"),
    ("mobile", r"\bios\b|android|андроид|мобильн"),
    ("backend_go", r"\bgo\b|golang|\bго\b"),
    ("backend_java", r"\bjava\b|джава|kotlin|spring"),
    ("backend_python", r"python|питон"),
    ("frontend_js", r"front|фронт|верстальщик|\breact\b|\bvue\b|angular|javascript|typescript|\bjs\b|веб-?дизайн|вёрстк|верстк"),
    ("fullstack", r"full ?stack|фул+ ?стек|node\.?js"),
    ("uiux_design", r"ui/?ux|дизайнер интерфейс|ux-"),
    ("infosec", r"информационной безопасности|безопасност\w+ информации|пентест|кибербез"),
]
IT_TITLE = re.compile(
    r"программист|разработчик|developer|engineer|инженер-?программист|тестиров|администратор|devops|аналитик данных|"
    r"data|верстальщик|python|java|frontend|backend|веб|web|qa", re.I)
INTERN = re.compile(r"стажер|стажёр|intern|trainee", re.I)
JUNIOR = re.compile(r"junior|джун|младший|начинающ", re.I)
SENIOR = re.compile(r"senior|сеньор|старший|ведущий|главный|lead|руководитель", re.I)
MIN_SAL, MAX_SAL = 25_000, 1_000_000  # отсечь опечатки (500 ₽, 5 000 000) и почасовые ставки
MIN_N = 8  # квантили показываем только при n ≥ MIN_N
STOP_TERMS = set("""опыт работы знание знания навыки умение работа высшее образование наличие понимание
уверенный пользователь пк windows office word excel английский язык технический грамотная речь
""".split())


def direction(title: str) -> str | None:
    t = title.lower()
    for code, rx in DIRECTION_RULES:
        if re.search(rx, t):
            return code
    return None


def grade(title: str, exp: float | None) -> str | None:
    if INTERN.search(title):
        return "intern"
    if JUNIOR.search(title):
        return "junior"
    if SENIOR.search(title):
        return "senior"
    try:
        exp = float(exp) if exp not in (None, "") else None
    except (TypeError, ValueError):
        exp = None
    if exp is None:
        return None
    if exp <= 1:
        return "junior"
    if exp < 5:
        return "middle"
    return "senior"


def quantiles(xs: list[float]) -> dict[str, int]:
    xs = sorted(xs)
    qs = statistics.quantiles(xs, n=20, method="inclusive")  # 5%-шаги
    return {"p10": round(qs[1], -2), "p25": round(qs[4], -2), "p50": round(statistics.median(xs), -2),
            "p75": round(qs[14], -2), "p90": round(qs[17], -2)}


def main() -> None:
    rows = [json.loads(line) for line in RAW.read_text(encoding="utf-8").splitlines() if line.strip()]
    it = [r for r in rows if "Информационные технологии" in (r.get("category") or "") or
          (IT_TITLE.search(r.get("job-name") or "") and direction(r.get("job-name") or ""))]
    cat = get_catalog()
    cfg = load_config("scoring")
    ndfl = float(cfg["U"]["ndfl_rate"])

    sal: dict[tuple[str, str], list[float]] = defaultdict(list)
    sal_dir: dict[str, list[float]] = defaultdict(list)
    n_dir: Counter[str] = Counter()
    skill_df: Counter[str] = Counter()
    skill_dir: dict[str, Counter[str]] = defaultdict(Counter)
    pair: Counter[tuple[str, str]] = Counter()
    unmatched: Counter[str] = Counter()
    n_text = 0
    newest = ""
    for r in it:
        title = r.get("job-name") or ""
        d = direction(title)
        text = f"{r.get('requirements') or ''}. {r.get('duty') or ''}"
        newest = max(newest, r.get("creation-date") or "")
        g = grade(title, r.get("experience"))
        if d:
            n_dir[d] += 1
            lo, hi = r.get("salary_min") or 0, r.get("salary_max") or 0
            vals = [v for v in (lo, hi) if MIN_SAL <= v <= MAX_SAL]
            if vals and "руб" in (r.get("currency") or "руб"):
                mid = sum(vals) / len(vals)
                sal_dir[d].append(mid)
                if g:
                    sal[(d, g)].append(mid)
        if len(text) < 60:
            continue
        n_text += 1
        nt = normalize_text(text)
        ms = cat.find_skills(nt, fuzzy=False)
        ids = sorted({m.id for m in ms})
        for s in ids:
            skill_df[s] += 1
            if d:
                skill_dir[d][s] += 1
        for a, b in combinations(ids, 2):
            pair[(a, b)] += 1
        covered = [False] * len(nt.norm)
        for m in ms:
            for i in range(m.nstart, min(m.nend, len(covered))):
                covered[i] = True
        seen = set()
        for mm in re.finditer(r"[a-z][a-z0-9+#./-]{1,}|[а-я][а-я-]{3,}", nt.norm):
            if any(covered[mm.start():mm.end()]):
                continue
            w = mm.group(0).strip(".-/")
            if w and w not in STOP_TERMS and w not in seen:
                seen.add(w)
                unmatched[w] += 1

    # --- квантили зарплат
    table = []
    for (d, g), xs in sorted(sal.items()):
        if len(xs) >= MIN_N:
            table.append({"direction": d, "grade": g, "n": len(xs), **quantiles(xs)})
    by_dir = [{"direction": d, "n": len(xs), **quantiles(xs)} for d, xs in sorted(sal_dir.items()) if len(xs) >= MIN_N]

    # --- PMI-соседи (только пары с поддержкой ≥ 8 вакансий)
    related: dict[str, list[dict]] = defaultdict(list)
    for (a, b), c in pair.items():
        if c < 8:
            continue
        pmi = math.log((c * n_text) / (skill_df[a] * skill_df[b]))
        npmi = pmi / -math.log(c / n_text)
        if npmi > 0.2:
            related[a].append({"skill": b, "n": c, "npmi": round(npmi, 3)})
            related[b].append({"skill": a, "n": c, "npmi": round(npmi, 3)})
    related_top = {k: sorted(v, key=lambda x: -x["npmi"])[:6] for k, v in sorted(related.items())}

    meta = {
        "source": "Единая цифровая платформа «Работа в России», открытые данные (opendata.trudvsem.ru/api/v1/vacancies)",
        "fetched": date.today().isoformat(),
        "newest_vacancy": newest,
        "vacancies_total": len(rows),
        "vacancies_it": len(it),
        "vacancies_with_text": n_text,
        "currency": "RUB",
        "basis": "gross_assumed",
        "basis_note": "Портал не различает «на руки» и «до НДФЛ»; в вакансиях работодателей зарплата по умолчанию "
                      "указывается до вычета НДФЛ, поэтому величины трактуются как gross. net = gross × (1 − НДФЛ).",
        "ndfl_rate": ndfl,
        "bias_note": "Выборка по всей РФ, региональные вакансии занижают уровень относительно Москвы и Санкт-Петербурга.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "salary_quantiles.json").write_text(json.dumps(
        {"meta": meta, "direction_grade": table, "direction": by_dir}, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "skill_counts.json").write_text(json.dumps(
        {"meta": {"vacancies_with_text": n_text}, "df": dict(skill_df.most_common()),
         "by_direction": {d: dict(c.most_common(40)) for d, c in sorted(skill_dir.items())}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "skill_related.json").write_text(json.dumps(
        {"meta": {"method": "npmi по совместной встречаемости в требованиях вакансий, n ≥ 8, npmi > 0,2",
                  "not_used_in_score": True}, "related": related_top}, ensure_ascii=False, indent=1), encoding="utf-8")
    cand = {w: c for w, c in unmatched.most_common(3000) if c >= 2}
    (OUT / "unmatched_terms.json").write_text(json.dumps(
        {"meta": {"note": "документная частота слов, не распознанных словарём навыков"}, "terms": cand},
        ensure_ascii=False, indent=1), encoding="utf-8")
    # --- конфиг рантайма: только квантили (без пар и частот)
    CONFIG_OUT.write_text(
        "# Рыночные ориентиры зарплат — ПРОИЗВОДНЫЕ агрегаты открытых данных (data/market/README.md). Генерируется\n"
        "# backend/scripts/market/aggregate.py; руками не править. В Score не входит, только подсказка и сигнал доверия.\n"
        + yaml.safe_dump({"version": 1, "meta": meta, "min_n": MIN_N, "direction_grade": table, "direction": by_dir},
                         allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(json.dumps({"total": len(rows), "it": len(it), "with_text": n_text, "dirs": dict(n_dir),
                      "salary_cells": len(table)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
