"""Сбор открытых вакансий IT с портала «Работа в России» (opendata.trudvsem.ru) в локальный сырой кэш.

Назначение — только наполнение тестовых/справочных данных (агрегаты в data/market/). Не механика продукта:
сырые тексты в git не попадают (data/market/_raw/ в .gitignore) и после агрегации удаляются.
ПДн (контакты, ФИО, названия компаний) на диск не пишутся — отбрасываются сразу при сохранении.
Вежливость: 1 запрос/с, User-Agent с названием проекта.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://opendata.trudvsem.ru/api/v1/vacancies"
UA = "Vzaimno-hackathon-research/1.0 (LCT-2026 FSP track; open-data aggregates only)"
RAW = Path(__file__).resolve().parents[3] / "data" / "market" / "_raw"
QUERIES = [
    "python", "django", "fastapi", "backend разработчик", "бэкенд", "программист python",
    "javascript", "typescript", "react", "vue", "angular", "frontend", "фронтенд", "верстальщик", "node.js",
    "qa", "тестировщик", "инженер по тестированию", "автотестирование", "qa automation",
    "devops", "девопс", "системный администратор", "kubernetes", "docker", "sre",
    "java", "golang", "php", "c#", "1с программист", "аналитик данных", "data scientist", "sql", "разработчик",
    "программист", "инженер-программист", "web разработчик", "fullstack",
]
KEEP = ("id", "job-name", "salary_min", "salary_max", "currency", "requirements", "duty", "schedule",
        "qualification", "typicalPosition", "skills", "creation-date")


def get(q: str, offset: int, limit: int = 100) -> dict:
    url = BASE + "?" + urllib.parse.urlencode({"text": q, "limit": limit, "offset": offset})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            return json.load(urllib.request.urlopen(req, timeout=60))
        except Exception as e:  # noqa: BLE001
            print("retry", q, offset, e, file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    return {}


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    seen: dict[str, dict] = {}
    for q in QUERIES:
        for off in range(0, 10):
            d = get(q, off)
            vs = (d.get("results") or {}).get("vacancies") or []
            if not vs:
                break
            for w in vs:
                v = w["vacancy"]
                cat = (v.get("category") or {}).get("specialisation", "")
                rec = {k: v.get(k) for k in KEEP}
                rec["experience"] = (v.get("requirement") or {}).get("experience")
                rec["category"] = cat
                seen.setdefault(v["id"], rec)
            time.sleep(1.0)
            if len(vs) < 100:
                break
        print(q, len(seen), flush=True)
    (RAW / "vacancies.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in seen.values()), encoding="utf-8")
    print("total", len(seen))


if __name__ == "__main__":
    main()
