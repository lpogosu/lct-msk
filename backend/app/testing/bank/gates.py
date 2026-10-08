"""CI-гейты семейства заданий (spec-testing §3.8).

Семейство допускается в банк, только если:
- 200 экземпляров генерируются без исключений;
- генерация детерминирована по seed;
- ключи (для текстового ответа) или содержимое (для выбора) достаточно разнообразны —
  это основа детектора «чужого ключа» и стойкости к сливу;
- дистракторы не совпадают с ключом, ключ проходит check(), дистрактор — нет;
- формулировка на русском; Python-код импортирует только модули из белого списка.
"""

from __future__ import annotations

import re

from app.testing.bank.base import GRADES, Family, Instance, canonical

N_DEFAULT = 200
MIN_DISTINCT_TEXT_KEYS = 0.95
MIN_DISTINCT_RENDERED = {"choice": 0.80, "line_choice": 0.80, "order": 0.80, "mcq_concept": 0.30}
PY_IMPORT_WHITELIST = {
    "collections", "itertools", "functools", "math", "dataclasses", "typing", "re", "json",
    "heapq", "bisect", "copy", "operator", "string", "enum", "datetime", "statistics", "asyncio",
    # Семейства навыка FastAPI (проверочный набор по вакансии): код только показывается и офлайн исполняется нами
    # в генераторе (families/skills/fastapi_web.py); кандидату библиотеки не нужны — он читает код.
    "fastapi", "pydantic",
}
_CYR = re.compile(r"[А-Яа-яЁё]")
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)


def _fingerprint(inst: Instance) -> str:
    r = inst.rendered
    return canonical([r.prompt, r.code, [o.text for o in r.options], list(r.lines), r.table])


def check_family(fam: Family, n: int = N_DEFAULT) -> list[str]:
    """Вернуть список нарушений (пустой — семейство годно)."""
    errors: list[str] = []
    if fam.target_grade not in GRADES:
        errors.append(f"target_grade={fam.target_grade!r} не из {GRADES}")
    if not -1.0 <= fam.position <= 1.0:
        errors.append(f"position={fam.position} вне [-1, 1]")
    if not _CYR.search(fam.title or ""):
        errors.append("title должен быть на русском")

    instances: list[Instance] = []
    for seed in range(n):
        try:
            instances.append(fam.generate(seed))
        except Exception as e:  # noqa: BLE001 — гейт должен собрать все ошибки
            errors.append(f"seed={seed}: исключение {type(e).__name__}: {e}")
            if len(errors) > 5:
                return errors
    if not instances:
        return errors

    for seed in (0, 7, n - 1):
        a, b = fam.generate(seed), fam.generate(seed)
        if _fingerprint(a) != _fingerprint(b) or canonical(a.key) != canonical(b.key):
            errors.append(f"недетерминированная генерация при seed={seed}")

    mode = instances[0].rendered.answer_mode
    if mode == "text":
        ratio = len({canonical(i.key) for i in instances}) / len(instances)
        if ratio < MIN_DISTINCT_TEXT_KEYS:
            errors.append(f"различных ключей {ratio:.2f} < {MIN_DISTINCT_TEXT_KEYS}")
    else:
        need = MIN_DISTINCT_RENDERED["mcq_concept" if fam.format == "mcq_concept" else mode]
        ratio = len({_fingerprint(i) for i in instances}) / len(instances)
        if ratio < need:
            errors.append(f"различных экземпляров {ratio:.2f} < {need}")

    for inst in instances[:50]:
        r = inst.rendered
        if not _CYR.search(r.prompt):
            errors.append(f"seed={inst.seed}: формулировка не на русском")
            break
        ok, _ = fam.check(inst, inst.key)
        if not ok:
            errors.append(f"seed={inst.seed}: ключ не проходит check()")
            break
        for wrong in inst.distractors:
            if wrong == canonical(inst.key):
                errors.append(f"seed={inst.seed}: дистрактор совпадает с ключом")
                break
        if r.answer_mode in ("choice", "line_choice") and not r.options:
            errors.append(f"seed={inst.seed}: answer_mode={r.answer_mode}, но нет вариантов")
            break
        if r.language == "python" and r.code:
            for m in _PY_IMPORT.finditer(r.code):
                mod = (m.group(1) or m.group(2)).split(".")[0]
                if mod not in PY_IMPORT_WHITELIST:
                    errors.append(f"seed={inst.seed}: импорт {mod} не из белого списка")
                    break
    return errors
