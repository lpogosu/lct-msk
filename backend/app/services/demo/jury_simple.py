"""Простой формат набора жюри: три таблицы (CSV или JSON) → строки импорта формата `eval/pairs_test.jsonl`.

    вакансии   id, text, salary_from, salary_to, salary_net
    кандидаты  id, spec, grade, skills (через «;»), experience_months, self_description
               необязательно: fsp, salary_min, salary_net, formats (через «;»), city
    разметка   vacancy_id, candidate_id, rel (0–3; релевантен ⇔ rel ≥ 2)

Кандидат простого формата теста не проходил: признаки подбора — `features: null`, категория — заявленная (spec × grade).
Такой кандидат попадает в группу «категория по опросу, не подтверждена» (DECISIONS п. 5): он ранжируется ниже
подтверждённых тестом, а заявленные навыки и самоописание в Score не входят — только серые чипы «заявлено» и последний
ключ сортировки при равенстве (DECISIONS п. 1). Отсутствие истории ФСП — не штраф (F = 0).

Ошибки — на русском, с таблицей, номером строки (для CSV — строка файла, заголовок — строка 1; для JSON — номер записи)
и полем: «Вакансии, строка 3, поле «salary_from»: …». Любая ошибка — 400 без частичной записи.
Шаблоны для скачивания лежат рядом: `jury_templates/` (их же проверяет тест).
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import ApiError, bad_request
from app.matching.brief_parser import BriefEditError, parse_brief, to_brief_spec
from app.matching.scoring import GRADES, ScoringConfig
from app.matching.skills import get_catalog, normalize_alias
from app.services.demo.cfg import section

TEMPLATES_DIR = Path(__file__).resolve().parent / "jury_templates"
TEMPLATE_FILES = ("vacancies.csv", "candidates.csv", "labels.csv", "example.json")

TABLES = ("vacancies", "candidates", "labels")
TABLE_RU = {"vacancies": "Вакансии", "candidates": "Кандидаты", "labels": "Разметка"}
REQUIRED = {
    "vacancies": ("id", "text"),
    "candidates": ("id", "spec", "grade"),
    "labels": ("vacancy_id", "candidate_id", "rel"),
}
KNOWN = {
    "vacancies": ("id", "text", "salary_from", "salary_to", "salary_net"),
    "candidates": (
        "id", "spec", "grade", "skills", "experience_months", "self_description", "fsp", "salary_min", "salary_net",
        "formats", "city",
    ),
    "labels": ("vacancy_id", "candidate_id", "rel"),
}
_ID_MAX = 64
_TRUE = {"1", "true", "да", "yes", "y", "net", "на руки", "нетто"}
_FALSE = {"0", "false", "нет", "no", "n", "gross", "до вычета", "до ндфл", "брутто"}
_FORMATS = {"office": "office", "hybrid": "hybrid", "remote": "remote", "офис": "office", "гибрид": "hybrid",
            "удалённо": "remote", "удаленно": "remote", "удалёнка": "remote", "удаленка": "remote"}


@dataclass(frozen=True)
class Record:
    """Строка таблицы: номер строки файла (CSV) или записи (JSON), значения по именам столбцов."""

    line: int
    data: Mapping[str, Any]
    csv: bool


def _where(table: str, rec: Record | None = None, field: str | None = None) -> str:
    out = TABLE_RU[table]
    if rec is not None:
        out += f", {'строка' if rec.csv else 'запись'} {rec.line}"
    if field:
        out += f", поле «{field}»"
    return out


def _error(table: str, rec: Record | None, field: str | None, message: str) -> ApiError:
    details = {"table": table, "line": rec.line if rec else None, "field": field}
    return bad_request("import_invalid", f"{_where(table, rec, field)}: {message}", details)


# --------------------------------------------------------------------------- чтение таблиц


def _delimiter(header: str) -> str:
    """Разделитель по строке заголовка: Excel в русской локали сохраняет CSV через «;», остальные — через «,»."""
    counts = {d: header.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ","


def read_table(table: str, value: Any) -> list[Record]:
    """CSV-текст (с заголовком) или JSON-массив объектов → записи. Пустые строки пропускаются."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.lstrip("﻿")
        if not text.strip():
            return []
        header = text.splitlines()[0]
        reader = csv.reader(io.StringIO(text), delimiter=_delimiter(header))
        rows: list[Record] = []
        names: list[str] | None = None
        prev_end = 0
        for raw in reader:
            start = prev_end + 1
            prev_end = reader.line_num
            if names is None:
                names = [str(h).strip().lower() for h in raw]
                if not any(names):
                    raise _error(table, None, None, "первая строка — заголовок с именами столбцов")
                missing = [k for k in REQUIRED[table] if k not in names]
                if missing:
                    raise _error(table, None, None, "в заголовке нет столбцов: " + ", ".join(missing)
                                 + f" (ожидаются: {', '.join(KNOWN[table])})")
                continue
            if not any(str(c).strip() for c in raw):
                continue
            if len(raw) > len(names):
                raise _error(table, Record(start, {}, True), None,
                             f"значений больше, чем столбцов ({len(raw)} > {len(names)}): возьмите в кавычки поле с разделителем")
            rows.append(Record(start, {n: raw[i] if i < len(raw) else "" for i, n in enumerate(names) if n}, True))
        return rows
    if isinstance(value, list):
        out = []
        for i, item in enumerate(value, 1):
            if not isinstance(item, Mapping):
                raise _error(table, Record(i, {}, False), None, "ожидается объект с полями " + ", ".join(KNOWN[table]))
            out.append(Record(i, {str(k).strip().lower(): v for k, v in item.items()}, False))
        return out
    raise _error(table, None, None, "ожидается CSV-текст или JSON-массив объектов")


# --------------------------------------------------------------------------- значения полей


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _text(table: str, rec: Record, field: str, *, max_len: int | None = None) -> str | None:
    v = rec.data.get(field)
    if _blank(v):
        return None
    s = str(v).strip()
    if max_len is not None and len(s) > max_len:
        raise _error(table, rec, field, f"не длиннее {max_len} символов (сейчас {len(s)})")
    return s


def _req(table: str, rec: Record, field: str, *, max_len: int | None = None) -> str:
    s = _text(table, rec, field, max_len=max_len)
    if s is None:
        raise _error(table, rec, field, "обязательное поле не заполнено")
    return s


def _ident(table: str, rec: Record, field: str) -> str:
    s = _req(table, rec, field)
    if len(s) > _ID_MAX:
        raise _error(table, rec, field, f"идентификатор не длиннее {_ID_MAX} символов")
    return s


def _number(table: str, rec: Record, field: str, *, lo: float | None = None, hi: float | None = None) -> float | None:
    v = rec.data.get(field)
    if _blank(v):
        return None
    if isinstance(v, bool):
        raise _error(table, rec, field, "ожидается число")
    if isinstance(v, int | float):
        x = float(v)
    else:
        s = re.sub(r"[\s  ]", "", str(v)).replace(",", ".")
        try:
            x = float(s)
        except ValueError:
            raise _error(table, rec, field, f"ожидается число, а не «{v}»") from None
    if lo is not None and x < lo or hi is not None and x > hi:
        bounds = f"от {lo:g}" if hi is None else f"от {lo:g} до {hi:g}" if lo is not None else f"до {hi:g}"
        raise _error(table, rec, field, f"число {bounds} (сейчас {x:g})")
    return x


def _int(table: str, rec: Record, field: str, *, lo: int | None = None, hi: int | None = None) -> int | None:
    x = _number(table, rec, field, lo=lo, hi=hi)
    if x is None:
        return None
    if x != int(x):
        raise _error(table, rec, field, f"ожидается целое число (сейчас {x:g})")
    return int(x)


def _bool(table: str, rec: Record, field: str) -> bool | None:
    v = rec.data.get(field)
    if _blank(v):
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise _error(table, rec, field, f"«{v}» — укажите true (на руки) или false (до вычета НДФЛ)")


def _list(v: Any) -> list[str]:
    if _blank(v):
        return []
    if isinstance(v, list | tuple):
        return [str(x).strip() for x in v if not _blank(x)]
    return [p.strip() for p in re.split(r"[;|\n]", str(v)) if p.strip()]


# --------------------------------------------------------------------------- справочники


def _spec_index(cfg: ScoringConfig) -> dict[str, str]:
    out: dict[str, str] = {}
    for code, name in cfg.spec_names.items():
        out[code.lower()] = code
        out[name.lower()] = code
    return out


def _grade_index(cfg: ScoringConfig) -> dict[str, str]:
    out = {g: g for g in GRADES}
    for code, name in cfg.grade_names.items():
        out[name.lower()] = code
        out[name.lower().replace("ё", "е")] = code
    return out


def _skill_id(token: str, min_coverage: float) -> str | None:
    """Навык по id справочника, названию или синониму («PostgreSQL», «постгрес», «Python 3»). Совпадение должно покрывать
    большую часть ячейки: иначе «квантовый-блокчейн» стал бы «Blockchain» — такой навык лучше показать предупреждением."""
    cat = get_catalog()
    t = token.strip()
    if t.lower() in cat.skills:
        return t.lower()
    norm = normalize_alias(t)
    if not norm:
        return None
    best = None
    for m in cat.find_skills(t, fuzzy=False):
        cover = len(normalize_alias(m.quote)) / len(norm)
        if cover >= min_coverage and (best is None or cover > best[0]):
            best = (cover, m.id)
    return best[1] if best else None


def _formats(table: str, rec: Record) -> list[str]:
    out = []
    for f in _list(rec.data.get("formats")):
        code = _FORMATS.get(f.lower())
        if code is None:
            raise _error(table, rec, "formats", f"«{f}» — формат из office, hybrid, remote (офис, гибрид, удалённо)")
        out.append(code)
    return sorted(set(out))


def _city(rec: Record) -> str | None:
    v = _text("candidates", rec, "city")
    if v is None:
        return None
    cat = get_catalog()
    if v.lower() in cat.cities:
        return v.lower()
    found = cat.find_cities(v)
    return found[0].id if found else v


def _fsp(rec: Record, cfg_fsp: Mapping[str, Any], default_discipline: str) -> dict[str, float]:
    """«70» → VSS 70 в дисциплине по умолчанию; «algo:70; product:40» → по дисциплинам. VSS — от 0 до 100."""
    v = rec.data.get("fsp")
    if _blank(v):
        return {}
    disciplines = set(cfg_fsp)
    items: list[tuple[str, Any]]
    if isinstance(v, Mapping):
        items = [(str(k), x) for k, x in v.items()]
    elif isinstance(v, int | float) and not isinstance(v, bool):
        items = [(default_discipline, v)]
    else:
        items = []
        for part in _list(v):
            if ":" in part:
                d, x = part.split(":", 1)
                items.append((d.strip().lower(), x.strip()))
            else:
                items.append((default_discipline, part))
    out: dict[str, float] = {}
    for d, x in items:
        if d not in disciplines:
            raise _error("candidates", rec, "fsp", f"дисциплина «{d}» не из {', '.join(sorted(disciplines))}")
        try:
            val = float(str(x).replace(",", "."))
        except ValueError:
            raise _error("candidates", rec, "fsp", f"VSS «{x}» — число от 0 до 100") from None
        if not 0 <= val <= 100:
            raise _error("candidates", rec, "fsp", f"VSS — число от 0 до 100 (сейчас {val:g})")
        out[d] = val
    return out


# --------------------------------------------------------------------------- адаптер


def _vacancy(rec: Record, cfg: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    t = "vacancies"
    vid = _ident(t, rec, "id")
    text = _req(t, rec, "text", max_len=int(cfg["max_text_chars"]))
    lo, hi = _int(t, rec, "salary_from", lo=1), _int(t, rec, "salary_to", lo=1)
    net = _bool(t, rec, "salary_net")
    if (lo is None) != (hi is None):
        raise _error(t, rec, "salary_from" if lo is None else "salary_to", "укажите обе границы вилки (от и до) или ни одной")
    if lo is not None and hi is not None and lo > hi:
        raise _error(t, rec, "salary_to", f"«до» ({hi}) меньше «от» ({lo})")
    form = {k: v for k, v in (("salary_from", lo), ("salary_to", hi), ("salary_net", net)) if v is not None}
    # Вилка обязательна (концепция §3.2): проверяем сразу, чтобы ошибка пришла с номером строки, а не при ранжировании
    try:
        to_brief_spec(parse_brief(text, **form), vid)
    except BriefEditError as e:
        field = "salary_from" if lo is None else None
        hint = "" if lo is not None else " — заполните salary_from и salary_to (₽ в месяц) или укажите вилку в тексте"
        raise _error(t, rec, field, f"{e}{hint}") from None
    return vid, {"text": text, "form": form or None}


def _candidate(
    rec: Record, cfg: Mapping[str, Any], specs: Mapping[str, str], grades: Mapping[str, str], cfg_fsp: Mapping[str, Any],
    warnings: list[str],
) -> tuple[str, dict[str, Any]]:
    t = "candidates"
    cid = _ident(t, rec, "id")
    spec_raw = _req(t, rec, "spec")
    spec = specs.get(spec_raw.lower())
    if spec is None:
        allowed = ", ".join(sorted({v for v in specs.values()}))
        raise _error(t, rec, "spec", f"«{spec_raw}» — специализация из справочника: {allowed}")
    grade_raw = _req(t, rec, "grade")
    grade = grades.get(grade_raw.lower()) or grades.get(grade_raw.lower().replace("ё", "е"))
    if grade is None:
        raise _error(t, rec, "grade", f"«{grade_raw}» — грейд из {', '.join(GRADES)}")
    skills: list[str] = []
    for token in _list(rec.data.get("skills")):
        sid = _skill_id(token, float(cfg["skill_match_min_coverage"]))
        if sid is None:
            warnings.append(f"{_where(t, rec, 'skills')}: навык «{token}» не найден в справочнике — пропущен")
        elif sid not in skills:
            skills.append(sid)
    exp = _int(t, rec, "experience_months", lo=0, hi=int(cfg["max_experience_months"]))
    about = _text(t, rec, "self_description", max_len=int(cfg["max_self_description_chars"])) or ""
    vss = _fsp(rec, cfg_fsp, str(cfg["fsp_default_discipline"]))
    sal = _int(t, rec, "salary_min", lo=0)
    net = _bool(t, rec, "salary_net")
    body: dict[str, Any] = {
        "features": None,  # теста нет → группа «категория по опросу, не подтверждена»
        "claimed_spec": spec,
        "claimed_grade": grade,
        "declared_skills": skills,
        "experience_months": exp,
        "self_description": about,
        "salary_min": sal,
        "salary_net": True if net is None else net,
        "formats": _formats(t, rec),
        "city": _city(rec),
    }
    if vss:
        body["vss_by_discipline"] = vss
    return cid, body


def adapt(
    vacancies: Any, candidates: Any, labels: Any, *, unlabeled_as_irrelevant: bool | None = None
) -> dict[str, Any]:
    """Три таблицы → {pairs, briefs, candidates, warnings, stats} для jury_admin.store_import.

    unlabeled_as_irrelevant (по умолчанию из config/demo.yaml): у вакансии, где размечен хотя бы один кандидат, все
    остальные кандидаты набора получают rel 0 — тогда метрики считаются по ранжированию всего набора, а не только
    размеченных (иначе разметка одних «релевантных» дала бы P@k = 1 по построению)."""
    cfg = section("admin")["simple"]
    if unlabeled_as_irrelevant is None:
        unlabeled_as_irrelevant = bool(cfg["unlabeled_as_irrelevant"])
    tables = {"vacancies": read_table("vacancies", vacancies), "candidates": read_table("candidates", candidates),
              "labels": read_table("labels", labels)}
    limits = {"vacancies": int(cfg["max_vacancies"]), "candidates": int(cfg["max_candidates"]),
              "labels": int(cfg["max_labels"])}
    for name, rows in tables.items():
        if len(rows) > limits[name]:
            raise bad_request("import_too_large", f"{TABLE_RU[name]}: не больше {limits[name]} строк (сейчас {len(rows)})",
                              {"table": name})
    if not tables["vacancies"] and not tables["candidates"]:
        raise bad_request("import_empty", "Нет ни вакансий, ни кандидатов: загрузите хотя бы одну таблицу по шаблону")
    for name, rows in tables.items():
        for rec in rows:
            unknown = [k for k in rec.data if k and k not in KNOWN[name]]
            if unknown:
                raise _error(name, rec, unknown[0], f"неизвестный столбец (ожидаются: {', '.join(KNOWN[name])})")

    scfg = ScoringConfig.load()
    specs, grades = _spec_index(scfg), _grade_index(scfg)
    cfg_fsp = scfg.fsp.get("spec_relevance", {})
    warnings: list[str] = []

    vac: dict[str, dict[str, Any]] = {}
    vac_line: dict[str, int] = {}
    for rec in tables["vacancies"]:
        vid, body = _vacancy(rec, cfg)
        if vid in vac:
            raise _error("vacancies", rec, "id", f"вакансия «{vid}» уже есть в строке {vac_line[vid]}")
        vac[vid], vac_line[vid] = body, rec.line
    cand: dict[str, dict[str, Any]] = {}
    cand_line: dict[str, int] = {}
    for rec in tables["candidates"]:
        cid, body = _candidate(rec, cfg, specs, grades, cfg_fsp, warnings)
        if cid in cand:
            raise _error("candidates", rec, "id", f"кандидат «{cid}» уже есть в строке {cand_line[cid]}")
        cand[cid], cand_line[cid] = body, rec.line

    rel: dict[tuple[str, str], int] = {}
    rel_line: dict[tuple[str, str], int] = {}
    for rec in tables["labels"]:
        vid = _ident("labels", rec, "vacancy_id")
        cid = _ident("labels", rec, "candidate_id")
        if vid not in vac:
            raise _error("labels", rec, "vacancy_id", f"вакансии «{vid}» нет в таблице вакансий")
        if cid not in cand:
            raise _error("labels", rec, "candidate_id", f"кандидата «{cid}» нет в таблице кандидатов")
        r = _int("labels", rec, "rel", lo=0, hi=3)
        if r is None:
            raise _error("labels", rec, "rel", "обязательное поле не заполнено (0–3)")
        if (vid, cid) in rel:
            raise _error("labels", rec, None, f"пара «{vid} × {cid}» уже размечена в строке {rel_line[(vid, cid)]}")
        rel[(vid, cid)], rel_line[(vid, cid)] = r, rec.line

    labeled = sorted({v for v, _ in rel})
    added = 0
    if unlabeled_as_irrelevant:
        for vid in labeled:
            for cid in cand:
                if (vid, cid) not in rel:
                    rel[(vid, cid)] = 0
                    added += 1
    max_pairs = int(section("admin")["import"]["max_pairs"])
    if len(rel) > max_pairs:
        raise bad_request(
            "import_too_large",
            f"Пар «вакансия × кандидат» получилось {len(rel)}, допустимо {max_pairs}: уменьшите набор или не считайте "
            "неразмеченные пары нерелевантными",
        )
    if added:
        warnings.insert(0, f"Неразмеченные пары у вакансий с разметкой считаются нерелевантными (rel = 0): добавлено {added}")
    pairs = [
        {"brief_id": vid, "brief": vac[vid], "candidate_id": cid, "candidate": cand[cid], "rel": r}
        for (vid, cid), r in sorted(rel.items())
    ]
    return {
        "pairs": pairs,
        "briefs": [{"brief_id": vid, **body} for vid, body in vac.items()],
        "candidates": [{"candidate_id": cid, "candidate": body} for cid, body in cand.items()],
        "warnings": warnings[: int(cfg["max_warnings"])],
        "stats": {
            "vacancies": len(vac), "candidates": len(cand), "labels": len(tables["labels"]), "labeled_vacancies": len(labeled),
            "pairs_added": added,
        },
    }


def template(name: str) -> bytes:
    """Файл шаблона для скачивания (только из списка TEMPLATE_FILES — путь снаружи не принимается)."""
    if name not in TEMPLATE_FILES:
        raise KeyError(name)
    return (TEMPLATES_DIR / name).read_bytes()
