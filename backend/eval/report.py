"""Плитки с порогами (для «Лаборатории»), SVG-графики и генерация docs/VALIDATION.md.

Пороги — из docs/VALIDATION_PLAN.md (зафиксированы до прогона). Отчёт пишет «уровень» вместо греческой
буквы и не цитирует ТЗ.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.core.configs import load_config
from app.matching.explain import plural
from app.testing.irt import load_scale
from eval import charts

DECISION_NAMES = {"PASSED": "сдал", "BORDERLINE_PASS": "сдал на границе", "BORDERLINE_FAIL": "не сдал, на границе",
                  "FAILED": "не сдал", "None": "нет решения"}


def _spec_name(code: str) -> str:
    names = {s["code"]: s["name"] for s in load_config("taxonomy")["specializations"]}
    return names.get(code, code)

# --------------------------------------------------------------------------- доступ и формат


def get(d: Any, path: str, default: Any = None) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, Mapping) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return default
    return cur


def num(x: Any, nd: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "да" if x else "нет"
    if isinstance(x, int):
        return f"{x:,}".replace(",", " ")
    s = f"{x:.{nd}f}"
    return s.replace(".", ",")


def pct(x: Any, nd: int = 0) -> str:
    return "—" if x is None else f"{x * 100:.{nd}f}%".replace(".", ",")


def ci(m: Mapping[str, Any] | None, nd: int = 2) -> str:
    if not m:
        return "—"
    return f"{num(m.get('mean'), nd)} [{num(m.get('ci_low'), nd)}; {num(m.get('ci_high'), nd)}]"


def table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return out


# --------------------------------------------------------------------------- плитки


def _leak_at(r: Mapping[str, Any], series: str, k: int) -> Any:
    leak = get(r, "testing.leak") or {}
    co = leak.get("coalitions") or []
    if k not in co:
        return None
    vals = (leak.get("false_middle") or {}).get(series) or []
    i = co.index(k)
    return vals[i] if i < len(vals) else None


def _worst_leak_at(r: Mapping[str, Any], k: int) -> Any:
    """Ложный Middle нашего теста при худшей для нас стратегии списывающего (наивный или осторожный)."""
    vals = [v for v in (_leak_at(r, "ours_naive", k), _leak_at(r, "ours_careful", k)) if v is not None]
    return max(vals) if vals else None


def _caught_at(r: Mapping[str, Any], k: int) -> Any:
    leak = get(r, "testing.leak") or {}
    co = leak.get("coalitions") or []
    return (leak.get("caught_naive") or [None] * len(co))[co.index(k)] if k in co else None


def _cusum(r: Mapping[str, Any], scen: str, h: float, key: str) -> Any:
    for row in get(r, f"testing.cusum.scenarios.{scen}.by_h") or []:
        if abs(float(row["h"]) - h) < 1e-9:
            return row.get(key)
    return None


def _calib(r: Mapping[str, Any], n: int) -> Any:
    return next((p["rmse"] for p in get(r, "testing.calibration.curve") or [] if p["n"] == n), None)


TileSpec = tuple[str, str, str, str, Callable[[Mapping[str, Any]], Any], str, Any, str]
# (id, вид ValidationRun, раздел, подпись, значение, оператор, цель, формат)
TILES: tuple[TileSpec, ...] = (
    ("t_decision", "sim", "Тестирование", "Верное решение у заявленного порога",
     lambda r: get(r, "testing.accuracy.decision_accuracy"), "ge", 0.85, "share"),
    ("t_grade_exact", "sim", "Тестирование", "Точный грейд", lambda r: get(r, "testing.accuracy.grade.exact"), "ge", 0.75, "share"),
    ("t_grade_w1", "sim", "Тестирование", "Грейд с точностью ±1", lambda r: get(r, "testing.accuracy.grade.within1"), "ge", 0.99, "share"),
    ("t_len_main", "sim", "Тестирование", "Средняя длина основной части, заданий",
     lambda r: get(r, "testing.accuracy.length.main_mean"), "le", 15, "num1"),
    ("t_len_total", "sim", "Тестирование", "Средняя длина с доборами, заданий",
     lambda r: get(r, "testing.accuracy.length.total_mean"), "le", 17, "num1"),
    ("t_len_p95", "sim", "Тестирование", "p95 длины с доборами, заданий",
     lambda r: get(r, "testing.accuracy.length.total_p95"), "le", 20, "num1"),
    ("t_shown_max", "sim", "Тестирование", "Показано в основной части вместе с pretest (максимум; pretest-семейств в банке пока нет)",
     lambda r: get(r, "testing.accuracy.length.main_shown_max"), "le", 20, "int"),
    ("t_retest_agree", "sim", "Тестирование", "Пересдача: совпадение решения",
     lambda r: get(r, "testing.retest.decision_agreement"), "ge", 0.78, "share"),
    ("t_retest_kappa", "sim", "Тестирование", "Пересдача: κ решения", lambda r: get(r, "testing.retest.decision_kappa"), "ge", 0.55, "num"),
    ("t_retest_qwk", "sim", "Тестирование", "Пересдача: QWK грейда", lambda r: get(r, "testing.retest.grade_qwk"), "ge", 0.85, "num"),
    ("t_retest_r", "sim", "Тестирование", "Пересдача: корреляция оценок уровня", lambda r: get(r, "testing.retest.theta_r"), "ge", 0.88, "num"),
    ("t_hyst", "sim", "Тестирование", "Ложное повышение грейда (гистерезис)",
     lambda r: get(r, "testing.hysteresis.false_upgrade_rate"), "le", 0.07, "share"),
    ("t_instances", "sim", "Тестирование", "Общих экземпляров у пар кандидатов (каждый экземпляр выдаётся одному — по построению)",
     lambda r: get(r, "testing.instance_overlap.shared_between_candidates"), "eq", 0, "int"),
    ("t_content", "sim", "Тестирование",
     "Двум кандидатам с одним семейством выпал одинаковый по тексту экземпляр (Backend, худшее семейство)",
     lambda r: get(r, "testing.instance_overlap.content_identical_pair_max"), "show", None, "pct2"),
    ("t_calib", "sim", "Тестирование", "RMSE сложности после 100 ответов", lambda r: _calib(r, 100), "le", 0.30, "num"),
    # Не было в плане (ориентир концепции ≥ 0,90 / ≥ 0,75) — поэтому «справочно», без статуса.
    ("t_tag_precision", "sim", "Тестирование", "Теги ✓ модулей: точность против истинного уровня (ориентир концепции ≥ 0,90)",
     lambda r: get(r, "testing.tags.check.precision"), "show", None, "share"),
    ("t_tag_recall", "sim", "Тестирование", "Теги ✓ модулей: полнота (ориентир концепции ≥ 0,75)",
     lambda r: get(r, "testing.tags.check.recall"), "show", None, "share"),
    # Худшая для нас стратегия списывающего (наивный ввод чужого ключа или осторожный честный ответ).
    ("l_coal20", "leak", "Утечки", "Ложный Middle у явных Junior, коалиция 20 (худшая стратегия списывания)",
     lambda r: _worst_leak_at(r, 20), "le", 0.15, "share"),
    ("l_caught", "leak", "Утечки", "Наивные списывающие пойманы «чужим ключом» (коалиция 20)",
     lambda r: _caught_at(r, 20), "ge", 0.90, "share"),
    ("l_b0", "leak", "Утечки", "B0 фиксированная форма: ложный Middle при коалиции 20",
     lambda r: _leak_at(r, "b0", 20), "show", None, "share"),
    ("d_delay", "drift", "Дрейф", "CUSUM, 50% знают метод: медиана задержки (h = 6), ответов",
     lambda r: _cusum(r, "share50", 6.0, "median_delay"), "le", 200, "int"),
    ("d_false", "drift", "Дрейф", "CUSUM: ложные карантины без утечки (h = 6)",
     lambda r: _cusum(r, "control", 6.0, "false_quarantine"), "le", 0.05, "share"),
    ("c_macro_f1", "vcat", "Категоризация", "macro-F1 подсказки специализации (V-CAT)",
     lambda r: get(r, "categorization.macro_f1"), "ge", 0.90, "num"),
    ("c_no_fsp", "vcat", "Категоризация", "Профили без ФСП получают категорию по тем же правилам",
     lambda r: get(r, "testing.no_fsp.survey_and_test_take_no_fsp_input"), "eq", True, "bool"),
    ("m_p10", "matching", "Подбор", "P@10 по оракулу, test-брифы, сквозной режим",
     lambda r: get(r, "matching.test.systems.ours.p10.mean"), "ge", 0.75, "num"),
    ("m_vs_b2", "matching", "Подбор", "P@10 значимо выше B2 (парный бутстреп)",
     lambda r: bool(get(r, "matching.test.paired_vs_b2.ours.significant")) and (get(r, "matching.test.paired_vs_b2.ours.diff") or 0) > 0,
     "eq", True, "bool"),
    ("m_spec", "matching", "Разбор брифа", "Специализация top-1", lambda r: get(r, "matching.parser.test.spec_top1"), "ge", 0.90, "share"),
    ("m_grade", "matching", "Разбор брифа", "Грейд точно", lambda r: get(r, "matching.parser.test.grade_exact"), "ge", 0.75, "share"),
    ("m_grade_w1", "matching", "Разбор брифа", "Грейд ±1", lambda r: get(r, "matching.parser.test.grade_within1"), "ge", 0.95, "share"),
    ("m_skills_f1", "matching", "Разбор брифа", "F1 навыков", lambda r: get(r, "matching.parser.test.skills.f1"), "ge", 0.85, "num"),
    ("m_must_nice", "matching", "Разбор брифа", "must/nice определены верно",
     lambda r: get(r, "matching.parser.test.must_nice_accuracy"), "ge", 0.80, "share"),
    ("m_cat3", "matching", "Разбор брифа", "Верная категория в топ-3", lambda r: get(r, "matching.parser.test.category_top3"), "ge", 0.95, "share"),
    ("m_stability", "matching", "Устойчивость", "Стабильность топ-10 при пересдаче (overlap@10)",
     lambda r: get(r, "matching.robustness.stability.ours_overlap10"), "ge", 0.70, "num"),
    ("m_jaccard", "matching", "Устойчивость", "Жаккар топ-10 после «добавлен nice-навык»",
     lambda r: get(r, "matching.robustness.nice_skill_jaccard10.mean"), "ge", 0.6, "num"),
    ("m_weights", "matching", "Устойчивость", "Падение P@10 при сдвиге веса на ±0,05 (максимум)",
     lambda r: get(r, "matching.robustness.weights.max_drop"), "le", 0.05, "num"),
    ("m_stuff_ours", "matching", "Устойчивость", "Накрутка навыков: сдвиг ранга вверх, наш подбор",
     lambda r: get(r, "matching.robustness.stuffing.ours_mean_shift_up"), "le", 2, "num1"),
    ("m_stuff_bm25", "matching", "Устойчивость", "Накрутка навыков: сдвиг ранга вверх, BM25 (ожидание ≥ 10)",
     lambda r: get(r, "matching.robustness.stuffing.bm25_mean_shift_up"), "ge", 10, "num1"),
    ("m_exp10", "matching", "Экспозиция", "Кандидаты без ФСП: условное отношение экспозиции, топ-10",
     lambda r: get(r, "matching.exposure.k10.conditional"), "between", (0.8, 1.25), "num"),
    ("m_exp50", "matching", "Экспозиция", "Кандидаты без ФСП: условное отношение экспозиции, топ-50",
     lambda r: get(r, "matching.exposure.k50.conditional"), "between", (0.8, 1.25), "num"),
    ("m_fid_sum", "matching", "Объяснения", "Σ вкладов = Score", lambda r: get(r, "matching.fidelity.sum_equals_score"), "ge", 1.0, "share"),
    ("m_fid_ev", "matching", "Объяснения", "Доводы ссылаются на существующие доказательства",
     lambda r: get(r, "matching.fidelity.evidence_exists"), "ge", 1.0, "share"),
    ("m_fid_fsp", "matching", "Объяснения", "Отсутствие ФСП никогда не «против»", lambda r: get(r, "matching.fidelity.fsp_never_con"), "ge", 1.0, "share"),
    ("m_fid_cf", "matching", "Объяснения", "Без доказательства довода №1 Score падает не меньше превышения над медианой",
     lambda r: get(r, "matching.fidelity.counterfactual_ok"), "ge", 1.0, "share"),
    ("m_build", "matching", "Производительность", "p95 сборки версии подборки, мс",
     lambda r: get(r, "matching.build_ms.p95"), "le", 300, "num0"),
)


def _passes(v: Any, op: str, target: Any) -> bool | None:
    if op == "show" or v is None:
        return None
    if op == "ge":
        return float(v) >= float(target) - 1e-9
    if op == "le":
        return float(v) <= float(target) + 1e-9
    if op == "eq":
        return v == target
    if op == "between":
        return float(target[0]) - 1e-9 <= float(v) <= float(target[1]) + 1e-9
    return None


def fmt_value(v: Any, f: str) -> str:
    if v is None:
        return "—"
    if f == "share":
        return num(v, 2)
    if f == "num1":
        return num(float(v), 1)
    if f == "num0":
        return num(float(v), 0)
    if f == "int":
        return num(int(v))
    if f == "bool":
        return "да" if v else "нет"
    if f == "pct2":
        return pct(float(v), 2)
    return num(float(v), 2)


def fmt_target(op: str, target: Any, f: str) -> str:
    if op == "show":
        return "показываем"
    if op == "between":
        return f"{num(target[0])}–{num(target[1])}"
    sign = {"ge": "≥ ", "le": "≤ ", "eq": ""}[op]
    if f == "bool":
        return "да" if target else "нет"
    return sign + fmt_value(target, f if f != "int" else "int")


def tiles(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = []
    for tid, kind, section, label, fn, op, target, f in TILES:
        try:
            v = fn(result)
        except (KeyError, TypeError, ValueError, IndexError):
            v = None
        ok = _passes(v, op, target)
        # Текст — по значению, округлённому как в JSON (4 знака): иначе 0,21500000001 в плитке и 0,215 в теле
        # отчёта показывались бы как 0,22 и 0,21. Статус — по точному значению.
        text = fmt_value(round(v, 4) if isinstance(v, float) else v, f)
        # 0,749 при цели ≥ 0,75 не должно выглядеть как «0,75 — не выполнено»: у пограничных значений третий знак.
        if (ok is not None and op in ("ge", "le") and f in ("share", "num") and text == fmt_value(target, f)
                and abs(float(v) - float(target)) > 1e-9):
            text = num(float(v), 3)
        out.append({
            "id": tid, "kind": kind, "section": section, "label": label, "value": v, "op": op,
            "target": list(target) if isinstance(target, tuple) else target, "format": f,
            "value_text": text, "target_text": fmt_target(op, target, f), "pass": ok,
        })
    return out


def status(p: bool | None) -> str:
    return "справочно" if p is None else ("✓ выполнено" if p else "✗ не выполнено")


# --------------------------------------------------------------------------- графики


def write_charts(r: Mapping[str, Any], out_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    leak = get(r, "testing.leak")
    if leak:
        fm = leak["false_middle"]
        series = [("Наш тест, наивный", fm["ours_naive"]), ("Наш тест, осторожный", fm["ours_careful"]),
                  ("B0 фикс. форма", fm["b0"]), ("B1 LOFT", fm["b1"]), ("B2 без клонов", fm["b2"]), ("B3 LLM", fm["b3"])]
        svg = charts.line_chart("Ложный Middle у явных Junior при сливе ответов", [str(k) for k in leak["coalitions"]],
                                series, y_label="доля ложных Middle", x_title="участников коалиции, сливших всё увиденное",
                                threshold=(0.15, "цель ≤ 0,15 при коалиции 20"))
        out["leak"] = _save(out_dir / "leak.svg", svg)
    curve = get(r, "testing.calibration.curve")
    if curve:
        svg = charts.line_chart("Калибровка сложности: ошибка по числу ответов", [str(p["n"]) for p in curve],
                                [("RMSE сложности", [p["rmse"] for p in curve])], y_label="RMSE",
                                x_title="ответов на семейство (0 — только экспертная оценка)",
                                y_max=max(0.8, max(p["rmse"] or 0 for p in curve) * 1.1), threshold=(0.30, "цель ≤ 0,30 после 100"))
        out["calibration"] = _save(out_dir / "calibration.svg", svg)
    ex = get(r, "testing.cusum.example")
    if ex and ex.get("leaked") and ex.get("honest"):
        h = get(r, "testing.cusum.h_config") or 6
        lk, ho = ex["leaked"], ex["honest"]
        svg = charts.path_chart("CUSUM по семейству: честный поток и слив метода",
                                [("слитое семейство", lk["path"]), ("честное семейство", ho["path"][: len(lk["path"])])],
                                y_label="статистика CUSUM", x_title="номер ответа",
                                threshold=(float(h), f"порог h = {num(float(h), 0)}"),
                                marker_x=(int(lk["n_phase"]), "начало слива"))
        out["cusum"] = _save(out_dir / "cusum.svg", svg)
    sysm = get(r, "matching.test.systems")
    if sysm:
        short = {"ours": "Наш, сквозной", "ours_gold": "Наш, эталонный бриф", "b2": "B2 категория + уровень",
                 "b0": "B0 случайный", "b1": "B1 BM25 по самоописанию", "b3": "B3 TF-IDF"}
        rows = [(short[s], sysm[s]["p10"]["mean"], sysm[s]["p10"]["ci_low"], sysm[s]["p10"]["ci_high"], s == "ours")
                for s in short if s in sysm]
        svg = charts.ci_chart("P@10 по оракулу, 20 test-брифов, 95% бутстреп-ДИ", rows, x_label="P@10",
                              threshold=(0.75, "цель 0,75"))
        out["p10"] = _save(out_dir / "p10.svg", svg)
    return out


def _save(p: Path, svg: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(svg + "\n", encoding="utf-8", newline="\n")
    return p


def _img(charts_map: Mapping[str, Path], key: str, doc_dir: Path, alt: str) -> list[str]:
    if key not in charts_map:
        return []
    rel = os.path.relpath(charts_map[key], doc_dir).replace(os.sep, "/")
    return [f"![{alt}]({rel})", ""]


# --------------------------------------------------------------------------- Markdown


def render_markdown(r: Mapping[str, Any], *, charts: Mapping[str, Path], doc_dir: Path, json_path: Path) -> str:
    L: list[str] = []
    meta = r["meta"]
    mode = "полный" if meta["mode"] == "full" else "быстрый (--quick, меньшие выборки)"
    sha = (meta.get("git_sha") or "—")[:10]
    json_rel = os.path.relpath(json_path, doc_dir).replace(os.sep, "/")
    L += [
        "# Валидация «Взаимно»: результаты",
        "",
        f"> Файл сгенерирован командой `cd backend && uv run python -m eval.validate` — режим {mode}, сид "
        f"`{meta['seed']}`, коммит `{sha}`, {num(meta['total_s'], 0)} с на {meta['workers']} процессах. Руками не "
        f"редактируется. Протокол — [VALIDATION_PLAN.md](VALIDATION_PLAN.md): метрики и пороги в нём записаны до "
        f"прогона, уточнения процедуры после прогонов вынесены в отдельные разделы 4–5 (пороги не менялись). Все числа в "
        f"машиночитаемом виде — [`{json_rel}`]({json_rel}) (его же показывает «Лаборатория» после `--save-db`).",
        "",
        "Всё ниже — **симуляция**: она проверяет процедуру на синтетической популяции с известной истиной и "
        "работает на тех же движках, что и прод (`app/testing`, банк из "
        f"{meta['bank_families']} семейств с экспертными оценками сложности, `app/matching`, `app/fsp`). "
        "Раздела «люди» (пилот, экспертная разметка пар) в этом прогоне нет — см. «Честные ограничения».",
        "",
        "## Итоги",
        "",
    ]
    tl = r.get("tiles") or tiles(r)
    L += table(["Раздел", "Метрика", "Значение", "Цель", "Статус"],
               [(t["section"], t["label"], t["value_text"], t["target_text"], status(t["pass"])) for t in tl])
    failed = [t for t in tl if t["pass"] is False]
    L += ["", f"Выполнено {sum(1 for t in tl if t['pass'])} из {sum(1 for t in tl if t['pass'] is not None)} "
              f"пороговых пунктов. Не выполнены: " + (", ".join(f"«{t['label']}»" for t in failed) if failed else "нет") + ".", ""]
    L += _data_section(r)
    L += _testing_section(r, charts, doc_dir)
    L += _vcat_section(r)
    L += _matching_section(r, charts, doc_dir)
    L += _pairs_section(r)
    L += _limits_section(r, failed)
    L += [
        "## Как воспроизвести",
        "",
        "```",
        "cd backend",
        "uv run python -m eval.validate            # полный прогон: этот файл, eval/out/validation.json, графики, набор пар",
        "uv run python -m eval.validate --quick    # дымовой прогон на меньших выборках (eval/out/validation_quick.json)",
        "uv run python -m eval.validate --save-db  # дополнительно записать ValidationRun в базу",
        "uv run python -m eval.validate --save-db --from-json eval/out/validation.json  # залить готовый результат без прогона",
        "```",
        "",
        "Время по разделам, с: " + ", ".join(f"{k} {num(v, 1)}" for k, v in meta["durations_s"].items()) + ".",
        "",
    ]
    text = "\n".join(L)
    return text.replace("θ", "уровень")


def _data_section(r: Mapping[str, Any]) -> list[str]:
    p = r["population"]
    m = r["matching"]
    vc = p.get("vss_truth_correlation") or {}
    per = m.get("per_brief") or {}
    styles: dict[str, int] = {}
    for b in per.values():
        styles[b["style"]] = styles.get(b["style"], 0) + 1
    style_names = {"formal": "формальная вакансия", "chat": "переписка со сленгом и опечатками", "short": "короткий запрос",
                   "long": "длинное описание с разделами", "mixed_en": "смесь с английским"}
    L = ["## 1. Данные", "",
         "**Популяция с латентной истиной** (`eval/population.py`). Каждый кандидат порождается своим генератором с "
         "сидом, поэтому популяция из n кандидатов — префикс популяции из 2n.", ""]
    L += table(["Показатель", "Значение"], [
        ("Кандидатов", num(p["n"])),
        ("Истинная специализация", ", ".join(f"{_spec_name(k)} {pct(v)}" for k, v in p["true_spec"].items())),
        ("Истинный грейд", ", ".join(f"{load_scale().grade_name(k)} {pct(v)}" for k, v in p["true_grade"].items())),
        ("Заявленная специализация не совпадает с истинной", pct(p["mismatched_share"], 1)),
        ("Есть история ФСП", pct(p["fsp_share"], 1)),
        ("Корреляция VSS (лучшая дисциплина) с истинным уровнем", f"{num(vc.get('pearson'))} (Спирмен {num(vc.get('spearman'))}, n = {vc.get('n')})"),
        ("Есть регулярные задачи", pct(p["tasks_share"], 1)),
        ("Категория подтверждена тестом", pct(p["confirmed_share"], 1)),
        ("Навыков в истинном стеке / приписано в самоописании (в среднем)", f"{num(p['true_skills_mean'], 1)} / {num(p['fake_skills_mean'], 1)}"),
    ])
    L += ["",
          f"**Брифы** (`eval/briefs/*.json`): {len(per)} русскоязычных брифов, разбиение 20 dev / 20 test зафиксировано в "
          "файлах. Стили: " + ", ".join(f"{style_names.get(k, k)} — {v}" for k, v in sorted(styles.items())) + ". "
          "**Тексты и эталонная разметка (специализация, грейд, must/nice, формат, город, вилка) написаны командой с "
          "помощью ИИ-ассистента**; посторонних авторов в наборе нет, а история правок эталона до первого коммита не "
          "зафиксирована. Все итоговые цифры подбора — по test-брифам; веса на брифах не подбирались.", ""]
    return L


def _testing_section(r: Mapping[str, Any], cm: Mapping[str, Path], doc_dir: Path) -> list[str]:
    t = r["testing"]
    a = t["accuracy"]
    L = ["## 2. Тестирование", "",
         "Метрики точности и пересдачи — по кандидатам, чья заявленная специализация совпадает с истинной "
         f"(n = {num(a['n'])}). Решение «верное», если «сдал» ⇔ истинный уровень не ниже порога заявленного грейда. "
         "«Не подтверждён» считается как «Ниже Стажёра».", "", "### 2.1. Точность", ""]
    g, g4 = a["grade"], a["grade_b4_experience"]
    L += table(["Система", "Верное решение у порога", "Точный грейд", "±1", "κ", "QWK"], [
        ("Наш CAT (основная часть + доборы)", num(a["decision_accuracy"]), num(g["exact"]), num(g["within1"]), num(g["kappa"]), num(g["qwk"])),
        ("B4: грейд по стажу (подсказка опроса)", "—", num(g4["exact"]), num(g4["within1"]), num(g4["kappa"]), num(g4["qwk"])),
    ])
    L += ["", "Точность решения по тому, как кандидат заявил грейд: " + "; ".join(
        f"{k} — {num(v['accuracy'])} (n = {v['n']})" for k, v in a["decision_accuracy_by_claim"].items()) + ".", ""]
    conf = a.get("confusion") or {}
    grade_order = ["Ниже Стажёра / не подтверждён", "Стажёр", "Junior", "Middle", "Senior"]
    keys = {c for row in conf.values() for c in row} | set(conf)
    cols = [g for g in grade_order if g in keys] + sorted(keys - set(grade_order))
    if conf:
        L += ["Матрица «истинный грейд → итоговый грейд» (строки — истина):", ""]
        L += table(["Истина \\ итог", *cols], [(k, *[conf[k].get(c, 0) for c in cols]) for k in cols if k in conf])
        L += [""]
    bs = a.get("by_spec") or {}
    if bs:
        L += ["Точность по специализациям теста (кандидаты с верной заявкой):", ""]
        L += table(["Специализация", "n", "Верное решение", "Точный грейд", "±1", "Подтверждено", "Заданий в тесте: среднее / p95"],
                   [(k, v["n"], num(v["decision_accuracy"]), num(v["grade"]["exact"]), num(v["grade"]["within1"]),
                     pct(v["confirmed_share"]), f"{num(v['total_mean'], 1)} / {num(v['total_p95'], 0)}") for k, v in bs.items()])
        L += [""]
    mm = a["mismatched"]
    L += [f"Кандидаты, ошибившиеся со специализацией (проходят чужой тест, n = {mm['n']}): категорию получают "
          f"{pct(mm['share_confirmed_any'])}, на уровне своего истинного грейда или выше — {pct(mm['share_confirmed_at_true_grade_or_higher'])}. "
          "Подсказка опроса (раздел 3) нужна, чтобы таких было меньше.", "", "### 2.2. Длина теста", ""]
    ln = a["length"]
    L += table(["Метрика", "Значение", "Цель"], [
        ("Основная часть, оцениваемых заданий: среднее / p95", f"{num(ln['main_mean'], 1)} / {num(ln['main_p95'], 0)}", "≤ 15"),
        ("С доборами, оцениваемых: среднее / p95", f"{num(ln['total_mean'], 1)} / {num(ln['total_p95'], 0)}", "≤ 17 / ≤ 20"),
        ("Основная часть, показано вместе с pretest: среднее / p95 / максимум",
         f"{num(ln.get('main_shown_mean'), 1)} / {num(ln.get('main_shown_p95'), 0)} / {ln.get('main_shown_max')}", "≤ 20 (DECISIONS п.17)"),
        ("Pretest-заданий на сессию", num(ln["pretest_items_per_session"], 2), "—"),
        ("Доля основных частей, дошедших до максимума", pct(ln["share_main_at_max"]), "—"),
    ])
    L += ["", "Решения основной части: " + ", ".join(f"{DECISION_NAMES.get(k, k)} — {v}" for k, v in sorted(a["decisions"].items()))
          + ".", "",
          "### 2.3. Пересдача и гистерезис", ""]
    rt, hy = t["retest"], t["hysteresis"]
    L += [f"Вторая сессия того же человека; все семейства первой сессии исключены (n = {num(rt['n'])}).", ""]
    L += table(["Метрика", "Значение", "Цель"], [
        ("Совпадение решения", num(rt["decision_agreement"]), "≥ 0,78"),
        ("κ решения", num(rt["decision_kappa"]), "≥ 0,55"),
        ("Точное совпадение грейда / ±1", f"{num(rt['grade_exact'])} / {num(rt['grade_within1'])}", "показываем"),
        ("QWK грейда", num(rt["grade_qwk"]), "≥ 0,85"),
        ("Корреляция оценок уровня", num(rt["theta_r"]), "≥ 0,88"),
        ("Ложное повышение при тесте на грейд выше (гистерезис)", f"{num(hy['false_upgrade_rate'])} (n = {hy['n']})", "≤ 0,07"),
    ])
    L += ["", "### 2.4. Ошибка экспертов в сложности и крутизне (S2)", ""]
    s2 = t["expert_error"]
    L += [f"Истинная сложность = экспертная + N(0; σ²) — ошибка одна на семейство; истинная крутизна A ≠ 1,5 модели. "
          f"n = {s2['n']} кандидатов на условие.", ""]
    L += table(["Условие", "Верное решение у порога", "Точный грейд"],
               [(c["label"], num(c["decision_accuracy"]), num(c["grade_exact"])) for c in s2["conditions"]])
    L += ["", "### 2.5. Бейзлайны тестирования", ""]
    bl = t["baselines"]
    L += [f"Подвыборка n = {bl['n']}. B0 и B1 — формы из 16 заданий («сдал», если вероятность уровня не ниже порога ≥ 0,5). "
          "B3 «LLM под каждого»: каждое задание сгенерировано заново, его сложность = задуманная + N(0; σ²) на экземпляр, "
          "часть ключей неверна. **Доля битых ключей — допущение, а не замер**: реального прогона заданий через "
          "YandexGPT/GigaChat в этом прогоне нет; поэтому показана чувствительность к этой доле.", ""]
    L += table(["Система", "Верное решение", "Точный грейд", "Длина", "Совпадение решения при пересдаче"],
               [(x["system"], num(x["decision_accuracy"]), num(x["grade_exact"]), num(x["length"], 1),
                 num(x.get("retest_agreement"))) for x in bl["rows"]])
    L += ["", "### 2.6. Утечки (S4)", ""]
    lk = t["leak"]
    L += [f"{lk['n_cheaters']} «явных Junior» (истинный уровень равномерно в [−0,8; −0,3]) заявляют Middle (Backend). "
          "Коалиция из k честных кандидатов сливает всё, что видела. Наш тест: экземпляры одноразовые; «наивный» "
          "списывающий вводит слитый ключ чужого экземпляра того же семейства, «осторожный» видит, что код другой, и "
          "отвечает честно. B0/B1/B2: копирует, только если достался тот же экземпляр (консервативно в пользу "
          "бейзлайнов). B3: экземпляры уникальны, слив бесполезен — его доля ложных Middle равна честной ошибке.", ""]
    L += _img(cm, "leak", doc_dir, "Доля ложных Middle по размеру коалиции")
    fm = lk["false_middle"]
    names = [("ours_naive", "Наш тест, наивный списывающий"), ("ours_careful", "Наш тест, осторожный"),
             ("b0", "B0 фиксированная форма"), ("b1", "B1 LOFT"), ("b2", "B2 CAT без клонов (20 экземпляров)"),
             ("b3", "B3 «LLM под каждого»")]
    L += table(["Коалиция", *[str(k) for k in lk["coalitions"]]],
               [("Слито семейств банка Backend", *[pct(x) for x in lk["leaked_bank_share"]])]
               + [(n, *[num(x) for x in fm[key]]) for key, n in names]
               + [("Наивные пойманы «чужим ключом»", *[num(x) for x in lk["caught_naive"]])])
    L += ["", "В «Итогах» для нашего теста берётся худшая из двух стратегий списывающего: наивный ввод чужого ключа "
          "почти всегда проваливается, поэтому рациональный списывающий отвечает честно, и его доля ложных Middle равна "
          "честной ошибке теста у порога."]
    ml = lk["method_leak"]
    col = lk.get("collisions") or {}
    L += ["", f"**Утечка метода** (коалиция {ml['coalition']}, слито {pct(ml['leaked_bank_share'])} семейств): тем, кто знает "
          "идею семейства, оно легче на Δb. Клоны защищают от слива ответов, но не от слива идеи — это видно в таблице.", ""]
    L += table(["Δb", "Ложный Middle"], [(num(x["delta_b"]), num(x["false_middle"])) for x in ml["rows"]])
    L += ["", f"Детектор «чужой ключ» активен для {lk['detector_active_families']['n']} из {lk['detector_active_families']['of']} "
          f"семейств Backend (доля коллизий ключей < 2%, измерено на {col.get('n_instances', '—')} экземплярах каждого "
          f"семейства реального банка; медиана коллизий {num(col.get('median_rate'))}). У семейств с выбором ответа ключи "
          "совпадают часто, поэтому для них детектор не работает — их защищают только клоны и CUSUM.", "",
          "### 2.7. Дрейф: CUSUM (S5)", ""]
    cu = t["cusum"]
    L += [f"Поток ответов по каждому из {cu['n_families']} семейств Backend: сначала объём {num(cu['sessions_per_phase'])} "
          f"честных сессий, затем столько же, где доля проходящих знает метод {cu['n_leaked']} семейств (Δb = "
          f"{num(cu['delta_b'], 1)}). Повторов: {cu['replicates']}. Прод-порог h = {num(float(cu['h_config']), 0)}.", ""]
    L += _img(cm, "cusum", doc_dir, "Пример статистики CUSUM")
    rows = []
    for scen, label in (("control", "без утечки"), ("share20", "20% знают метод"), ("share50", "50% знают метод")):
        for row in get(cu, f"scenarios.{scen}.by_h") or []:
            rows.append((label, num(float(row["h"]), 0), num(row.get("detected")), num(row.get("median_delay"), 0),
                         num(row.get("false_quarantine"), 3)))
    L += table(["Сценарий", "h", "Найдено слитых семейств", "Медиана задержки, ответов", "Ложные карантины"], rows)
    ctrl = {float(x["h"]): x for x in get(cu, "scenarios.control.by_h") or []}
    s50 = {float(x["h"]): x for x in get(cu, "scenarios.share50.by_h") or []}
    h0 = float(cu["h_config"])
    ok_h = [h for h in sorted(ctrl) if (ctrl[h].get("false_quarantine") or 0) <= 0.05]
    if h0 in ctrl and (ctrl[h0].get("false_quarantine") or 0) > 0.05:
        txt = (f"Прод-порог h = {num(h0, 0)} на потоке такой длины даёт {pct(ctrl[h0]['false_quarantine'])} ложных карантинов "
               "(цель ≤ 5%): у честного потока LLR в среднем отрицателен, но за тысячи ответов CUSUM без сброса успевает "
               "случайно пересечь низкий порог. ")
        if ok_h:
            h1 = ok_h[0]
            delay = s50.get(h1, {}).get("median_delay")
            delay_txt = "—" if delay is None else f"{num(delay, 0)} {plural(int(round(delay)), 'ответ', 'ответа', 'ответов')}"
            txt += (f"Первый порог сетки с ложными ≤ 5% — h = {num(h1, 0)}: при 50% знающих он находит "
                    f"{pct(s50.get(h1, {}).get('detected'))} слитых семейств с медианой задержки в {delay_txt}. ")
        txt += ("Рекомендация владельцу конфигурации тестирования: поднять cusum_h или сбрасывать статистику по окну; "
                "без утечки CUSUM — сигнал методисту, а не автоматическая блокировка. Порог плана не меняем.")
        L += ["", txt]
    L += ["", "### 2.8. Калибровка сложности", ""]
    ca = t["calibration"]
    L += [f"Истинная сложность неякорных семейств = экспертная + N(0; {num(ca['sigma_expert'], 1)}²); MAP-уточнение "
          f"`calibration.calibrate_family` по n ответам. Семейств: {ca['families']}, повторов: {ca['replicates']}.", ""]
    L += _img(cm, "calibration", doc_dir, "RMSE сложности по числу ответов")
    L += table(["Ответов на семейство", *[str(p["n"]) for p in ca["curve"]]],
               [("RMSE", *[num(p["rmse"]) for p in ca["curve"]])])
    L += ["", "### 2.9. Дискриминативность", ""]
    di = t["discrimination"]
    L += ["r_pb — точечно-бисериальная корреляция ответа с оценкой уровня **без этого задания**; pretest-семейств в "
          "банке пока нет, поэтому считаем по боевым заданиям. ΔP — доля верных у Middle минус у Junior (по оценке уровня).", ""]
    L += table(["Специализация", "Семейств", "Медиана r_pb", "Доля r_pb ≥ 0,20", "Флаг r_pb < 0,10", "Медиана ΔP", "Доля ΔP ≥ 0,20"],
               [(s, v["families"], num(v["r_pb_median"]), num(v["share_r_pb_ge_020"]), ", ".join(v["flag_r_pb_lt_010"]) or "нет",
                 num(v["delta_p_median"]), num(v["share_delta_p_ge_020"])) for s, v in di["by_spec"].items()])
    L += ["", "AUC оценки уровня для соседних грейдов: " + ", ".join(f"{k.replace('_', ' / ')} — {num(v)}" for k, v in di["auc_adjacent"].items())
          + f". Надёжность 1 − mean(SE²)/var: {num(di['reliability'])}.", "",
          *_overlap_text(t["instance_overlap"]), ""]
    tg = t.get("tags")
    if tg:
        L += ["**Теги модулей.** ✓ утверждает «уровень модуля не ниже порога Junior», ✓✓ — «не ниже порога итогового "
              f"грейда»; истина — уровень модуля генератора. По {num(tg['modules'])} модулям: точность ✓ "
              f"{num(tg['check']['precision'])}, полнота {num(tg['check']['recall'])} (полнота считается по всем модулям "
              f"теста, у {pct(tg['share_insufficient'])} из них заданий меньше двух — «~»); точность ✓✓ "
              f"{num(tg['strong_precision'])} (n = {tg['strong_n']}). В плане этой метрики не было, поэтому ориентир "
              "концепции (≥ 0,90 / ≥ 0,75) показан справочно.", ""]
    nf = t["no_fsp"]
    L += ["### 2.10. Кандидаты без истории ФСП", "",
          "Опрос и тест не принимают на вход ничего из ФСП, поэтому категория присваивается по тем же правилам. "
          f"Доля подтверждённых тестом: с ФСП — {pct(nf['confirmed_share_with_fsp'], 1)} (n = {nf['n_with_fsp']}), "
          f"без ФСП — {pct(nf['confirmed_share_without_fsp'], 1)} (n = {nf['n_without_fsp']}); разница — только от уровня, "
          "который в генераторе коррелирует с ФСП.", ""]
    return L


def _overlap_text(io: Mapping[str, Any]) -> list[str]:
    """Пересечение экземпляров: id — по построению выдачи, содержание — измерено на реальном банке."""
    txt = (f"Пересечение экземпляров у пар кандидатов: {io['shared_between_candidates']} из {num(io['instances'])} "
           "выданных (тест и пересдача). Это свойство выдачи, а не измерение: симулятор, как и пул в проде, отдаёт "
           "каждый экземпляр одному человеку.")
    if io.get("content_identical_pair_max") is not None:
        txt += (f" Измеримый вопрос — увидят ли двое одно и то же задание под разными id: по "
                f"{io.get('content_n_instances')} экземплярам каждого из {io.get('content_families')} семейств Backend "
                f"вероятность, что двум кандидатам с одним семейством выпадет одинаковый по тексту экземпляр, в среднем "
                f"{pct(io.get('content_identical_pair_mean'), 2)}, в худшем семействе "
                f"({io.get('content_identical_pair_max_family')}) — {pct(io.get('content_identical_pair_max'), 2)}; "
                f"совпадения есть у {io.get('content_families_with_identical')} семейств. У таких близнецов и ключ "
                "общий; модель утечек в 2.6 это учитывает: слитый ключ верен с этой вероятностью (плюс угадывание).")
    return [txt]


def _vcat_section(r: Mapping[str, Any]) -> list[str]:
    v = r["categorization"]
    names = v.get("spec_names") or {}
    L = ["## 3. Категоризация по специализации (V-CAT)", "",
         f"{v['n_profiles']} синтетических профилей опроса ({len(v['specializations'])} специализаций × "
         f"{v['n_profiles'] // max(1, len(v['specializations']))}) и {v['n_hard']} трудных случаев. Ответы порождает "
         f"своя матрица в `eval/vcat.py` (шум {pct(v['generator']['noise'])}, «амбициозные» отметки соседней "
         f"специализации у {pct(v['generator']['ambitious'])} профилей, выбор кандидата совпадает с истиной в "
         f"{pct(v['generator']['choice_true'])}); классифицирует прод-функция `app.testing.survey.classify`.", ""]
    L += table(["Метрика", "Подсказка классификатора", "Только выбор кандидата"], [
        ("macro-F1", num(v["macro_f1"]), num(v["baseline_choice_macro_f1"])),
        ("Точность", num(v["accuracy"]), num(v["baseline_choice_accuracy"])),
    ])
    L += ["", f"Подсказка показывается {pct(v['hint_rate'])} профилей, верна в {pct(v['hint_precision'])} случаев; из тех, "
          f"кто выбрал не свою специализацию, верную подсказку получают {pct(v['wrong_choice_hint_recall'])}.", ""]
    L += table(["Специализация", "Точность", "Полнота", "F1"],
               [(names.get(k, k), num(x["precision"]), num(x["recall"]), num(x["f1"])) for k, x in v["per_class"].items()])
    hk = v["hard"]["by_kind"]
    kind_names = {"fullstack": "Fullstack", "career_change": "смена профессии", "contradictory": "противоречивый опрос"}
    L += ["", "Трудные случаи:", ""]
    L += table(["Тип", "n", "Верная подсказка (top-1)", "Подсказка показана"],
               [(kind_names.get(k, k), x["n"], num(x["top_accuracy"]), num(x["hint_shown"])) for k, x in hk.items()])
    L += [""]
    if (v["macro_f1"] or 0) < 0.90:
        L += ["**Порог macro-F1 ≥ 0,90 не достигнут.** Веса W/V опроса (`config/testing.yaml`, раздел survey) — "
              "начальные значения. Итоговую специализацию всё равно выбирает кандидат, а категорию подтверждает тест.", ""]
    th = v.get("tuning_hint")
    if th:
        cur, best = th["current"], th["best"]
        L += [f"Подсказка владельцу конфигурации (в прод-конфиг не внесена): сетка «вес технологий × бонус за выбор» "
              f"подобрана на чётных профилях V-CAT и проверена на нечётных. Сейчас ({num(cur['tech_weight'], 1)} / "
              f"{num(cur['choice_bonus'], 1)}) macro-F1 на проверочной половине — {num(cur['macro_f1_test'])}; при "
              f"{num(best['tech_weight'], 1)} / {num(best['choice_bonus'], 1)} — {num(best['macro_f1_test'])}. Оговорка: "
              "генератор V-CAT писала та же команда, поэтому выигрыш стоит перепроверить на живых опросах.", ""]
    return L


def _matching_section(r: Mapping[str, Any], cm: Mapping[str, Path], doc_dir: Path) -> list[str]:
    m = r["matching"]
    labels = m["systems"]
    test = m["test"]
    L = ["## 4. Подбор", "",
         "**Оракул релевантности** по скрытой истине (VALIDATION_PLAN §1.5): rel = 0 при несмежной специализации; иначе 3, "
         "−1 за соседний грейд (≥ 2 ступени → 0), −1 если покрыты не все must, −2 если покрыто меньше половины, −1 если "
         "минимум кандидата выше 1,1 · верх вилки. Релевантен ⇔ rel ≥ 2. Навык «покрыт», если он есть в истинном стеке и "
         "(не тестируется или истинный уровень его модуля не ниже порога грейда брифа − 0,5). Формат работы оракул не учитывает.",
         "",
         f"Популяция — {num(m['n_population'])} кандидатов, из них с подтверждённой категорией {num(m['n_pool_confirmed'])}. "
         "Сквозной режим: разбор брифа → один уточняющий вопрос, если парсер его задал (отвечает работодатель — "
         "вариант из эталона) → обязательная вилка (если парсер её не нашёл, работодатель вводит её в форме) → "
         "рекомендованные категории → версия подборки с пресетом «Баланс».", "",
         "### 4.1. Главная таблица (20 test-брифов, среднее и 95% бутстреп-ДИ по брифам)", ""]
    L += _img(cm, "p10", doc_dir, "P@10 по системам с доверительными интервалами")
    order = ["ours", "ours_gold", "ours_noq", "b2", "b0", "b1", "b3"]
    L += table(["Система", "P@10", "P@5", "nDCG@10", "Recall@50", "MRR"],
               [(labels[s], ci(test["systems"][s]["p10"]), ci(test["systems"][s]["p5"]), ci(test["systems"][s]["ndcg10"]),
                 ci(test["systems"][s]["recall50"]), ci(test["systems"][s]["mrr"])) for s in order])
    ceil = test["p10_ceiling"] or 0
    tail = " — у части брифов релевантных меньше 10." if ceil < 0.999 else "."
    L += ["", f"Потолок P@10 (если поставить всех релевантных наверх): {num(ceil)}{tail} Recall@50 ограничен сверху "
          "величиной 50 / (число релевантных в популяции), а релевантных у брифа — сотни, поэтому он мал у всех систем.", ""]
    pv = test["paired_vs_b2"]
    L += ["Разница P@10 с B2 «категория + точечная оценка уровня» (парный бутстреп по брифам):", ""]
    L += table(["Система", "Разница", "95% ДИ", "Значимо"],
               [(labels.get(s, m["ablations"].get(s, s)), num(x["diff"]), f"[{num(x['ci_low'])}; {num(x['ci_high'])}]",
                 "да" if x["significant"] else "нет") for s, x in pv.items() if s in ("ours", "ours_gold", "ours_noq", "b0", "b1", "b3")])
    ours_b2 = pv["ours"]
    L += [""]
    if not (ours_b2["significant"] and (ours_b2["diff"] or 0) > 0):
        worse = (ours_b2["diff"] or 0) < 0
        L += [("**По оракулу наш подбор не обгоняет B2 — так и пишем"
               + (f": численно он даже ниже на {num(-(ours_b2['diff'] or 0))}, хотя разница незначима"
                  if worse and not ours_b2["significant"] else "")
               + (f": он ниже на {num(-(ours_b2['diff'] or 0))}, и разница значима" if worse and ours_b2["significant"] else "")
               + ".** Оракул видит только грейд, стек и деньги; B2 сортирует основную категорию ровно по тому, что оракул "
               "вознаграждает сильнее всего (уровень), а наша формула добавляет то, чего оракул не видит: формат работы, "
               "свежесть задач и ФСП. Где формула выигрывает — ниже: устойчивость к накрутке навыков и к пересдаче (4.4), "
               "проверяемые объяснения (4.6); где проигрывает — разобрано в диагностике выше и в 4.5."), ""]
    te = m.get("top10_errors")
    if te:
        L += ["**Откуда нерелевантные места в топ-10** (описательная диагностика после прогона, порогов у неё нет):", ""]
        reasons = list(dict.fromkeys([*te["ours"]["reasons"], *te["b2"]["reasons"]]))
        L += table(["Причина по оракулу", "Наш подбор", "B2"],
                   [(te["labels"].get(k, k), te["ours"]["reasons"].get(k, 0), te["b2"]["reasons"].get(k, 0)) for k in reasons]
                   + [("пустые места (в категориях меньше 10 кандидатов)", te["ours"]["empty_slots"], te["b2"]["empty_slots"]),
                      ("всего нерелевантных мест из " + str(te["slots"]), te["ours"]["irrelevant"] + te["ours"]["empty_slots"],
                       te["b2"]["irrelevant"] + te["b2"]["empty_slots"])])
        ranked_reasons = list(te["ours"]["reasons"])
        top_reason = ranked_reasons[0] if ranked_reasons else None
        if "grade_above" in ranked_reasons[:2]:
            L += ["", "Два главных источника: (1) must-навыки, которые тест не проверяет (Docker, Kafka, Redis и т. п.): "
                  "оракул знает истинный стек, а формула по построению видит только доказательства — тест, задачи, ФСП — и "
                  "не верит самоописанию; (2) кандидаты на ступень выше брифа: в категорию попадают сильные люди, заявившие "
                  "грейд ниже своего и не повысившие его, а внутри категории Score растёт вместе с результатом теста. Оракул "
                  "считает «переросших» релевантными, только если покрыты все must. Это расхождение формулы и оракула, а не "
                  "ошибка движка; вопрос передан владельцу подбора (штрафовать ли «переросших» внутри категории, как "
                  "показывать непроверяемые must).", ""]
        elif top_reason is not None:
            L += ["", f"Главный источник нерелевантных мест: {te['labels'].get(top_reason, top_reason)}.", ""]
    L += ["### 4.2. Абляции", "", "Снятый компонент отдаёт вес тесту (ограничения на веса соблюдены). «Без нижней границы» — "
          "ранжирование по точечной оценке уровня (SE = 0, без старения).", ""]
    L += table(["Вариант", "P@10", "nDCG@10", "Разница P@10 с полной формулой", "95% ДИ"],
               [(m["ablations"][a], ci(test["systems"][a]["p10"]), ci(test["systems"][a]["ndcg10"]),
                 num(-(test["paired_vs_ours"][a]["diff"] or 0)),
                 f"[{num(-(test['paired_vs_ours'][a]['ci_high'] or 0))}; {num(-(test['paired_vs_ours'][a]['ci_low'] or 0))}]")
                for a in m["ablations"]])
    L += ["", "### 4.3. Разбор брифа", ""]
    pt, pd = m["parser"]["test"], m["parser"]["dev"]
    L += table(["Метрика", "test", "dev (справочно)", "Цель"], [
        ("Специализация top-1", num(pt["spec_top1"]), num(pd["spec_top1"]), "≥ 0,90"),
        ("Грейд точно / ±1", f"{num(pt['grade_exact'])} / {num(pt['grade_within1'])}", f"{num(pd['grade_exact'])} / {num(pd['grade_within1'])}", "≥ 0,75 / ≥ 0,95"),
        ("Навыки: точность / полнота / F1", f"{num(pt['skills']['precision'])} / {num(pt['skills']['recall'])} / {num(pt['skills']['f1'])}",
         f"{num(pd['skills']['precision'])} / {num(pd['skills']['recall'])} / {num(pd['skills']['f1'])}", "F1 ≥ 0,85"),
        ("must: F1", num(pt["must"]["f1"]), num(pd["must"]["f1"]), "—"),
        ("must/nice определены верно (среди найденных)", num(pt["must_nice_accuracy"]), num(pd["must_nice_accuracy"]), "≥ 0,80"),
        ("Верная категория в топ-3", num(pt["category_top3"]), num(pd["category_top3"]), "≥ 0,95"),
        ("Вилка: точно от и до / на руки или до НДФЛ", f"{num(pt['salary_exact'])} / {num(pt['salary_basis_accuracy'])}",
         f"{num(pd['salary_exact'])} / {num(pd['salary_basis_accuracy'])}", "—"),
        ("Формат точно / город", f"{num(pt['formats_exact'])} / {num(pt['city_accuracy'])}", f"{num(pd['formats_exact'])} / {num(pd['city_accuracy'])}", "—"),
        ("Задан уточняющий вопрос", num(pt["question_rate"]), num(pd["question_rate"]), "—"),
    ])
    errs = [x for x in pt["rows"] if not x["spec_ok"] or x["grade"] != x["gold_grade"] or x["missed"] or x["kind_errors"]]
    if errs:
        L += ["", "Ошибки парсера на test-брифах:", ""]
        L += table(["Бриф", "Стиль", "Специализация", "Грейд (парсер / эталон)", "Пропущены навыки", "Лишние", "Ошибка must/nice"],
                   [(x["id"], x["style"], "верно" if x["spec_ok"] else f"ошибка ({x['parsed_spec']})", f"{x['grade']} / {x['gold_grade']}",
                     ", ".join(x["missed"]) or "—", ", ".join(x["extra"]) or "—", ", ".join(x["kind_errors"]) or "—") for x in errs])
    rb = m["robustness"]
    st, stab, jac, wt = rb["stuffing"], rb.get("stability") or {}, rb["nice_skill_jaccard10"], rb["weights"]
    into = st["ours_share_into_top10"] or 0
    into_txt = ("ни один не попал в топ-10" if into == 0 else
                f"в топ-10 оказались {pct(into, 1)} — они были там и до накрутки")
    L += ["", "### 4.4. Устойчивость", "",
          f"**Накрутка навыков.** В каждом test-брифе 50 самым слабым кандидатам выдачи добавили в самоописание 5 навыков "
          f"брифа ({st['stuffers']} случаев). Наш подбор: средний сдвиг вверх {num(st['ours_mean_shift_up'], 1)} позиции "
          f"(максимум {st['ours_max_shift_up']}), {into_txt}: заявленные навыки в Score не входят и служат только "
          f"последним ключом сортировки при равном Score (DECISIONS п.1). BM25 по самоописанию: сдвиг вверх "
          f"{num(st['bm25_mean_shift_up'], 1)} позиции, в топ-10 попали {pct(st['bm25_share_into_top10'], 1)}.", ""]
    L += [f"**Стабильность при пересдаче** (тест пересимулирован на непересекающихся семействах, overlap@10, "
          f"{stab.get('n_briefs', '—')} брифов): наш подбор {num(stab.get('ours_overlap10'))}, тот же подбор по точечной "
          f"оценке {num(stab.get('point_overlap10'))}, B2 «категория + точечная оценка» {num(stab.get('b2_overlap10'))}. "
          "Цель ≥ 0,70.", ""]
    if (stab.get("ours_overlap10") or 0) < 0.70:
        o, p_, b2 = (stab.get("ours_overlap10") or 0), (stab.get("point_overlap10") or 0), (stab.get("b2_overlap10") or 0)
        parts = []
        if o > b2 + 0.02:
            parts.append("компоненты K, A и U делают выдачу устойчивее, чем чистая сортировка по оценке уровня (B2)")
        if o > p_ + 0.02:
            parts.append("нижняя граница уровня добавляет устойчивости по сравнению с точечной оценкой")
        elif abs(o - p_) <= 0.02:
            parts.append("нижняя граница уровня по сравнению с точечной оценкой устойчивости не добавляет")
        detail = ("; ".join(parts) + ", но до 0,70 далеко. ") if parts else "До 0,70 далеко. "
        L += ["Порог не достигнут. В категории сотни кандидатов, а их уровни внутри полосы грейда различаются меньше, чем "
              "погрешность теста из ~15 заданий: состав первой десятки при пересдаче заметно меняется у любой формулы, "
              "опирающейся на тест. По этому прогону " + detail + "Что делаем в продукте: версия подборки неизменна и "
              "меняется только по действию работодателя, грейд защищён гистерезисом; порог 0,70 был оценкой концепции, "
              "его не меняем.", ""]
    L += [f"**Добавлен nice-навык:** Жаккар топ-10 до и после — {num(jac['mean'])} в среднем, минимум {num(jac['min'])}.", ""]
    L += [f"**Веса ±0,05** (компенсация пропорционально остальным; если вес упирается в ограничение wF ≤ 0,15 или "
          f"wT ≥ 0,30, он остаётся на месте). P@10 при весах «Баланс» — {num(wt['base_p10'])}.", ""]
    L += table(["Компонент", "Сдвиг", "P@10", "Падение P@10"],
               [(x["component"], ("+" if x["delta"] > 0 else "−") + "0,05", "пропущен" if x["skipped"] else num(x["p10"]),
                 "—" if x["skipped"] else num(x["drop"])) for x in wt["rows"]])
    ex = m["exposure"]
    L += ["", "### 4.5. Экспозиция кандидатов без ФСП (DECISIONS п.11)", "",
          "Условное отношение: наблюдаемое число релевантных кандидатов без ФСП в топ-k к ожидаемому, если бы они попадали "
          "в топ-k так же часто, как все релевантные **того же дециля компонента T** (при равном тесте). 1,0 — паритет; "
          "цель 0,8–1,25. Децили считаются по всем кандидатам рекомендованных категорий брифа, а не только по 500 "
          "строкам версии. Безусловное отношение (доля без ФСП в топе к их доле среди релевантных) — справочно.", ""]
    L += table(["k", "Условное", "Безусловное", "Наблюдаемо / ожидаемо"],
               [(k[1:], num(ex[k]["conditional"]), num(ex[k]["unconditional"]),
                 f"{num(ex[k]['observed'], 0)} / {num(ex[k]['expected'], 1)}") for k in ("k10", "k50")])
    L += ["", f"Доля кандидатов без ФСП среди релевантных — {pct(ex['share_no_fsp_relevant'])}.", ""]
    c10 = ex["k10"]["conditional"]
    if c10 is not None and c10 < 0.8:
        abl = get(m, "test.systems.no_F.p10.mean")
        L += [f"**В топ-10 порог не выполнен ({num(c10)}).** При равном тесте компонент F (до 15 баллов) поднимает "
              "обладателей ФСП над такими же кандидатами без ФСП, и в самой верхушке это заметно сильнее, чем в топ-50. "
              "Отсутствие ФСП — не штраф (F = 0, в «против» не попадает), но конкуренция за 10 мест — игра с нулевой "
              f"суммой. Абляция «без F» даёт P@10 {num(abl)} против {num(get(m, 'test.systems.ours.p10.mean'))} у полной "
              "формулы" + (": по оракулу ФСП не добавляет релевантности сверх теста"
                          if (abl or 0) > (get(m, "test.systems.ours.p10.mean") or 0) else "")
              + ". Вопрос о весе wF передан владельцу подбора; веса на test-брифах не подбираем.", ""]
    fi = m["fidelity"]
    L += ["### 4.6. Честность объяснений", "",
          f"Проверено {fi['items']} карточек (топ-50 каждого test-брифа).", ""]
    rows = [("Σ вкладов = Score (± 0,1 после округления)", pct(fi["sum_equals_score"], 1)),
            ("Каждый довод «за» и «против» ссылается на существующее доказательство (попытка теста, модуль, задача, запись ФСП, "
             "настройки, навык брифа)", pct(fi["evidence_exists"], 1)),
            ("Отсутствие ФСП никогда не попадает в «против»", pct(fi["fsp_never_con"], 1)),
            ("«За» даны только компонентам выше медианы версии более чем на 1 балл", pct(fi["pros_above_median"], 1)),
            (f"Без доказательства довода №1 Score падает не меньше его превышения над медианой ({fi['counterfactual_checked']} проверок)",
             pct(fi["counterfactual_ok"], 1))]
    L += table(["Проверка", "Доля"], rows)
    by = fi.get("counterfactual_by_component") or {}
    if by:
        L += ["", "Контрфакт по компоненту довода №1: " + "; ".join(
            f"{k} — {v['ok']} из {v['n']}" for k, v in by.items()) + ". Контрфакт: T — теста нет; F — нет ФСП; "
            "K и A — убраны именно те модули, задачи и записи ФСП, на которые ссылается довод (у кандидата могут остаться "
            "другие доказательства того же навыка — тогда Score падает меньше). U — настройки, а не доказательство, не проверяется.", ""]
    bm = m["build_ms"]
    L += ["### 4.7. Производительность", "",
          f"Сборка версии подборки (Score + объяснения + ранги) на пуле из {num(bm['pool'])} подтверждённых кандидатов: "
          f"медиана {num(bm['p50'], 0)} мс, p95 {num(bm['p95'], 0)} мс, максимум {num(bm['max'], 0)} мс ({bm['n']} брифов). "
          "Цель p95 ≤ 300 мс. Замер — на машине разработчика параллельно с другими разделами прогона.", ""]
    per = m["per_brief"]
    L += ["### 4.8. По брифам (test)", ""]
    L += table(["Бриф", "Стиль", "Категория эталона", "Релевантных", "Категории подборки", "Вопрос", "P@10 наш", "P@10 B2", "P@10 B1"],
               [(bid, b["style"], f"{b['spec']} · {b['grade']}", b["n_relevant"],
                 "; ".join(f"{c[0]} · {c[1]}" for c in b["categories"]), b["question"] or "—",
                 num(b["metrics"]["ours"]["p10"], 1), num(b["metrics"]["b2"]["p10"], 1), num(b["metrics"]["b1"]["p10"], 1))
                for bid, b in sorted(per.items()) if b["split"] == "test"])
    dev = m["dev"]["systems"]
    L += ["", "### 4.9. dev-брифы (справочно)", ""]
    L += table(["Система", "P@10", "nDCG@10"], [(labels[s], ci(dev[s]["p10"]), ci(dev[s]["ndcg10"])) for s in order])
    L += [""]
    return L


def _pairs_section(r: Mapping[str, Any]) -> list[str]:
    p = r.get("pairs")
    L = ["## 5. Набор пар для проверки жюри", ""]
    if not p:
        return L + ["В этом прогоне набор пар не выгружался (быстрый режим или `--no-pairs`).", ""]
    L += [f"`{p['file']}` — {num(p['lines'])} строк: для каждого из {p['briefs']} test-брифов пул из объединения "
          "топ-20 систем (наш сквозной, B0, B1, B2, B3) и 10 случайных кандидатов. Одна строка — одна пара «бриф × "
          f"кандидат» в JSON. Схема — `{p['schema']}` (JSON Schema 2020-12). Кандидаты вне пула при оценке считаются "
          "нерелевантными (обычный пулинг); для P@10 и P@5 глубины 20 хватает.", "",
          "Поля строки:", "",
          "- `brief_id`, `brief.text`, `brief.form` (вилка из обязательной формы, если её нет в тексте), `brief.gold` — "
          "эталон: специализация, грейд, must, nice, форматы, город, вилка;",
          "- `candidate_id`, `candidate.features` — слепые признаки в форме `app.matching.scoring.CandidateFeatures` "
          "(категория, оценка уровня и SE, подбаллы модулей с тегами, задачи, VSS по дисциплинам ФСП, заявленные навыки, "
          "минимум зарплаты, форматы, город); `null`, если категория тестом не подтверждена; `candidate.self_description` — "
          "самоописание (в Score не входит, нужно только для B1/B3);",
          "- `rel` — релевантность 0–3 по оракулу (релевантен ⇔ rel ≥ 2); `ranks_top20` — место в топ-20 каждой системы пула.",
          "",
          "Как пользоваться: ранжируйте кандидатов каждого брифа своей системой (или нашей — порядок в `ranks_top20`) и "
          "посчитайте P@k по полю `rel`. Импорт этого файла через `POST /api/admin/import` и выдача через "
          "`POST /api/eval/rank` запланированы (DECISIONS п.5) и будут принимать ровно этот формат. Скрытой истины "
          "(истинный уровень, истинный стек) в файле нет — только итоговая метка оракула.", ""]
    return L


def _limits_section(r: Mapping[str, Any], failed: Sequence[Mapping[str, Any]]) -> list[str]:
    L = ["## 6. Честные ограничения", "",
         "- Синтетика проверяет процедуру, а не реальные сложности заданий. Ответы порождает модель Раша с разбросом "
         "клонов; нецикличных DINA-персон и живого пилота в этом прогоне нет, раздела «люди» нет.",
         "- Экспертной разметки пар нет: релевантность — оракул по истине генератора. Оракул и генератор писала одна команда, "
         "поэтому P@k проверяет согласованность механики с нашей же моделью мира, а не с мнением рекрутера.",
         "- Брифы и эталон к ним написаны командой с ИИ-ассистентом; 10 брифов от посторонних авторов, заявленные в "
         "концепции, не собраны. Разбиение dev/test есть, но авторы одни и те же.",
         "- Генератор V-CAT и веса классификатора опроса составлены одной командой — проверка частично круговая.",
         "- B3 «LLM под каждого» — симуляция с допущениями (разброс сложности σ = 0,5, доля битых ключей задана, а не "
         "измерена). B3 подбора — TF-IDF, а не эмбеддинги: модель эмбеддингов в прогоне не используется.",
         "- Знание «методов» частично обходит защиту: клоны закрывают слив ответов, но не слив идеи семейства; это измерено "
         "в 2.6 и ловится CUSUM лишь при массовом сливе.",
         "- От ИИ-ассистента удалённый тест не защищён, как любой удалённый тест; грейд — решение с уровнем доверия, а не "
         "сертификат.",
         "- Точное совпадение грейда при пересдаче не может быть близким к 1 при 4–5 уровнях и ~15 заданиях; стабильность "
         "для пользователя обеспечивает гистерезис.",
         "- В банке нет pretest-семейств: r_pb считается по боевым заданиям с оценкой уровня без этого задания.",
         "- Fullstack как производная категория в популяции не порождается (каждый кандидат проходит один тест), поэтому "
         "Fullstack-брифы ищут в смежных категориях Backend и Frontend.",
         "- Формат работы и город оракул не учитывает, а наша формула учитывает (компонент U): часть «ошибок» по оракулу — "
         "это кандидаты, которых формула честно опустила из-за формата.",
         ""]
    if failed:
        L += ["Пороговые пункты, которые не выполнены в этом прогоне, перечислены в «Итогах»; пороги после прогона не "
              "менялись.", ""]
    return L
