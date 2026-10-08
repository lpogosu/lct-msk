"""Расписания и конвейеры: ближайшие запуски cron и порядок стадий CI с needs.

Cron — точная модель vixie-cron: если ОБА поля «день месяца» и «день недели» ограничены (не начинаются с «*»), день
подходит при совпадении ЛЮБОГО из них (OR), иначе нужны оба; шаги `*/N` отсчитываются от начала диапазона заново в каждом
часе; день недели 0 и 7 — воскресенье. CI — самый длинный путь по DAG: job без needs ждёт все job предыдущих стадий,
job с needs — только перечисленные (needs: [] — старт сразу).
"""

from __future__ import annotations

import random
import re
from datetime import datetime, timedelta
from typing import Any

import yaml

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    OpsFamily,
    Scenario,
    ScenarioError,
    numeric_alternatives,
)

# ============================================================== cron

_FIELD_LIMITS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
_RU_DOW = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_CMDS = ["/usr/local/bin/backup.sh", "/opt/etl/run.sh", "/usr/bin/certbot renew", "/srv/app/cleanup.py", "/opt/report/send.sh",
         "/usr/local/bin/rotate-logs", "/srv/billing/close-day.sh"]
_NOW_RE = re.compile(r"сейчас:\s*(\d{4})-(\d{2})-(\d{2})\s*\(\S+\)\s*(\d{2}):(\d{2})")
_CRON_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)$")
_SEARCH_DAYS = 366 * 9


def parse_field(text: str, index: int, *, range_exclusive: bool = False, python_dow: bool = False) -> set[int]:
    lo, hi = _FIELD_LIMITS[index]
    out: set[int] = set()
    for item in text.split(","):
        step = 1
        if "/" in item:
            item, s = item.split("/", 1)
            step = int(s)
            if step < 1:
                raise ScenarioError("шаг меньше 1")
        if item == "*":
            a, b = lo, (6 if index == 4 else hi)
        elif "-" in item:
            a, b = (int(x) for x in item.split("-", 1))
            if range_exclusive:
                b -= 1
        else:
            a = b = int(item)
        if not (lo <= a <= b <= hi):
            raise ScenarioError(f"значение вне диапазона: {item}")
        out.update(range(a, b + 1, step))
    if index == 4 and not python_dow:
        out = {0 if v == 7 else v for v in out}
    return out


def parse_cron_case(code: str) -> dict[str, Any]:
    line = next((ln for ln in code.splitlines() if ln.strip() and not ln.lstrip().startswith("#")), None)
    m = _CRON_RE.match(line.strip()) if line else None
    now = _NOW_RE.search(code)
    if not m or not now:
        raise ScenarioError("не найдена строка crontab или «сейчас»")
    y, mo, d, h, mi = (int(x) for x in now.groups())
    return {"fields": list(m.groups()[:5]), "now": datetime(y, mo, d, h, mi)}


def next_runs(
    case: dict[str, Any],
    n: int = 3,
    *,
    strict: bool = True,
    force_and: bool = False,
    python_dow: bool = False,
    range_exclusive: bool = False,
) -> list[datetime]:
    f = case["fields"]
    sets = [parse_field(f[i], i, range_exclusive=range_exclusive, python_dow=(python_dow and i == 4)) for i in range(5)]
    dom_star, dow_star = f[2].startswith("*"), f[4].startswith("*")
    now: datetime = case["now"]
    out: list[datetime] = []
    first_day = now.replace(hour=0, minute=0)
    for offset in range(_SEARCH_DAYS):
        day = first_day + timedelta(days=offset)
        if day.month not in sets[3]:
            continue
        dow = day.weekday() if python_dow else (day.weekday() + 1) % 7
        dom_ok, dow_ok = day.day in sets[2], dow in sets[4]
        ok = (dom_ok and dow_ok) if (dom_star or dow_star or force_and) else (dom_ok or dow_ok)
        if not ok:
            continue
        for h in sorted(sets[1]):
            for mi in sorted(sets[0]):
                t = day.replace(hour=h, minute=mi)
                if t > now or (not strict and t == now):
                    out.append(t)
                    if len(out) == n:
                        return out
    raise ScenarioError("расписание не срабатывает")


def _fmt_runs(runs: list[datetime]) -> str:
    return " ; ".join(t.strftime("%Y-%m-%d %H:%M") for t in runs)


def _field_spans(code: str) -> list[tuple[int, int, str]]:
    """Диапазоны пяти полей строки crontab в тексте: (начало, конец, текст)."""
    pos = 0
    for line in code.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return [(pos + m.start(), pos + m.end(), m.group()) for m in re.finditer(r"\S+", line)][:5]
        pos += len(line)
    return []


@register
class CronNextRuns(OpsFamily):
    id = "ops.cron.next_runs"
    observes = ("cron", "linux")
    title = "cron: ближайшие запуски по выражению"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "middle"
    position = -0.3
    time_limit_s = 150
    is_anchor = True
    answer_kind = "datetimes"
    language = "text"
    measures = (
        "Чтение 5 полей cron: списки, диапазоны, шаги (*/N перезапускается каждый час), «или» для дня месяца и "
        "дня недели, строго позже текущего момента, календарь месяцев."
    )
    misconceptions = {
        "dom_dow_and": "если ограничены и день месяца, и день недели, cron запускает задачу в день, подходящий под "
        "ЛЮБОЕ из условий (или), а не под оба сразу",
        "include_now": "«ближайшие» запуски — строго после текущего момента: запуск в эту же минуту уже случился",
        "continuous_step": "*/N в минутах отсчитывается от начала каждого часа (0, N, 2N…), а не идёт непрерывным "
        "интервалом: после последнего кратного N запуск снова в :00",
        "dow_python": "в cron день недели 0 и 7 — воскресенье, 1 — понедельник (а не 0 — понедельник)",
        "range_exclusive": "диапазон a-b в cron включает правую границу",
    }
    answer_hint = "Введите три момента времени в формате ГГГГ-ММ-ДД ЧЧ:ММ через пробел или « ; », по возрастанию."

    @staticmethod
    def _minute(rng: random.Random) -> str:
        kind = rng.choice(["step", "step", "list", "single", "rangestep"])
        if kind == "step":
            return f"*/{rng.choice([5, 10, 15, 20, 30, 7, 25, 45])}"
        if kind == "list":
            return ",".join(str(x) for x in sorted(rng.sample(range(0, 60), rng.randint(2, 3))))
        if kind == "single":
            return str(rng.randint(2, 58))
        a = rng.randint(2, 20)
        return f"{a}-{rng.randint(30, 55)}/{rng.choice([5, 10, 15])}"

    @staticmethod
    def _hour(rng: random.Random) -> str:
        kind = rng.choice(["star", "single", "single", "range", "list", "step"])
        if kind == "star":
            return "*"
        if kind == "single":
            return str(rng.randint(2, 23))
        if kind == "range":
            a = rng.randint(2, 11)
            return f"{a}-{rng.randint(a + 3, 22)}"
        if kind == "list":
            return ",".join(str(x) for x in sorted(rng.sample(range(2, 24), rng.randint(2, 3))))
        return f"*/{rng.choice([2, 3, 4, 6, 8])}"

    def make(self, rng: random.Random) -> Scenario:
        or_case = rng.random() < 0.25
        pure = (not or_case) and rng.random() < 0.3
        if pure:
            fields = [f"*/{rng.choice([7, 11, 13, 17, 25, 40])}", "*", "*", "*", "*"]
        else:
            month = rng.choice(["*", "*", "*", "*/3", "3-9", "2,6,10"])
            if or_case:
                dom = rng.choice(["1", "15", "1,15", "10-14", "28"])
                dow = rng.choice(["1", "5", "0", "6,0", "1-3", "2,4"])
            elif rng.random() < 0.5:
                dom, dow = "*", rng.choice(["*", "1-5", "0", "6,0", "1,3,5", "2-6"])
            else:
                dom, dow = rng.choice(["1", "15", "1,15", "5-9", "28"]), "*"
            fields = [self._minute(rng), self._hour(rng), dom, month, dow]
        cmd = rng.choice(_CMDS)
        year, month_n, day_n = rng.choice([2026, 2027]), rng.randint(1, 12), rng.randint(1, 28)
        base = datetime(year, month_n, day_n, rng.randint(0, 23), rng.randint(0, 59))
        case = {"fields": fields, "now": base}
        now = base
        if rng.random() < 0.3:  # «сейчас» — ровно момент запуска: проверяет строгость «позже»
            now = next_runs(case, 1)[0]
            if now - base > timedelta(days=60):
                raise GenerationRetry
        code = (
            f"# crontab -l\n{' '.join(fields)}  {cmd}\n"
            f"# сейчас: {now:%Y-%m-%d} ({_RU_DOW[now.weekday()]}) {now:%H:%M}"
        )
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Демон cron (vixie-cron) работает по местному времени сервера без переходов на летнее время. "
            "Когда сработают три ближайших запуска показанной задачи после указанного момента?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        case = parse_cron_case(code)
        f = case["fields"]
        if mutant == "dom_dow_and":
            if f[2].startswith("*") or f[4].startswith("*"):
                return None
            return _fmt_runs(next_runs(case, force_and=True))
        if mutant == "include_now":
            return _fmt_runs(next_runs(case, strict=False))
        if mutant == "continuous_step":
            m = re.fullmatch(r"\*/(\d+)", f[0])
            if not m or any(x != "*" for x in f[1:]):
                return None
            first = next_runs(case, 1)[0]
            step = int(m.group(1))
            return _fmt_runs([first + timedelta(minutes=step * k) for k in range(3)])
        if mutant == "dow_python":
            return None if f[4] == "*" else _fmt_runs(next_runs(case, python_dow=True))
        if mutant == "range_exclusive":
            return _fmt_runs(next_runs(case, range_exclusive=True)) if any("-" in x for x in f) else None
        return _fmt_runs(next_runs(case))

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Поля: минута, час, день месяца, месяц, день недели. Список через запятую, диапазон включает обе границы, "
            "*/N — каждые N единиц от начала диапазона заново в каждом часе. Если ограничены и день месяца, и день "
            "недели, задача срабатывает в дни, подходящие под любое из условий. Запуск в текущую минуту уже прошёл, "
            "поэтому берём моменты строго позже «сейчас»."
        )

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        spans = [(a + m.start(), a + m.end()) for a, _b, text in _field_spans(code) for m in re.finditer(r"\d+", text)
                 if int(m.group()) >= 2]
        now = _NOW_RE.search(code)
        if now:
            spans += [now.span(4), now.span(5)]
        return spans

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: Any) -> list[str]:
        old = code[span[0] : span[1]]
        now = _NOW_RE.search(code)
        if now and span in (now.span(4), now.span(5)):
            hi = 23 if span == now.span(4) else 59
            return numeric_alternatives(old, rng, p, 0, hi, width=2)
        for i, (a, b, _t) in enumerate(_field_spans(code)):
            if a <= span[0] < b:
                return numeric_alternatives(old, rng, p, 2, [59, 23, 28, 12, 6][i])
        return []


# ============================================================== CI: стадии и needs


_JOBS = ["compile", "package", "lint", "unit", "integration", "e2e", "scan", "docs", "deploy_stage", "smoke",
         "deploy_prod", "migrate", "bench", "sign"]


def schedule_jobs(
    doc: dict[str, Any],
    *,
    ignore_needs: bool = False,
    plus_stage: bool = False,
    empty_as_none: bool = False,
) -> tuple[dict[str, int], dict[str, int]]:
    """Старт и завершение каждой job в минутах от начала пайплайна. Флаги — модели заблуждений."""
    stages: list[str] = doc["stages"]
    jobs = {k: v for k, v in doc.items() if k != "stages" and isinstance(v, dict) and "stage" in v}
    start: dict[str, int] = {}
    finish: dict[str, int] = {}
    order = list(jobs)

    def stage_barrier(name: str) -> int:
        idx = stages.index(jobs[name]["stage"])
        return max((finish[j] for j in jobs if j in finish and stages.index(jobs[j]["stage"]) < idx), default=0)

    # Обход по стадиям, внутри стадии — в порядке файла: зависимости всегда уже посчитаны.
    for name in sorted(order, key=lambda j: (stages.index(jobs[j]["stage"]), order.index(j))):
        job = jobs[name]
        needs = job.get("needs")
        if needs is not None and not ignore_needs and not (empty_as_none and not needs):
            s = max((finish[n] for n in needs), default=0)
            if plus_stage:
                s = max(s, stage_barrier(name))
        else:
            s = stage_barrier(name)
        start[name] = s
        finish[name] = s + int(job["time"])
    return start, finish


@register
class CiStages(OpsFamily):
    id = "ops.ci.stages"
    observes = ("ci_cd", "gitlab_ci", "github_actions")
    title = "CI: стадии, needs и время пайплайна"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "middle"
    position = 0.6
    time_limit_s = 150
    answer_kind = "ints"
    language = "text"
    measures = (
        "Порядок выполнения job: барьер между стадиями, needs как граф зависимостей (в том числе через стадии), "
        "needs: [] — старт сразу; критический путь."
    )
    misconceptions = {
        "needs_ignored": "job с needs не ждёт всю предыдущую стадию, а стартует сразу после перечисленных job (барьер "
        "стадий для него не действует)",
        "needs_plus_stage": "needs заменяет ожидание стадии: job не ждёт job предыдущей стадии, которых нет в списке",
        "empty_needs_as_none": "needs: [] — job вообще ничего не ждёт и стартует в нулевую минуту, а не после предыдущей "
        "стадии",
        "sequential": "job выполняются параллельно, пока их зависимости позволяют (раннеров достаточно), а не друг за "
        "другом в порядке файла",
    }
    answer_hint = "Введите три числа через пробел — ответы по порядку вопроса, в минутах."

    def make(self, rng: random.Random) -> Scenario:  # noqa: C901
        n_stages = rng.randint(3, 4)
        stage_names = rng.sample(["build", "test", "check", "package", "stage", "deploy"], n_stages)
        # порядок стадий осмысленный: build → … → deploy
        rank = {"build": 0, "test": 1, "check": 2, "package": 3, "stage": 4, "deploy": 5}
        stage_names.sort(key=rank.__getitem__)
        names = rng.sample(_JOBS, 6 + n_stages)
        counts = [rng.randint(1, 2)] + [rng.randint(2, 3) for _ in range(n_stages - 2)] + [rng.randint(1, 2)]
        jobs: list[tuple[str, str, int, list[str] | None]] = []
        by_stage: list[list[str]] = []
        it = iter(names)
        for si, stage in enumerate(stage_names):
            cur: list[str] = []
            for _ in range(counts[si]):
                name = next(it)
                earlier = [j for lst in by_stage for j in lst]
                needs: list[str] | None = None
                if earlier and rng.random() < 0.55:
                    needs = [] if rng.random() < 0.18 else sorted(rng.sample(earlier, rng.randint(1, min(2, len(earlier)))))
                jobs.append((name, stage, rng.randint(1, 20), needs))
                cur.append(name)
            by_stage.append(cur)
        if not any(j[3] for j in jobs) or not any(j[3] == [] for j in jobs):
            raise GenerationRetry
        lines = [f"stages: [{', '.join(stage_names)}]"]
        for name, stage, t, needs in jobs:
            lines += [f"{name}:", f"  stage: {stage}", f"  time: {t}"]
            if needs is not None:
                lines.append(f"  needs: [{', '.join(needs)}]")
        code = "\n".join(lines)
        late = [j[0] for j in jobs if j[1] in stage_names[1:]]
        asked = rng.sample(late, 2)
        return Scenario(code, {"asked": asked})

    def prompt(self, sc: Scenario) -> str:
        a, b = sc.data["asked"]
        return (
            "Конвейер в стиле GitLab CI: time — длительность job в минутах; раннеров неограниченно, все job проходят "
            "успешно. Через сколько минут от старта пайплайна стартует job "
            f"{a}, через сколько стартует job {b} и через сколько завершится весь пайплайн?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        doc = yaml.safe_load(code)
        a, b = sc.data["asked"]
        if mutant == "sequential":
            jobs = {k: v for k, v in doc.items() if k != "stages" and isinstance(v, dict) and "stage" in v}
            starts: dict[str, int] = {}
            clock = 0
            for name, job in jobs.items():
                starts[name] = clock
                clock += int(job["time"])
            return f"{starts[a]} {starts[b]} {clock}"
        flags = {None: {}, "needs_ignored": {"ignore_needs": True}, "needs_plus_stage": {"plus_stage": True},
                 "empty_needs_as_none": {"empty_as_none": True}}[mutant]
        start, finish = schedule_jobs(doc, **flags)
        return f"{start[a]} {start[b]} {max(finish.values())}"

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Job без needs стартует, когда завершились все job предыдущих стадий; job с needs — когда завершились "
            "перечисленные (даже если они из более ранних стадий); needs: [] разрешает старт в нулевую минуту. "
            "Завершение пайплайна — позднейшее завершение любой job (критический путь)."
        )
