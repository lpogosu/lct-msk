"""V-CAT: корректность категоризации по специализации (spec-testing §6.1, концепция §7.1).

300 синтетических профилей опроса (10 специализаций × 30) и 30 трудных случаев. Ответы порождает
СВОЯ матрица P(частота действия | специализация) и P(технология | специализация) ниже — она написана
отдельно от весов W/V классификатора в config/testing.yaml. Классифицирует прод-функция
`app.testing.survey.classify`; метрика — macro-F1 её подсказки (top) против истинной специализации.

Честная оговорка: генератор и веса W/V писала одна команда, поэтому проверка частично круговая.
"""

from __future__ import annotations

import inspect
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from app.core.configs import load_config
from app.testing.survey import SurveyAnswers, classify, load_survey_config
from eval import metrics as M
from eval.common import MASTER_SEED

# Действия «руками за 12 месяцев»: ядро (обычно «регулярно») и смежные (обычно «иногда»).
CORE_ACTIONS: dict[str, tuple[str, ...]] = {
    "backend_python": ("server_api", "sql_db"),
    "backend_go": ("server_api", "sql_db"),
    "backend_java": ("server_api", "sql_db"),
    "frontend_js": ("ui_layout", "spa_components", "browser_debug"),
    "fullstack": ("server_api", "spa_components", "ui_layout", "sql_db"),
    "data_ml": ("data_analysis", "ml_models"),
    "devops": ("ci_cd", "containers_infra", "monitoring"),
    "qa": ("manual_testing", "autotests"),
    "mobile": ("mobile_app",),
    "infosec": ("security",),
}
SIDE_ACTIONS: dict[str, tuple[str, ...]] = {
    "backend_python": ("queues_cache", "algorithms", "containers_infra", "autotests", "ci_cd", "monitoring"),
    "backend_go": ("queues_cache", "containers_infra", "monitoring", "algorithms", "ci_cd"),
    "backend_java": ("queues_cache", "algorithms", "autotests", "ci_cd", "monitoring"),
    "frontend_js": ("autotests", "algorithms", "server_api"),
    "fullstack": ("browser_debug", "containers_infra", "ci_cd", "queues_cache"),
    "data_ml": ("sql_db", "algorithms", "server_api"),
    "devops": ("server_api", "sql_db", "security", "autotests"),
    "qa": ("browser_debug", "ci_cd", "sql_db", "server_api"),
    "mobile": ("ui_layout", "server_api", "autotests"),
    "infosec": ("monitoring", "containers_infra", "algorithms", "server_api"),
}
# Технологии: ядро («использую сейчас / в последний год») и смежные (давность любая).
CORE_TECH: dict[str, tuple[tuple[str, ...], ...]] = {
    # Кортеж кортежей: из каждой группы берётся одна технология (например, React ИЛИ Vue).
    "backend_python": (("python",), ("django_fastapi",), ("postgresql",)),
    "backend_go": (("go",), ("postgresql",)),
    "backend_java": (("java_kotlin",), ("postgresql",)),
    "frontend_js": (("javascript",), ("typescript", "javascript"), ("react", "react", "vue_angular"), ("html_css",)),
    "fullstack": (("javascript", "typescript"), ("react", "vue_angular"), ("python", "nodejs"), ("postgresql",), ("html_css",)),
    "data_ml": (("python",), ("pandas_numpy",), ("ml_frameworks",)),
    "devops": (("docker",), ("kubernetes",), ("linux_git",)),
    "qa": (("test_frameworks",),),
    "mobile": (("mobile_sdk",),),
    "infosec": (("security_tools",), ("linux_git",)),
}
SIDE_TECH: dict[str, tuple[str, ...]] = {
    "backend_python": ("docker", "linux_git", "test_frameworks", "pandas_numpy"),
    "backend_go": ("docker", "kubernetes", "linux_git", "python"),
    "backend_java": ("docker", "linux_git", "kubernetes"),
    "frontend_js": ("nodejs", "test_frameworks", "docker"),
    "fullstack": ("typescript", "docker", "django_fastapi", "linux_git"),
    "data_ml": ("postgresql", "docker", "linux_git"),
    "devops": ("python", "go", "postgresql"),
    "qa": ("python", "javascript", "postgresql", "docker"),
    "mobile": ("java_kotlin", "javascript", "typescript"),
    "infosec": ("python", "docker", "go"),
}
# Соседние специализации — для «амбициозных» отметок и для ошибочного выбора кандидата.
NEIGHBORS: dict[str, tuple[str, ...]] = {
    "backend_python": ("fullstack", "data_ml", "backend_go"),
    "backend_go": ("backend_python", "devops"),
    "backend_java": ("backend_python", "mobile"),
    "frontend_js": ("fullstack", "mobile"),
    "fullstack": ("backend_python", "frontend_js"),
    "data_ml": ("backend_python",),
    "devops": ("backend_go", "backend_python"),
    "qa": ("frontend_js", "backend_python"),
    "mobile": ("frontend_js",),
    "infosec": ("devops",),
}
P_CORE = (0.80, 0.15)  # (регулярно, иначе иногда) для ядра
P_SIDE = (0.15, 0.45)
P_RARE = (0.02, 0.08)
NOISE = 0.10  # доля ответов, переразыгранных равномерно
AMBITIOUS = 0.15  # доля профилей с отметками действий соседней специализации
CHOICE_TRUE = 0.80  # выбор кандидата совпадает с истиной
RECENCY_CORE = (("now", 0.6), ("last_year", 0.3), ("one_three_years", 0.1))
RECENCY_SIDE = (("now", 0.2), ("last_year", 0.3), ("one_three_years", 0.3), ("older", 0.2))
FREQ = ("never", "sometimes", "regularly")


@dataclass(frozen=True)
class Profile:
    pid: str
    true_spec: str
    kind: str  # regular | fullstack | career_change | contradictory
    answers: SurveyAnswers


def specializations() -> list[str]:
    # Только направления, для которых описан синтетический профиль опроса. Новые survey_only-специализации
    # (например, uiux_design: классификатор опроса её не предлагает, тест в дорожной карте) в прогон не входят.
    return [s["code"] for s in load_config("taxonomy")["specializations"] if s["code"] in CORE_ACTIONS]


def _all_actions() -> list[str]:
    return [a["code"] for a in load_config("testing")["survey"]["actions"]]


def _pick(rng: random.Random, weighted: Sequence[tuple[str, float]]) -> str:
    u, acc = rng.random() * sum(w for _, w in weighted), 0.0
    for x, w in weighted:
        acc += w
        if u < acc:
            return x
    return weighted[-1][0]


def _freq(rng: random.Random, p: tuple[float, float]) -> str:
    u = rng.random()
    return "regularly" if u < p[0] else "sometimes" if u < p[0] + p[1] else "never"


def _actions_for(spec: str, rng: random.Random) -> dict[str, str]:
    out = {}
    for a in _all_actions():
        if a in CORE_ACTIONS[spec]:
            out[a] = _freq(rng, P_CORE)
        elif a in SIDE_ACTIONS[spec]:
            out[a] = _freq(rng, P_SIDE)
        else:
            out[a] = _freq(rng, P_RARE)
        if rng.random() < NOISE:
            out[a] = rng.choice(FREQ)
    return out


def _tech_for(spec: str, rng: random.Random) -> dict[str, str]:
    out: dict[str, str] = {}
    for group in CORE_TECH[spec]:
        out[rng.choice(group)] = _pick(rng, RECENCY_CORE)
    for t in SIDE_TECH[spec]:
        if rng.random() < 0.35:
            out.setdefault(t, _pick(rng, RECENCY_SIDE))
    return out


def regular_profile(spec: str, i: int, seed: int = MASTER_SEED) -> Profile:
    rng = random.Random(f"{seed}:vcat:{spec}:{i}")
    actions = _actions_for(spec, rng)
    tech = _tech_for(spec, rng)
    if rng.random() < AMBITIOUS:
        nb = rng.choice(NEIGHBORS[spec])
        for a in rng.sample(CORE_ACTIONS[nb], min(len(CORE_ACTIONS[nb]), rng.randint(1, 2))):
            actions[a] = rng.choice(("sometimes", "regularly"))
    chosen = spec if rng.random() < CHOICE_TRUE else rng.choice(NEIGHBORS[spec])
    return Profile(f"v-{spec}-{i:02d}", spec, "regular", SurveyAnswers(chosen, actions, tech, None))


def hard_cases(seed: int = MASTER_SEED) -> list[Profile]:
    """30 трудных случаев: 10 Fullstack, 10 смен профессии, 10 противоречивых опросов.

    - Fullstack: и серверные, и интерфейсные действия «регулярно»; выбор — Backend или Frontend в половине случаев.
    - Смена профессии: прежняя работа (QA, аналитика, поддержка) «регулярно», новое направление «иногда»;
      истина — новое направление, его же выбирает кандидат.
    - Противоречивые: выбор одной специализации, а действия и технологии — другой; истина — то, что человек
      делает руками (подсказка должна сработать).
    """
    out: list[Profile] = []
    for i in range(10):
        rng = random.Random(f"{seed}:vcat:hard:fs:{i}")
        actions = _actions_for("fullstack", rng)
        for a in ("server_api", "spa_components", "ui_layout"):
            actions[a] = "regularly"
        tech = _tech_for("fullstack", rng)
        chosen = "fullstack" if i < 5 else rng.choice(("backend_python", "frontend_js"))
        out.append(Profile(f"h-fullstack-{i:02d}", "fullstack", "fullstack", SurveyAnswers(chosen, actions, tech, None)))
    switches = [("qa", "backend_python"), ("data_ml", "backend_python"), ("qa", "frontend_js"), ("mobile", "frontend_js"),
                ("devops", "backend_go"), ("backend_python", "data_ml"), ("frontend_js", "fullstack"),
                ("infosec", "devops"), ("qa", "backend_java"), ("backend_java", "backend_go")]
    for i, (old, new) in enumerate(switches):
        rng = random.Random(f"{seed}:vcat:hard:cc:{i}")
        actions = _actions_for(old, rng)
        for a in CORE_ACTIONS[new]:
            actions[a] = "sometimes"
        tech = _tech_for(new, rng)
        for t, rec in _tech_for(old, rng).items():
            tech.setdefault(t, "one_three_years" if rec == "now" else rec)
        out.append(Profile(f"h-switch-{i:02d}", new, "career_change", SurveyAnswers(new, actions, tech, None)))
    pairs = [("backend_python", "frontend_js"), ("frontend_js", "backend_python"), ("devops", "backend_python"),
             ("data_ml", "backend_python"), ("qa", "frontend_js"), ("mobile", "frontend_js"),
             ("backend_go", "devops"), ("frontend_js", "fullstack"), ("infosec", "devops"), ("backend_java", "mobile")]
    for i, (true, chosen) in enumerate(pairs):
        rng = random.Random(f"{seed}:vcat:hard:cx:{i}")
        out.append(Profile(f"h-contra-{i:02d}", true, "contradictory",
                           SurveyAnswers(chosen, _actions_for(true, rng), _tech_for(true, rng), None)))
    return out


def generate(per_spec: int = 30, seed: int = MASTER_SEED) -> tuple[list[Profile], list[Profile]]:
    regular = [regular_profile(s, i, seed) for s in specializations() for i in range(per_spec)]
    return regular, hard_cases(seed)


def _confusion(truth: Sequence[str], pred: Sequence[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for t, p in zip(truth, pred, strict=True):
        out.setdefault(t, {}).setdefault(p, 0)
        out[t][p] += 1
    return out


def tuning_hint(regular: Sequence[Profile], specs: Sequence[str]) -> dict[str, Any]:
    """Подсказка владельцу config/testing.yaml (в прод-конфиг НЕ вносится): сетка tech_weight × choice_bonus,
    подобрана на чётных профилях V-CAT, проверена на нечётных. Температура на top-1 не влияет."""
    base = load_survey_config()
    dev, test = list(regular[0::2]), list(regular[1::2])

    def f1(cfg: Any, rows: Sequence[Profile]) -> float:
        return M.macro_f1([p.true_spec for p in rows], [classify(p.answers, cfg).top for p in rows], specs)

    grid = [(tw, cb) for tw in (0.3, 0.5, 0.8, 1.2) for cb in (0.2, 0.3, 0.5, 0.8)]
    scored = [(f1(replace(base, tech_weight=tw, choice_bonus=cb), dev), tw, cb) for tw, cb in grid]
    # Лучший на dev; при равенстве — ближайший к текущим значениям.
    best = max(scored, key=lambda s: (round(s[0], 6), -abs(s[1] - base.tech_weight) - abs(s[2] - base.choice_bonus)))
    cfg_best = replace(base, tech_weight=best[1], choice_bonus=best[2])
    return {
        "current": {"tech_weight": base.tech_weight, "choice_bonus": base.choice_bonus,
                    "macro_f1_dev": f1(base, dev), "macro_f1_test": f1(base, test)},
        "best": {"tech_weight": best[1], "choice_bonus": best[2], "macro_f1_dev": best[0],
                 "macro_f1_test": f1(cfg_best, test)},
        "grid": [{"tech_weight": tw, "choice_bonus": cb, "macro_f1_dev": v} for v, tw, cb in scored],
    }


def evaluate(per_spec: int = 30, seed: int = MASTER_SEED) -> dict[str, Any]:
    """macro-F1 подсказки классификатора, бейзлайн «выбор кандидата», трудные случаи."""
    regular, hard = generate(per_spec, seed)
    specs = specializations()
    names = {s["code"]: s["name"] for s in load_config("taxonomy")["specializations"]}
    res = [classify(p.answers) for p in regular]
    truth = [p.true_spec for p in regular]
    top = [r.top for r in res]
    chosen = [p.answers.chosen_spec for p in regular]
    per_class = M.f1_scores(truth, top)
    hints = [(r, p) for r, p in zip(res, regular, strict=True) if r.hint]
    wrong_choice = [(r, p) for r, p in zip(res, regular, strict=True) if p.answers.chosen_spec != p.true_spec]
    hard_res = [(classify(p.answers), p) for p in hard]
    by_kind: dict[str, dict[str, Any]] = {}
    for kind in ("fullstack", "career_change", "contradictory"):
        rows = [(r, p) for r, p in hard_res if p.kind == kind]
        by_kind[kind] = {
            "n": len(rows),
            "top_accuracy": M.mean([float(r.top == p.true_spec) for r, p in rows]),
            "hint_shown": M.mean([float(r.hint) for r, _ in rows]),
            "hint_correct": M.mean([float(r.top == p.true_spec) for r, p in rows if r.hint]) if any(r.hint for r, _ in rows) else None,
        }
    return {
        "n_profiles": len(regular),
        "n_hard": len(hard),
        "specializations": specs,
        "spec_names": names,
        "macro_f1": M.macro_f1(truth, top, specs),
        "accuracy": M.agreement(truth, top),
        "baseline_choice_macro_f1": M.macro_f1(truth, chosen, specs),
        "baseline_choice_accuracy": M.agreement(truth, chosen),
        "per_class": {str(k): {kk: vv for kk, vv in v.items()} for k, v in per_class.items() if k in specs},
        "confusion": _confusion(truth, top),
        "hint_rate": len(hints) / len(regular) if regular else None,
        "hint_precision": M.mean([float(r.top == p.true_spec) for r, p in hints]) if hints else None,
        "wrong_choice_hint_recall": M.mean([float(r.hint and r.top == p.true_spec) for r, p in wrong_choice])
        if wrong_choice else None,
        "hard": {
            "top_accuracy": M.mean([float(r.top == p.true_spec) for r, p in hard_res]),
            "by_kind": by_kind,
        },
        # Опрос и CAT не принимают на вход ничего из ФСП: категория без истории ФСП присваивается по тем же
        # правилам (проверяется и по популяции — доля подтверждённых с ФСП и без ФСП).
        "no_fsp_inputs_in_survey": _no_fsp_inputs(),
        "generator": {"noise": NOISE, "ambitious": AMBITIOUS, "choice_true": CHOICE_TRUE},
        "tuning_hint": tuning_hint(regular, specs),
    }


def _no_fsp_inputs() -> bool:
    """Ни опрос, ни создание сессии теста не принимают на вход ничего из ФСП (проверка по сигнатурам)."""
    from app.testing.session import TestSession

    names = list(SurveyAnswers.__dataclass_fields__) + list(inspect.signature(classify).parameters)
    names += list(inspect.signature(TestSession.create).parameters)
    return not any("fsp" in n.lower() or "vss" in n.lower() for n in names)


def survey_answers_to_dict(a: SurveyAnswers) -> Mapping[str, Any]:
    return {"chosen_spec": a.chosen_spec, "actions": dict(a.actions), "technologies": dict(a.technologies)}
