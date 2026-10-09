"""Анализ пилота с ИИ-испытуемыми: прогоны backend/var/pilot_runs/*.json → таблицы и раздел «10» в docs/VALIDATION.md.

    cd backend
    uv run python -m eval.agent_pilot.analyze               # считает, печатает, пишет eval/out/agent_pilot.json и раздел в docs
    uv run python -m eval.agent_pilot.analyze --no-docs     # только печать и JSON

Анализ предрегистрирован в PROTOCOL.md (написан до прогонов): метрики и ориентиры не подбирались по результатам.
Без прогонов раздел содержит только предрегистрацию. Раздел вставляется между маркерами <!-- agent-pilot:begin/end -->
(после блока market-sim, перед «Как воспроизвести»), остальные генераторы (validate, fairness, market_sim) не затрагивает.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
import sys
from pathlib import Path
from typing import Any

from eval.agent_pilot.personas import GRADE_RANK, PERSONAS

BACKEND = Path(__file__).resolve().parents[2]
RUNS = BACKEND / "var" / "pilot_runs"
OUT_JSON = BACKEND / "eval" / "out" / "agent_pilot.json"
DOCS = BACKEND.parent / "docs" / "VALIDATION.md"
MARK_BEGIN, MARK_END = "<!-- agent-pilot:begin -->", "<!-- agent-pilot:end -->"
LEVELS = ["none", "intern", "junior", "middle", "senior"]
LEVEL_RU = {"none": "ниже Стажёра", "intern": "Стажёр", "junior": "Junior", "middle": "Middle", "senior": "Senior"}

# Ориентиры предрегистрации (PROTOCOL.md §4): заданы ДО прогонов, это не критерии «прошёл / не прошёл».
TARGETS = {"exact": 0.60, "within1": 0.90, "kappa_w": 0.60, "honest_false_alarms": 0, "cheater_detect": 0.50}
# Попытки «списывающих» с этого номера — пост-хок повтор после исправления выдачи двойников (VALIDATION §10.4): в
# предрегистрированные доли не входят и показываются отдельной таблицей.
POSTHOC_FROM_ATTEMPT = 3


# --------------------------------------------------------------------------- метрики


def band(run: dict[str, Any]) -> int | None:
    """Полоса грейда по баллу на линейке: высший грейд, чей порог ≤ балла (0 — ниже Стажёра). None — результата нет."""
    res = run.get("result") or {}
    score, ruler = res.get("score"), res.get("ruler") or []
    if score is None or not ruler:
        return None
    rank = 0
    for c in ruler:
        if score >= c["score"]:
            rank = max(rank, GRADE_RANK.get(c["grade"], 0))
    return rank


def passed(run: dict[str, Any]) -> bool | None:
    d = (run.get("result") or {}).get("decision")
    return None if d is None else bool(d.get("passed"))


def kappa(a: list[int], b: list[int], weighted: bool) -> float | None:
    """Каппа Коэна (при weighted — с линейными весами) по порядковым категориям 0..4."""
    n, k = len(a), len(LEVELS)
    if n == 0:
        return None
    obs = [[0.0] * k for _ in range(k)]
    for x, y in zip(a, b, strict=True):
        obs[x][y] += 1 / n
    ra = [sum(obs[i]) for i in range(k)]
    rb = [sum(obs[i][j] for i in range(k)) for j in range(k)]
    w = lambda i, j: (abs(i - j) / (k - 1)) if weighted else (0.0 if i == j else 1.0)  # noqa: E731
    d_obs = sum(w(i, j) * obs[i][j] for i in range(k) for j in range(k))
    d_exp = sum(w(i, j) * ra[i] * rb[j] for i in range(k) for j in range(k))
    return None if d_exp == 0 else 1 - d_obs / d_exp


def pearson(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or st.pstdev(x) == 0 or st.pstdev(y) == 0:
        return None
    mx, my = st.fmean(x), st.fmean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    return cov / (sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y)) ** 0.5


def load_runs(path: Path = RUNS) -> dict[str, dict[int, dict[str, Any]]]:
    runs: dict[str, dict[int, dict[str, Any]]] = {}
    for f in sorted(path.glob("*.json")) if path.exists() else []:
        r = json.loads(f.read_text(encoding="utf-8"))
        runs.setdefault(r["persona"], {})[int(r["attempt"])] = r
    return runs


def pct(x: float) -> str:
    return f"{x:.2f}".replace(".", ",")


def f2(x: float | None, nd: int = 2) -> str:
    return "—" if x is None else f"{x:.{nd}f}".replace(".", ",")


def analyze(runs: dict[str, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    honest = {p: r for p, r in runs.items() if PERSONAS[p].kind == "honest"}
    cheat = {p: r for p, r in runs.items() if PERSONAS[p].kind == "cheater"}
    res: dict[str, Any] = {"n_personas": len(runs), "n_runs": sum(len(v) for v in runs.values())}

    # 1. Повторный тест (честные персоны с обеими попытками и результатом)
    pairs = [(p, band(r[1]), band(r[2]), r[1], r[2]) for p, r in sorted(honest.items()) if 1 in r and 2 in r]
    pairs = [x for x in pairs if x[1] is not None and x[2] is not None]
    a = [x[1] for x in pairs]
    b = [x[2] for x in pairs]
    sa = [x[3]["result"]["score"] for x in pairs]
    sb = [x[4]["result"]["score"] for x in pairs]
    dec = [(passed(x[3]), passed(x[4])) for x in pairs]
    res["retest"] = {
        "n": len(pairs),
        "exact": (sum(x == y for x, y in zip(a, b, strict=True)) / len(pairs)) if pairs else None,
        "within1": (sum(abs(x - y) <= 1 for x, y in zip(a, b, strict=True)) / len(pairs)) if pairs else None,
        "kappa": kappa(a, b, False),
        "kappa_w": kappa(a, b, True),
        "decision_agree": (sum(d1 == d2 for d1, d2 in dec) / len(dec)) if dec else None,
        "score_mad": st.fmean(abs(x - y) for x, y in zip(sa, sb, strict=True)) if pairs else None,
        "score_r": pearson(sa, sb),
        "rows": [
            {"persona": x[0], "spec": PERSONAS[x[0]].spec, "intended": PERSONAS[x[0]].intended,
             "band1": LEVELS[x[1]], "band2": LEVELS[x[2]], "score1": x[3]["result"]["score"], "score2": x[4]["result"]["score"],
             "passed1": passed(x[3]), "passed2": passed(x[4])}
            for x in pairs
        ],
    }

    # 2. Уровень по тесту против задуманного (все попытки честных)
    conf: dict[str, dict[str, int]] = {g: {lv: 0 for lv in LEVELS} for g in ("junior", "middle", "senior")}
    bias: dict[str, list[int]] = {g: [] for g in conf}
    pass_rate: dict[str, list[bool]] = {g: [] for g in conf}
    for p, r in honest.items():
        for run in r.values():
            bd = band(run)
            if bd is None:
                continue
            g = PERSONAS[p].intended
            conf[g][LEVELS[bd]] += 1
            bias[g].append(bd - GRADE_RANK[g])
            pv = passed(run)
            if pv is not None:
                pass_rate[g].append(pv)
    res["confusion"] = conf
    res["bias"] = {g: (st.fmean(v) if v else None) for g, v in bias.items()}
    res["pass_rate"] = {g: (sum(v) / len(v) if v else None, len(v)) for g, v in pass_rate.items()}
    res["exact_level"] = {g: (sum(x == 0 for x in v) / len(v) if v else None) for g, v in bias.items()}

    # 3. Длина и время
    lens: dict[str, list[int]] = {g: [] for g in ("junior", "middle", "senior")}
    wall: list[float] = []
    n_to = n_skip = n_items = 0
    acc: dict[str, list[float]] = {g: [] for g in lens}
    for p, r in honest.items():
        for run in r.values():
            lens[PERSONAS[p].intended].append(len(run["items"]))
            for it in run["items"]:
                n_items += 1
                wall.append(float(it["wall_s"]))
                n_to += bool(it["timed_out"])
                n_skip += bool(it["skipped"])
            rs = [x for x in run["audit"]["responses"] if not x["is_pretest"] and x["is_correct"] is not None]
            if rs:
                acc[PERSONAS[p].intended].append(sum(bool(x["is_correct"]) for x in rs) / len(rs))
    res["items"] = {
        "by_level": {g: {"n_runs": len(v), "mean": st.fmean(v) if v else None, "min": min(v, default=None), "max": max(v, default=None)}
                     for g, v in lens.items()},
        "wall_median_s": st.median(wall) if wall else None, "timed_out": n_to, "skipped": n_skip, "answered": n_items,
        "accuracy": {g: (st.fmean(v) if v else None) for g, v in acc.items()},
    }

    # 4. Антиподсказчик
    fa_runs = sum(
        1 for r in honest.values() for run in r.values()
        if any(e["kind"] == "twin_trace" for e in run["audit"]["evidence"]) or run["audit"]["flags"]
    )
    res["honest_false_alarms"] = {"runs": sum(len(r) for r in honest.values()), "alarms": fa_runs}
    crows = []
    for p, r in sorted(cheat.items()):
        for at, run in sorted(r.items()):
            ev = run["audit"]["evidence"]
            kinds: dict[str, int] = {}
            for e in ev:
                kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
            copied = [i for i in run["items"] if i["used_clip"]]
            with_twin = [i for i in copied if i["clip_present"]]
            crows.append({
                "persona": p, "attempt": at, "claimed": run["claimed_grade"], "intended": run["intended"],
                "n_items": len(run["items"]), "copied": len(copied), "with_twin": len(with_twin),
                "evidence": kinds, "flags": [f"{f['kind']}/{f['severity']}" for f in run["audit"]["flags"]],
                "trust": run["audit"]["session"]["trust"], "status": run["audit"]["session"]["status"],
                "decision": (run.get("result") or {}).get("decision", {}).get("label") if (run.get("result") or {}).get("decision") else None,
                "band": LEVELS[band(run)] if band(run) is not None else None,
                "detected": bool(kinds.get("twin_trace") or run["audit"]["flags"] or run["audit"]["session"]["status"] == "UNDER_REVIEW"),
                # протокол §5.4: «удалось ли списывающему получить завышенную категорию» — подтверждён грейд выше задуманного
                "overrated": bool(passed(run)) and LEVELS.index(run["claimed_grade"]) > LEVELS.index(run["intended"]),
                "posthoc": at >= POSTHOC_FROM_ATTEMPT,
            })
    pre = [c for c in crows if not c["posthoc"]]
    post = [c for c in crows if c["posthoc"]]
    res["cheaters"] = crows
    res["cheater_detect_rate"] = (sum(c["detected"] for c in pre) / len(pre)) if pre else None
    res["cheater_overrated"] = {"pre": [sum(c["overrated"] for c in pre), len(pre)], "post": [sum(c["overrated"] for c in post), len(post)]}
    return res


# --------------------------------------------------------------------------- документ


def preregistration() -> str:
    t = TARGETS
    return (
        "### 10.1. Что и зачем (предрегистрация)\n\n"
        "Модель IRT-симуляции (раздел 2) проверяет движок на синтетических людях, которых мы же и описали. Пилот добавляет сквозное "
        "свидетельство: **Claude-агенты играют кандидатов с заданным профилем знаний и проходят настоящий адаптивный тест через "
        "настоящий HTTP API** (регистрация, опрос, выбор грейда, одноразовые клоны заданий, решение, экран результата), "
        "каждый — дважды, на двух аккаунтах (повторный тест подтверждённого грейда правилами пересдачи не предусмотрен, поэтому "
        "вторая попытка идёт со свежего аккаунта и с другими клонами). 12 честных персон (4 специализации × Junior/Middle/Senior) и "
        "2 «списывающих», которые копируют задание и решают его копию из буфера (проверка двойника-ловушки).\n\n"
        "Протокол, профили знаний и анализ описаны в `backend/eval/agent_pilot/PROTOCOL.md` и **зафиксированы до первого прогона**. "
        "Сессии пилота помечены `demo_assisted`: в калибровку, норму семейств, экспозицию и метрики не входят.\n\n"
        "**Честная рамка.** ИИ-испытуемые не заменяют людей. Языковой модели трудно «не знать»: она склонна отвечать лучше, чем "
        "отвечал бы живой Junior, поэтому **ожидаем завышение у младших персон** (и почти нулевое занижение у старших). "
        "Пилот не калибрует шкалу и не доказывает валидность на людях — он показывает, что весь путь работает, повтор "
        "воспроизводим, а ловушка срабатывает; что получилось, то и публикуем.\n\n"
        "**Предрегистрированные метрики** (ориентиры заданы до прогона и не используются как условие «прошёл / не прошёл»):\n\n"
        "| Метрика | Что считаем | Ориентир |\n|---|---|---|\n"
        f"| Согласие повторов | полоса грейда по баллу (0 — ниже Стажёра … 4 — Senior), точное совпадение | ≥ {t['exact']:.0%} |\n"
        f"| Согласие ±1 | различие не более чем на одну полосу | ≥ {t['within1']:.0%} |\n"
        f"| Каппа Коэна | взвешенная (линейные веса) и обычная по полосам | взвешенная ≥ {t['kappa_w']:.2f} |\n"
        "| Балл | средний модуль разности баллов повторов, корреляция Пирсона | без порога |\n"
        "| Уровень против задуманного | матрица «задуман × получен», смещение (получен − задуман) по уровням | без порога; ожидаем + у Junior |\n"
        "| Длина и время | число заданий по уровням, медиана секунд на задание, таймауты | без порога (у агентов время не человеческое) |\n"
        f"| Ложные тревоги | честные прогоны с уликой-«следом двойника» или флагом | {t['honest_false_alarms']} |\n"
        f"| Ловля списывающих | доля прогонов «списывающих» с уликой `twin_trace`, флагом или проверкой методиста | ≥ {t['cheater_detect']:.0%} |\n"
    )


def render(res: dict[str, Any] | None) -> str:
    out = ["## 10. Пилот с ИИ-испытуемыми", "", preregistration()]
    if not res or not res["n_runs"]:
        out += ["### 10.2. Результаты", "", "Прогоны ещё не выполнены: таблицы появятся после `uv run python -m eval.agent_pilot.analyze`.", ""]
        return "\n".join(out)
    r = res["retest"]
    out += [
        "### 10.2. Результаты", "",
        f"Прогонов: **{res['n_runs']}**, персон: **{res['n_personas']}**; пар повторов у честных персон с результатом: **{r['n']}**.", "",
        "**Повтор теста (test-retest), честные персоны**", "",
        "| Метрика | Значение | Ориентир |", "|---|---|---|",
        f"| Точное совпадение полосы | {f2(r['exact'])} | ≥ {pct(TARGETS['exact'])} |",
        f"| Различие не больше ±1 | {f2(r['within1'])} | ≥ {pct(TARGETS['within1'])} |",
        f"| Каппа Коэна (обычная) | {f2(r['kappa'])} | — |",
        f"| Каппа Коэна (взвешенная) | {f2(r['kappa_w'])} | ≥ {pct(TARGETS['kappa_w'])} |",
        f"| Совпадение решения «подтверждён / нет» | {f2(r['decision_agree'])} | — |",
        f"| Средний модуль разности баллов | {f2(r['score_mad'], 1)} | — |",
        f"| Корреляция баллов | {f2(r['score_r'])} | — |", "",
        "| Персона | Специализация | Задуман | Полоса 1 / 2 | Балл 1 / 2 | Решение 1 / 2 |", "|---|---|---|---|---|---|",
    ]
    for x in r["rows"]:
        yes = lambda v: "да" if v else "нет"  # noqa: E731
        out.append(
            f"| {x['persona']} | {x['spec']} | {LEVEL_RU[x['intended']]} | {LEVEL_RU[x['band1']]} / {LEVEL_RU[x['band2']]} "
            f"| {x['score1']} / {x['score2']} | {yes(x['passed1'])} / {yes(x['passed2'])} |"
        )
    out += ["", "**Полученный уровень против задуманного** (полоса по баллу, все попытки честных персон)", "",
            "| Задуман \\ получен | " + " | ".join(LEVEL_RU[lv] for lv in LEVELS) + " | смещение | совпало | подтверждён |",
            "|---|" + "---|" * (len(LEVELS) + 3)]
    for g in ("junior", "middle", "senior"):
        row = res["confusion"][g]
        pr = res["pass_rate"][g]
        out.append(
            f"| {LEVEL_RU[g]} | " + " | ".join(str(row[lv]) for lv in LEVELS)
            + f" | {f2(res['bias'][g])} | {f2(res['exact_level'][g])} | {f2(pr[0])} (n={pr[1]}) |"
        )
    it = res["items"]
    out += ["", "**Длина теста и время**", "", "| Уровень | Прогонов | Заданий (среднее, мин–макс) | Доля верных ответов |", "|---|---|---|---|"]
    for g in ("junior", "middle", "senior"):
        v = it["by_level"][g]
        out.append(f"| {LEVEL_RU[g]} | {v['n_runs']} | {f2(v['mean'], 1)} ({v['min'] if v['min'] is not None else '—'}–{v['max'] if v['max'] is not None else '—'}) | {f2(it['accuracy'][g])} |")
    out += ["", f"Ответов: {it['answered']}, таймаутов: {it['timed_out']}, пропусков: {it['skipped']}, медиана времени ответа агента: "
            f"{f2(it['wall_median_s'], 1)} с (у агента это не человеческое время, поэтому метрика справочная).", ""]
    fa = res["honest_false_alarms"]
    out += [f"**Ложные тревоги антиподсказчика:** {fa['alarms']} из {fa['runs']} честных прогонов (ориентир: {TARGETS['honest_false_alarms']}).", ""]
    def cheat_table(rows: list[dict[str, Any]]) -> list[str]:
        lines = ["| Персона | Заявлено / задуман | Заданий | Скопировано (с двойником) | Улики | Флаги | Статус | Решение | Поймана |",
                 "|---|---|---|---|---|---|---|---|---|"]
        for c in rows:
            ev = ", ".join(f"{k}: {v}" for k, v in sorted(c["evidence"].items())) or "—"
            lines.append(
                f"| {c['persona']} (поп. {c['attempt']}) | {c['claimed']} / {c['intended']} | {c['n_items']} | {c['copied']} ({c['with_twin']}) "
                f"| {ev} | {', '.join(c['flags']) or '—'} | {c['status']} | {c['decision'] or '—'} | {'да' if c['detected'] else 'нет'} |"
            )
        return lines

    pre = [c for c in res["cheaters"] if not c.get("posthoc")]
    post = [c for c in res["cheaters"] if c.get("posthoc")]
    ov = res.get("cheater_overrated") or {"pre": [0, len(pre)], "post": [0, len(post)]}
    if pre:
        out += ["**«Списывающие» персоны**", "", *cheat_table(pre), "",
                f"Доля пойманных прогонов: **{f2(res['cheater_detect_rate'])}** (ориентир ≥ {pct(TARGETS['cheater_detect'])}). "
                "«Поймана» значит, что улика записана в акт сверки для методиста; на решение теста улика не влияет (вес 0 до калибровки ε). "
                f"**Завышенную категорию (подтверждён заявленный Middle при задуманном Junior) получили {ov['pre'][0]} из {ov['pre'][1]} прогонов** — "
                "двойник доставался только первым четырём заданиям сессии, а дальше копирование в ИИ проходило без последствий.", ""]
    post_lines: list[str] = []
    if post:
        post_lines += ["### 10.4. Повтор «списывающих» после исправления (пост-хок)", "",
                "Не входит в предрегистрированные доли выше. По итогам 10.2 изменена выдача двойников: двойник ставится на каждое задание, "
                "для которого он посчитан (`evidence.twin_rate_per_session: 20` вместо 4), а предрасчёт двойников встроен в конвейер пула. "
                "Те же персоны и инструкции, свежие аккаунты и клоны; честных персон это изменение не касается (они отвечают по экрану) "
                "и они не перепрогонялись. В пилоте двойники достраиваются в момент выдачи (запас 20 с, PROTOCOL.md §2), на стенде — "
                "предрасчётом в конвейере пула и выдачей клона с готовым двойником.", "",
                *cheat_table(post), "",
                f"Завышенную категорию получили **{ov['post'][0]} из {ov['post'][1]}** прогонов (до исправления — {ov['pre'][0]} из {ov['pre'][1]}).", ""]
    out += ["### 10.3. Как читать", "",
            "Результаты — на малой выборке (12 честных персон, 2 списывающих) и на агентах, а не на людях: доверительные интервалы "
            "не строились, выводов о валидности на людях делать нельзя. Если ориентир не достигнут, это записано как есть. "
            "Пилот повторяется командами из `PROTOCOL.md`; сырые прогоны лежат в `backend/var/pilot_runs/` (в git не входят).", ""]
    out += post_lines  # пост-хок — после «Как читать»: предрегистрированная часть раздела идёт целиком первой
    return "\n".join(out)


def splice_docs(section: str, path: Path = DOCS) -> bool:
    """Раздел 10 между маркерами: старый блок убирается, новый ставится после блока market-sim (раздел 9), иначе перед
    «Как воспроизвести». Так порядок 8 → 9 → 10 держится при любой очерёдности запусков validate / market_sim / analyze."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = f"{MARK_BEGIN}\n{section.rstrip()}\n{MARK_END}\n"
    if MARK_BEGIN in text and MARK_END in text:
        head, rest = text.split(MARK_BEGIN, 1)
        _, tail = rest.split(MARK_END, 1)
        text = head + tail.lstrip("\n")
    ms_end = "<!-- market-sim:end -->"
    if ms_end in text:
        head, tail = text.split(ms_end, 1)
        new = head + ms_end + "\n\n" + block + "\n" + tail.lstrip("\n")
    elif "## Как воспроизвести" in text:
        head, tail = text.split("## Как воспроизвести", 1)
        new = head + block + "\n## Как воспроизвести" + tail
    else:
        return False
    path.write_text(new, encoding="utf-8", newline="\n")
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=Path, default=RUNS)
    ap.add_argument("--no-docs", action="store_true")
    ap.add_argument("--docs", type=Path, default=DOCS)
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    runs = load_runs(args.runs)
    res = analyze(runs) if runs else None
    section = render(res)
    if res:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(section if res else "Прогонов нет: в раздел идёт только предрегистрация.")
    if not args.no_docs:
        print("docs:", "обновлён" if splice_docs(section, args.docs) else "не найден якорь/файл", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
