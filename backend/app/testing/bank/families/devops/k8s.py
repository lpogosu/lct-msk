"""Kubernetes: арифметика RollingUpdate (maxSurge / maxUnavailable) и размещение подов по requests.

Ключи — точные формулы из документации Kubernetes: maxSurge в процентах округляется ВВЕРХ, maxUnavailable — ВНИЗ; если
оба значения в штуках равны нулю, maxUnavailable принудительно 1 (deployment util ResolveFenceposts); максимум подов за
обновление ограничен ёмкостью кластера; ресурсы узла считаются за вычетом резервов и DaemonSet, количество подов на узле —
минимум по CPU и памяти, округление вниз ПО КАЖДОМУ узлу (ресурсы разных узлов не складываются).
"""

from __future__ import annotations

import math
import random
import re
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
)

# ============================================================== rolling update

_CAP_RE = re.compile(r"не более (\d+) под")


def resolve_fence(value: Any, replicas: int, *, round_up: bool) -> int:
    """maxSurge / maxUnavailable: целое — как есть, «N%» — доля от replicas с округлением вверх/вниз."""
    if isinstance(value, str):
        if not value.endswith("%"):
            raise ScenarioError(f"неверное значение: {value}")
        raw = replicas * int(value[:-1]) / 100
        return math.ceil(raw) if round_up else math.floor(raw)
    return int(value)


def rolling_numbers(
    code: str,
    *,
    round_wrong: bool = False,
    no_limit: bool = False,
    ready_minus_surge: bool = False,
    total_plus_unavailable: bool = False,
    skip_zero_rule: bool = False,
) -> tuple[int, int, int, int]:
    """(maxSurge, maxUnavailable в штуках, максимум подов, минимум Ready)."""
    cap = _CAP_RE.search(code)
    if not cap:
        raise ScenarioError("нет ограничения кластера")
    capacity = int(cap.group(1))
    doc = yaml.safe_load(code)
    spec = doc["spec"]
    replicas = int(spec["replicas"])
    ru = spec["strategy"]["rollingUpdate"]
    surge = resolve_fence(ru["maxSurge"], replicas, round_up=not round_wrong)
    unav = resolve_fence(ru["maxUnavailable"], replicas, round_up=round_wrong)
    if surge == 0 and unav == 0 and not skip_zero_rule:
        unav = 1
    total = replicas + (unav if total_plus_unavailable else surge)
    if not no_limit and not total_plus_unavailable:
        total = min(total, capacity)
    ready = replicas - (surge if ready_minus_surge else unav)
    return surge, unav, total, ready


@register
class RollingUpdate(OpsFamily):
    id = "ops.k8s.rolling"
    observes = ("kubernetes",)
    title = "Kubernetes: арифметика RollingUpdate"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "senior"
    position = -0.3
    time_limit_s = 180
    enemies = ("ops.res.limits",)
    answer_kind = "ints"
    language = "text"
    measures = (
        "maxSurge и maxUnavailable в штуках и процентах (вверх / вниз), правило «оба нуля → maxUnavailable = 1», "
        "максимум подов при ограниченной ёмкости кластера, минимум Ready."
    )
    misconceptions = {
        "round_wrong": "maxSurge в процентах округляется вверх, maxUnavailable — вниз (чтобы обновление могло идти, "
        "но не роняло лишнего)",
        "no_cluster_limit": "создать подов больше, чем помещается в кластере, нельзя: максимум ограничен его ёмкостью",
        "ready_minus_surge": "число Ready-подов не опускается ниже replicas − maxUnavailable; surge влияет на "
        "максимум, а не на минимум",
        "total_plus_unavailable": "максимум подов — это replicas + maxSurge (с учётом ёмкости), а не replicas + "
        "maxUnavailable",
        "skip_zero_rule": "если maxSurge и maxUnavailable оба оказались нулём, Kubernetes принудительно ставит "
        "maxUnavailable = 1, иначе обновление не сдвинется",
    }
    answer_hint = "Введите четыре числа через пробел в порядке вопроса."

    def make(self, rng: random.Random) -> Scenario:
        replicas = rng.randint(4, 40)

        def pick(zero_ok: bool) -> Any:
            if rng.random() < 0.55:
                return f"{rng.choice([10, 15, 20, 25, 30, 33, 40, 50])}%"
            return rng.randint(0 if zero_ok else 1, 5)

        surge_v = pick(False) if rng.random() < 0.85 else 0
        unav_v = pick(True)
        surge = resolve_fence(surge_v, replicas, round_up=True)
        unav = resolve_fence(unav_v, replicas, round_up=False)
        if isinstance(surge_v, int) and isinstance(unav_v, int) and surge_v == 0 and unav_v == 0:
            raise GenerationRetry  # API Kubernetes отвергает два нуля в штуках
        eff_unav = unav if (surge or unav) else 1
        capacity = replicas + rng.randint(0, surge + 3)
        if capacity == replicas and eff_unav == 0:
            raise GenerationRetry  # обновление не могло бы сдвинуться
        name = rng.choice(["shop-api", "billing", "catalog", "auth-gw", "orders", "notify"])
        code = "\n".join([
            f"# В кластере помещается не более {capacity} подов",
            "apiVersion: apps/v1",
            "kind: Deployment",
            "metadata:",
            f"  name: {name}",
            "spec:",
            f"  replicas: {replicas}",
            "  strategy:",
            "    type: RollingUpdate",
            "    rollingUpdate:",
            f"      maxSurge: {surge_v}",
            f"      maxUnavailable: {unav_v}",
        ])
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Выкатывается новая версия Deployment (обновление идёт без ошибок). Укажите по порядку: 1) сколько подов "
            "означает maxSurge; 2) сколько подов означает maxUnavailable; 3) какое наибольшее число подов Deployment "
            "существует одновременно; 4) какое наименьшее число подов Ready остаётся в ходе обновления."
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        flags = {
            None: {}, "round_wrong": {"round_wrong": True}, "no_cluster_limit": {"no_limit": True},
            "ready_minus_surge": {"ready_minus_surge": True}, "total_plus_unavailable": {"total_plus_unavailable": True},
            "skip_zero_rule": {"skip_zero_rule": True},
        }[mutant]
        return " ".join(str(x) for x in rolling_numbers(code, **flags))

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Проценты переводятся в штуки от replicas: maxSurge — с округлением вверх, maxUnavailable — вниз. Если оба "
            "значения получились нулём, maxUnavailable становится 1. Подов одновременно не больше replicas + maxSurge, и "
            "не больше ёмкости кластера. Ready-подов не меньше replicas − maxUnavailable."
        )


# ============================================================== ресурсы и размещение

_BINARY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30}
_DECIMAL = {"K": 10**3, "M": 10**6, "G": 10**9}
_MEM_RE = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|K|M|G)?$")


def cpu_millis(value: Any) -> int:
    """cpu: 4, 0.5, «500m» → миллиядра."""
    if isinstance(value, str):
        if value.endswith("m"):
            return int(value[:-1])
        return round(float(value) * 1000)
    return round(float(value) * 1000)


def mem_bytes(value: Any, *, binary_only: bool = False) -> int:
    m = _MEM_RE.match(str(value))
    if not m:
        raise ScenarioError(f"память: {value}")
    num, suffix = float(m.group(1)), m.group(2)
    if suffix is None:
        return int(num)
    if suffix in _BINARY:
        return int(num * _BINARY[suffix])
    return int(num * (_BINARY[suffix + "i"] if binary_only else _DECIMAL[suffix]))


def placement(
    code: str,
    *,
    pooled: bool = False,
    no_reserved: bool = False,
    no_daemonset: bool = False,
    binary_only: bool = False,
    cpu_only: bool = False,
) -> tuple[int, int]:
    """(сколько подов поместится, сколько миллиядер CPU останется свободно во всём кластере)."""
    doc = yaml.safe_load(code)
    zero = {"cpu": 0, "memory": 0}
    reserved = zero if no_reserved else doc["reserved_per_node"]
    daemon = zero if no_daemonset else doc["daemonset_per_node"]
    req = doc["pod_requests"]
    req_cpu, req_mem = cpu_millis(req["cpu"]), mem_bytes(req["memory"], binary_only=binary_only)
    if req_cpu <= 0 or req_mem <= 0:
        raise ScenarioError("запрос ресурсов должен быть положительным")

    def alloc(node: dict[str, Any]) -> tuple[int, int]:
        cpu = cpu_millis(node["cpu"]) - cpu_millis(reserved["cpu"]) - cpu_millis(daemon["cpu"])
        mem = (
            mem_bytes(node["memory"], binary_only=binary_only)
            - mem_bytes(reserved["memory"], binary_only=binary_only)
            - mem_bytes(daemon["memory"], binary_only=binary_only)
        )
        return max(cpu, 0), max(mem, 0)

    nodes = doc["nodes"]
    if pooled:
        cpu_total = sum(int(g["count"]) * alloc(g)[0] for g in nodes)
        mem_total = sum(int(g["count"]) * alloc(g)[1] for g in nodes)
        fit = cpu_total // req_cpu if cpu_only else min(cpu_total // req_cpu, mem_total // req_mem)
        return fit, cpu_total - fit * req_cpu
    total = left = 0
    for g in nodes:
        cpu, mem = alloc(g)
        fit = cpu // req_cpu if cpu_only else min(cpu // req_cpu, mem // req_mem)
        total += int(g["count"]) * fit
        left += int(g["count"]) * (cpu - fit * req_cpu)
    return total, left


@register
class ResourceLimits(OpsFamily):
    id = "ops.res.limits"
    observes = ("kubernetes",)
    title = "Kubernetes: сколько подов поместится по requests"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "junior"
    position = 0.7
    time_limit_s = 150
    enemies = ("ops.k8s.rolling",)
    answer_kind = "ints"
    language = "text"
    measures = (
        "Единицы CPU (m, ядра) и памяти (Mi/Gi против M/G), доступный ресурс узла за вычетом резервов и DaemonSet, "
        "число подов на узле — минимум по CPU и памяти, округление вниз по каждому узлу."
    )
    misconceptions = {
        "pooled": "поды размещаются на узлах по отдельности: остатки разных узлов не складываются, округление вниз "
        "идёт на каждом узле",
        "no_reserved": "резерв kube-reserved и system-reserved вычитается из ёмкости каждого узла: поды его занять "
        "не могут",
        "no_daemonset": "DaemonSet занимает ресурсы на каждом узле до обычных подов",
        "binary_only": "G и M — десятичные единицы (10⁹ и 10⁶ байт), а Gi и Mi — двоичные (2³⁰ и 2²⁰): "
        "они не равны",
        "cpu_only": "под должен поместиться и по CPU, и по памяти: число подов на узле — минимум по двум ресурсам",
    }
    answer_hint = "Введите два числа через пробел: сколько подов поместится и сколько миллиядер CPU останется свободно."

    def make(self, rng: random.Random) -> Scenario:
        groups = []
        for cpu, mem in rng.sample([(2, 8), (4, 16), (8, 32), (16, 64), (4, 8), (8, 16)], 2):
            groups.append(f"  - {{count: {rng.randint(2, 5)}, cpu: {cpu}, memory: {mem}Gi}}")
        res_cpu, res_mem = rng.randrange(200, 1100, 100), rng.choice(["512Mi", "1Gi", "1536Mi", "2Gi"])
        ds_cpu, ds_mem = rng.randrange(50, 300, 25), rng.choice(["64Mi", "128Mi", "256Mi", "384Mi"])
        pod_cpu = rng.choice([f"{rng.randrange(150, 1600, 50)}m", f"{rng.randrange(150, 1600, 50)}m", "0.5", "1.5"])
        pod_mem = rng.choice([f"{rng.randrange(300, 3000, 100)}M", f"{rng.randint(1, 6)}G", f"{rng.randrange(256, 3072, 64)}Mi",
                              f"{rng.randint(1, 5)}Gi"])
        code = "\n".join([
            "nodes:", *groups,
            f"reserved_per_node: {{cpu: {res_cpu}m, memory: {res_mem}}}",
            f"daemonset_per_node: {{cpu: {ds_cpu}m, memory: {ds_mem}}}",
            f"pod_requests: {{cpu: {pod_cpu}, memory: {pod_mem}}}",
        ])
        sc = Scenario(code)
        total, _ = placement(code)
        if total < 4:
            raise GenerationRetry
        return sc

    def prompt(self, sc: Scenario) -> str:
        return (
            "Узлы кластера описаны группами (count узлов с одинаковой ёмкостью). Из ёмкости каждого узла вычитаются "
            "резервы системы и DaemonSet, остальное доступно подам. Какое наибольшее число одинаковых подов с "
            "показанными requests поместится в кластер и сколько миллиядер CPU останется свободно (по доступному "
            "CPU узлов)?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        flags = {None: {}, "pooled": {"pooled": True}, "no_reserved": {"no_reserved": True},
                 "no_daemonset": {"no_daemonset": True}, "binary_only": {"binary_only": True},
                 "cpu_only": {"cpu_only": True}}[mutant]
        fit, left = placement(code, **flags)
        return f"{fit} {left}"

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Для узла: доступно = ёмкость − резерв − DaemonSet по каждому ресурсу. Подов на узле — минимум из "
            "⌊CPU/requests.cpu⌋ и ⌊память/requests.memory⌋ (G и M — десятичные, Gi и Mi — двоичные единицы). Умножаем на "
            "число узлов группы и суммируем по группам; остаток CPU — доступный минус занятый подами."
        )
