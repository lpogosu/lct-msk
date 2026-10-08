"""mq_core: семантика очередей сообщений на уровне навыка (Kafka, RabbitMQ, Celery).

Три модели, каждая — документированное поведение брокера, без недокументированных деталей:
- момент подтверждения (offset commit в Kafka, ack в RabbitMQ, acks_late в Celery): подтверждение ДО обработки даёт
  at-most-once (сообщение теряется при падении), ПОСЛЕ — at-least-once (повторная обработка после рестарта);
- назначение партиций группе потребителей Kafka по стратегии range: потребители отсортированы по имени, каждый
  получает подряд идущие партиции, первые P mod C получают на одну больше; при каждом входе или выходе перебалансировка
  назначает партиции заново; потребителей больше, чем партиций, — лишние простаивают;
- справедливая раздача RabbitMQ: без prefetch сообщения раздаются потребителям по кругу сразу и ждут у занятого,
  с prefetch_count=1 новое сообщение получает тот, кто освободился (basic.qos).
Ключ — результат модели (families/_scenario.py), контрольные примеры — в tests/test_bank_skills.py.
"""

from __future__ import annotations

import random
import re

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import numeric_alternatives
from app.testing.bank.families.skills._common import (
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    SkillFamily,
)

MODULE = "mq_core"

# ============================================================== момент подтверждения

_SYSTEMS = (
    ("Kafka", "офсет", "commit"),
    ("RabbitMQ", "сообщение", "ack"),
    ("Celery", "задача", "ack"),
)


def parse_ack(code: str) -> dict[str, object]:
    n = re.search(r"Сообщения: офсеты 0–(\d+)", code)
    during = [int(x) for x in re.findall(r"падение во время обработки офсета (\d+)", code)]
    after = [int(x) for x in re.findall(r"падение после обработки офсета (\d+), до подтверждения", code)]
    if not n or not (during or after):
        raise ScenarioError("нет диапазона или падений")
    if "до обработки" in code:
        mode, k = "before", 1
    elif m := re.search(r"после обработки офсета, кратного (\d+)", code):
        mode, k = "batch", int(m.group(1))
    elif "после обработки каждого" in code:
        mode, k = "after", 1
    else:
        raise ScenarioError("нет режима подтверждения")
    return {"last": int(n.group(1)), "during": during, "after": after, "mode": mode, "k": k}


def run_ack(code: str, mutant: str | None = None) -> list[int]:
    """Офсеты в порядке завершения обработки (с повторами). Падение — один раз на каждый указанный офсет."""
    case = parse_ack(code)
    last, mode, k = case["last"], case["mode"], case["k"]
    if mutant == "before_as_after" and mode == "before":
        mode = "after"
    elif mutant == "after_as_before" and mode == "after":
        mode = "before"
    elif mutant == "batch_per_message" and mode == "batch":
        mode, k = "after", 1
    during, after = set(case["during"]), set(case["after"])
    committed, pos, done = 0, 0, []
    guard = 0
    while pos <= last:
        guard += 1
        if guard > 400:
            raise ScenarioError("зацикливание")
        if mode == "before":
            committed = pos + 1
        crashed = False
        if pos in during:
            during.discard(pos)  # падение во время обработки: работа не завершена
            crashed = True
        else:
            done.append(pos)
            if pos in after:
                after.discard(pos)  # работа сделана, процесс упал до подтверждения
                crashed = True
            elif mode == "after" or (mode == "batch" and (pos + 1) % k == 0):
                committed = pos + 1
        if crashed:
            if mutant == "skip_failed":
                pos += 1  # рестарт «после упавшего» — как будто всё уже сделано
            elif mutant == "restart_from_zero":
                pos = 0
            else:
                pos = committed  # рестарт с последней подтверждённой позиции
            continue
        pos += 1
    return done


@register
class MqAckOrder(SkillFamily):
    id = "mq.ack_order"
    title = "Очередь: когда подтверждать сообщение — потеря или повторы"
    specs = SPECS
    module = MODULE
    observes = ("kafka", "rabbitmq", "celery")
    format = "predict_output"
    target_grade = "middle"
    position = -0.2
    time_limit_s = 180
    answer_kind = "ints"
    language = "text"
    measures = (
        "Гарантии доставки: подтверждение до обработки теряет сообщение при падении (at-most-once), после обработки "
        "даёт повторы (at-least-once), пакетное подтверждение повторяет всё после последней подтверждённой границы."
    )
    misconceptions = {
        "before_as_after": "подтверждение до обработки фиксирует позицию заранее: после падения сообщение уже не вернётся — оно потеряно",
        "after_as_before": "подтверждение после обработки не успевает выполниться при падении: сообщение придёт снова, и обработка повторится",
        "batch_per_message": "пакетное подтверждение фиксирует позицию только на границе пакета: после падения повторяется всё с начала пакета",
        "skip_failed": "после рестарта потребитель читает с последней подтверждённой позиции, а не со следующего сообщения",
        "restart_from_zero": "потребитель продолжает с последней подтверждённой позиции, а не с начала топика",
    }
    answer_hint = "Офсеты в порядке завершения обработки через пробел (повторные обработки перечислите повторно)."
    twin_patterns = (r"падение во время обработки офсета (\d+)", r"падение после обработки офсета (\d+),", r"кратного (\d+)")
    twin_range = (3, 12)

    def make(self, rng: random.Random) -> Scenario:
        name, unit, verb = rng.choice(_SYSTEMS)
        last = rng.randint(9, 13)
        spots = sorted(rng.sample(range(1, last), rng.randint(3, 5)))
        crashes = [(c, rng.choice(("during", "after"))) for c in spots]
        mode = rng.choice(("before", "after", "batch", "batch"))
        if mode == "before":
            ack = f"{verb} до обработки каждого сообщения"
        elif mode == "after":
            ack = f"{verb} после обработки каждого сообщения"
        else:
            ack = f"{verb} после обработки офсета, кратного {rng.choice((2, 3, 4))} (подтверждается позиция «офсет + 1»)"
        crash_lines = "\n".join(
            f"падение во время обработки офсета {c}"
            if kind == "during"
            else f"падение после обработки офсета {c}, до подтверждения"
            for c, kind in crashes
        )
        code = (
            f"Брокер: {name}, одна партиция\n"
            f"Сообщения: офсеты 0–{last}\n"
            f"Подтверждение ({verb}): {ack}\n"
            f"Падения процесса (каждое случается один раз):\n{crash_lines}\n"
            "После падения потребитель стартует с последней подтверждённой позиции"
        )
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(alts - {base, None}) < 2:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Потребитель читает партицию по порядку: получить сообщение, обработать, подтвердить. Процесс падает "
            "в указанные моменты: «во время обработки» — работа не завершилась, «после обработки, до подтверждения» — "
            "работа сделана, а подтверждение не выполнилось. В каком порядке потребитель ЗАВЕРШИТ обработку "
            "сообщений, включая повторные? Сообщение, которое потеряно, в ответ не попадёт."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        return " ".join(map(str, run_ack(code, mutant)))

    def why(self, sc: Scenario, raw_key: str) -> str:
        case = parse_ack(sc.code)
        done = run_ack(sc.code)
        dup = sorted({o for o in done if done.count(o) > 1})
        lost = [o for o in range(case["last"] + 1) if o not in done]
        mode = {
            "before": "позиция подтверждается до обработки, поэтому упавшее сообщение уже считается прочитанным и теряется",
            "after": "позиция подтверждается после обработки, поэтому после падения упавшее сообщение читается заново",
            "batch": "позиция подтверждается только на границе пакета, поэтому после падения повторяется всё с последней границы",
        }[case["mode"]]
        tail = []
        if dup:
            tail.append("обработаны повторно: " + " ".join(map(str, dup)))
        if lost:
            tail.append("потеряны: " + " ".join(map(str, lost)))
        return f"Здесь {mode}. Порядок завершения: {raw_key}" + (f" ({'; '.join(tail)})." if tail else ".")


# ============================================================== группа потребителей Kafka (range)


def range_assign(partitions: int, consumers: list[str]) -> dict[str, list[int]]:
    cs = sorted(consumers)
    base, extra = divmod(partitions, len(cs))
    out: dict[str, list[int]] = {}
    p = 0
    for i, c in enumerate(cs):
        n = base + (1 if i < extra else 0)
        out[c] = list(range(p, p + n))
        p += n
    return out


def parse_group(code: str) -> dict[str, object]:
    sizes_m = re.search(r"Сообщений в партициях: ([\d ]+)$", code, re.M)
    who = re.search(r"Вопрос: нагрузка потребителя (\w+)", code)
    if not sizes_m or not who:
        raise ScenarioError("нет размеров партиций или вопроса")
    sizes = [int(x) for x in sizes_m.group(1).split()]
    events = []
    for m in re.finditer(r"^\d+\) (в группу входят|входит|выходит) ([\w, ]+)$", code, re.M):
        names = [x.strip() for x in m.group(2).split(",")]
        events.append(("leave" if m.group(1) == "выходит" else "join", names))
    if not events:
        raise ScenarioError("нет событий")
    return {"sizes": sizes, "events": events, "who": who.group(1)}


def run_group(code: str, mutant: str | None = None) -> tuple[int, int]:
    """(нагрузка потребителя из вопроса в сообщениях, число простаивающих потребителей) после всех событий."""
    case = parse_group(code)
    sizes: list[int] = case["sizes"]
    members: list[str] = []
    assignment: dict[str, list[int]] = {}
    for kind, names in case["events"]:
        for nm in names:
            if kind == "join":
                if nm in members:
                    raise ScenarioError("потребитель уже в группе")
                members.append(nm)
            else:
                if nm not in members:
                    raise ScenarioError("такого потребителя нет")
                members.remove(nm)
        if not members:
            raise ScenarioError("группа пуста")
        if mutant == "no_rebalance_on_leave" and kind == "leave":
            for nm in names:
                assignment.pop(nm, None)  # партиции ушедшего «зависают»: заблуждение — их никто не подхватывает
            continue
        if mutant == "round_robin":
            cs = sorted(members)
            assignment = {c: [p for p in range(len(sizes)) if p % len(cs) == i] for i, c in enumerate(cs)}
        elif mutant == "join_order":
            base, extra = divmod(len(sizes), len(members))
            assignment, p = {}, 0
            for i, c in enumerate(members):
                n = base + (1 if i < extra else 0)
                assignment[c] = list(range(p, p + n))
                p += n
        else:
            assignment = range_assign(len(sizes), members)
    who = case["who"]
    if who not in members:
        raise ScenarioError("потребителя из вопроса нет в группе")
    load = sum(sizes[p] for p in assignment.get(who, []))
    idle = 0 if mutant == "more_is_faster" else sum(1 for c in members if not assignment.get(c))
    return load, idle


@register
class MqConsumerGroup(SkillFamily):
    id = "mq.consumer_group"
    title = "Kafka: партиции группы потребителей после перебалансировки"
    specs = SPECS
    module = MODULE
    observes = ("kafka",)
    format = "predict_output"
    target_grade = "middle"
    position = 0.3
    time_limit_s = 180
    answer_kind = "ints"
    language = "text"
    measures = (
        "Группа потребителей Kafka: партиция читается одним потребителем группы, назначение range по именам, "
        "перебалансировка при входе и выходе, простой лишних потребителей, зависимость параллелизма от числа партиций."
    )
    misconceptions = {
        "round_robin": "в стратегии range каждый потребитель получает подряд идущие партиции, а не по одной по кругу",
        "join_order": "потребители сортируются по имени (member id), а не по порядку входа в группу",
        "no_rebalance_on_leave": "при выходе потребителя запускается перебалансировка: его партиции переходят оставшимся",
        "more_is_faster": "потребителей больше числа партиций бессмысленно: лишние простаивают, партицию читает один потребитель группы",
    }
    answer_hint = "Два числа через пробел: сколько сообщений приходится на названного потребителя и сколько потребителей простаивает."
    twin_patterns = (r"Сообщений в партициях: (\d+)",)
    twin_range = (100, 9900)

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        m = re.search(r"Сообщений в партициях: ([\d ]+)$", code, re.M)
        return [(m.start(1) + x.start(), m.start(1) + x.end()) for x in re.finditer(r"\d+", m.group(1))] if m else []

    def make(self, rng: random.Random) -> Scenario:
        parts = rng.randint(3, 8)
        sizes = [rng.randint(100, 9500) for _ in range(parts)]
        pool = ["billing", "ledger", "mailer", "audit", "search", "worker", "relay"]
        first = rng.sample(pool, rng.randint(2, 4))
        events = [f"1) в группу входят {', '.join(first)}"]
        members = list(first)
        for i in range(2, rng.randint(3, 4) + 1):
            if len(members) > 2 and rng.random() < 0.55:
                gone = rng.choice(members)
                members.remove(gone)
                events.append(f"{i}) выходит {gone}")
            else:
                new = rng.choice([x for x in pool if x not in members] or [None])
                if new is None:
                    continue
                members.append(new)
                events.append(f"{i}) входит {new}")
        who = rng.choice(members)
        code = (
            f"Топик payments: {parts} партиций, номера от 0\n"
            f"Сообщений в партициях: {' '.join(map(str, sizes))}\n"
            "Группа читает топик; стратегия назначения — range, потребители упорядочены по имени (member id).\n"
            "При каждом входе или выходе — полная перебалансировка.\n"
            "События:\n" + "\n".join(events) + f"\nВопрос: нагрузка потребителя {who}"
        )
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(alts - {base, None}) < 2 or int(base.split()[0]) == 0:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Группа потребителей Kafka читает топик. Показаны размеры партиций и события в группе. После ВСЕХ событий "
            "посчитайте: сколько сообщений приходится на названного потребителя (сумма по его партициям) и сколько "
            "потребителей группы не получили ни одной партиции."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        load, idle = run_group(code, mutant)
        return f"{load} {idle}"

    def why(self, sc: Scenario, raw_key: str) -> str:
        case = parse_group(sc.code)
        members: list[str] = []
        for kind, names in case["events"]:
            for nm in names:
                members.append(nm) if kind == "join" else members.remove(nm)
        asg = range_assign(len(case["sizes"]), members)
        text = "; ".join(f"{c}: партиции {' '.join(map(str, ps)) or '—'}" for c, ps in asg.items())
        return (
            f"После последней перебалансировки (range, по имени): {text}. Партицию читает ровно один потребитель группы, "
            f"поэтому на {case['who']} приходится сумма сообщений его партиций, а потребители без партиций простаивают."
        )


# ============================================================== справедливая раздача RabbitMQ


def parse_dispatch(code: str) -> dict[str, object]:
    w = re.search(r"Потребителей: (\d+)", code)
    d = re.search(r"Время обработки сообщений \(с\): ([\d ]+)$", code, re.M)
    if not (w and d):
        raise ScenarioError("нет потребителей или времён")
    prefetch1 = "prefetch_count = 1" in code
    return {"workers": int(w.group(1)), "dur": [int(x) for x in d.group(1).split()], "prefetch1": prefetch1}


def run_dispatch(code: str, mutant: str | None = None) -> list[int]:
    """Номера сообщений (с 1) в порядке завершения обработки."""
    case = parse_dispatch(code)
    nw, dur = case["workers"], case["dur"]
    fair = case["prefetch1"]
    if mutant == "fair_always":
        fair = True
    elif mutant == "rr_always":
        fair = False
    if mutant == "fifo_completion":
        return [*range(1, len(dur) + 1), sum(dur)]
    finish: list[tuple[int, int]] = []
    if fair:
        free = [0] * nw
        for i, d in enumerate(dur, start=1):
            w = min(range(nw), key=lambda j: (free[j], j))
            free[w] += d
            finish.append((free[w], i))
    else:
        free = [0] * nw
        for i, d in enumerate(dur, start=1):
            w = (i - 1) % nw
            free[w] += d
            finish.append((free[w], i))
    times = [t for t, _ in finish]
    if len(set(times)) != len(times):
        raise ScenarioError("одновременное завершение: порядок не определён")
    return [*(i for _, i in sorted(finish)), max(times)]  # последнее число — когда закончится последнее сообщение


@register
class MqFairDispatch(SkillFamily):
    id = "mq.fair_dispatch"
    title = "RabbitMQ: раздача сообщений потребителям и prefetch"
    specs = SPECS
    module = MODULE
    observes = ("rabbitmq", "celery")
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    time_limit_s = 180
    answer_kind = "ints"
    language = "text"
    measures = (
        "Раздача сообщений между потребителями очереди: по кругу без prefetch (длинные задачи копятся у одного) и "
        "справедливо при prefetch_count = 1; порядок завершения не совпадает с порядком в очереди."
    )
    misconceptions = {
        "fair_always": "без prefetch брокер раздаёт сообщения по кругу сразу, не дожидаясь подтверждений: длинные задачи копятся у одного потребителя",
        "rr_always": "при prefetch_count = 1 сообщение получает тот потребитель, который освободился, а не следующий по кругу",
        "fifo_completion": "сообщения берутся по порядку, но заканчиваются в порядке времени обработки: с несколькими потребителями порядок завершения другой",
    }
    answer_hint = "Номера сообщений (с 1) в порядке завершения обработки, затем — на какой секунде закончится последнее. Всё через пробел."
    twin_patterns = ()
    twin_range = (1, 20)

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        m = re.search(r"Время обработки сообщений \(с\): ([\d ]+)$", code, re.M)
        return [(m.start(1) + x.start(), m.start(1) + x.end()) for x in re.finditer(r"\d+", m.group(1))] if m else []

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: object) -> list[str]:
        return numeric_alternatives(code[span[0] : span[1]], rng, p, 2, 19)

    def make(self, rng: random.Random) -> Scenario:
        nw = rng.choice((2, 2, 3))
        n = rng.randint(8, 10)
        dur = [rng.randint(1, 15) for _ in range(n)]
        prefetch = "prefetch_count = 1" if rng.random() < 0.5 else "prefetch не задан"
        code = (
            f"Очередь tasks, сообщения 1…{n} лежат в очереди сразу, потребители начинают одновременно\n"
            f"Потребителей: {nw} (подтверждают сообщение после обработки)\n"
            f"Настройка: {prefetch}\n"
            f"Время обработки сообщений (с): {' '.join(map(str, dur))}"
        )
        try:
            base = self.solve(Scenario(code), code)
            alts = {self.solve(Scenario(code), code, m) for m in self.misconceptions}
        except ScenarioError as e:
            raise GenerationRetry from e
        if len(alts - {base, None}) < 2:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "В очередь RabbitMQ сразу положили сообщения, потребители начинают одновременно. Показаны настройка prefetch "
            "и время обработки каждого сообщения. В каком порядке сообщения будут обработаны до конца? "
            "Ответьте номерами сообщений по порядку завершения и, последним числом, секундой, на которой закончится последнее."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        return " ".join(map(str, run_dispatch(code, mutant)))

    def why(self, sc: Scenario, raw_key: str) -> str:
        case = parse_dispatch(sc.code)
        how = (
            "prefetch_count = 1: новое сообщение получает потребитель, который освободился первым (при равенстве — с меньшим номером)"
            if case["prefetch1"]
            else "prefetch не задан: сообщения сразу раздаются по кругу (1-е первому, 2-е второму …) и ждут у занятого потребителя"
        )
        return f"{how}. Завершение считаем по накопленному времени каждого потребителя: {raw_key}."
