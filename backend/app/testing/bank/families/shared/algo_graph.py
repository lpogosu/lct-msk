"""algo.graph.* — порядок обхода графа в глубину с явным стеком (и чем он отличается от BFS/рекурсии)."""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import Family, Instance, Rendered, register
from app.testing.bank.families.shared._common import (
    SPECS,
    exec_capture,
    explain,
    text_distractors,
    unlookalike,
)

_LETTERS = "ABCDEFGHKLMNPRSTUVWXYZ"
_FUNCS = ("dfs", "walk", "traverse", "visit_all")

_ITERATIVE = """def {fn}(start):
    seen = set()
    stack = [start]
    order = []
    while stack:
        v = stack.pop()
        if v in seen:
            continue
        seen.add(v)
        order.append(v)
        for u in graph[v]:
            if u not in seen:
                stack.append(u)
    return order
"""
# Модели кандидата с заблуждениями — тоже исполняются на том же графе.
_MARK_ON_PUSH = """def {fn}(start):
    seen = {{start}}
    stack = [start]
    order = []
    while stack:
        v = stack.pop()
        order.append(v)
        for u in graph[v]:
            if u not in seen:
                seen.add(u)
                stack.append(u)
    return order
"""
_RECURSIVE = """def {fn}(start):
    order = []

    def go(v):
        order.append(v)
        for u in graph[v]:
            if u not in order:
                go(u)

    go(start)
    return order
"""
_BFS = """from collections import deque


def {fn}(start):
    seen = {{start}}
    queue = deque([start])
    order = []
    while queue:
        v = queue.popleft()
        order.append(v)
        for u in graph[v]:
            if u not in seen:
                seen.add(u)
                queue.append(u)
    return order
"""

HINTS = {
    "recursive_order": "явный стек переворачивает порядок соседей: последний добавленный сосед извлекается "
    "первым, поэтому порядок отличается от рекурсивного DFS.",
    "bfs_order": "стек (LIFO) даёт обход в глубину; в ширину (BFS) обходит очередь (FIFO).",
    "mark_on_push": "здесь вершина помечается при извлечении из стека, а не при добавлении: вершина может "
    "лежать в стеке несколько раз, и посещается та копия, что извлечена раньше.",
}

_ANS = re.compile(r"[A-Za-z]")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
# Кириллица, у которой нет латинского двойника: слово с такой буквой — русский текст («Ответ:»), не вершины
_CYR_ONLY = re.compile(r"[А-Яа-яЁё]")


@register
class DfsStackOrder(Family):
    id = "algo.graph.dfs_stack"
    title = "Обход графа в глубину с явным стеком: порядок вершин"
    specs = SPECS
    module = "algo"
    format = "predict_output"
    target_grade = "middle"
    position = 0.0
    c = 0.0
    time_limit_s = 150
    is_anchor = True

    def normalize(self, answer: Any) -> Any:
        if not isinstance(answer, str):
            return answer
        letters: list[str] = []
        for word in _WORD.findall(answer):
            latin = unlookalike(word)
            if _CYR_ONLY.search(latin):  # после замены двойников осталась кириллица — это слово, а не вершины
                continue
            letters += _ANS.findall(latin)
        return " ".join(ch.upper() for ch in letters) if letters else answer.strip()

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        fn = rng.choice(_FUNCS)
        while True:
            nodes = rng.sample(_LETTERS, 7)
            edges: set[frozenset[str]] = set()
            for i in range(1, len(nodes)):  # случайное остовное дерево — граф связный
                edges.add(frozenset((nodes[i], nodes[rng.randrange(i)])))
            while len(edges) < len(nodes) - 1 + rng.choice((2, 3)):
                a, b = rng.sample(nodes, 2)
                edges.add(frozenset((a, b)))
            adj: dict[str, list[str]] = {v: [] for v in sorted(nodes)}
            # обход множества строк зависит от PYTHONHASHSEED — сортируем, чтобы генерация была детерминированной
            for a, b in sorted(tuple(sorted(pair)) for pair in edges):
                adj[a].append(b)
                adj[b].append(a)
            for v in adj:
                rng.shuffle(adj[v])
            start = rng.choice(nodes)
            graph_src = "graph = {\n" + "".join(f'    "{v}": {adj[v]!r},\n'.replace("'", '"') for v in adj) + "}\n"
            call = f'\n\nprint(" ".join({fn}("{start}")))'
            code = graph_src + "\n\n" + _ITERATIVE.format(fn=fn) + call
            key = self.normalize(exec_capture(code))
            alt = {
                "recursive_order": exec_capture(graph_src + "\n\n" + _RECURSIVE.format(fn=fn) + call),
                "bfs_order": exec_capture(graph_src + "\n\n" + _BFS.format(fn=fn) + call),
                "mark_on_push": exec_capture(graph_src + "\n\n" + _MARK_ON_PUSH.format(fn=fn) + call),
            }
            distractors = text_distractors(key, [(v, t) for t, v in alt.items()], self.normalize)
            if len(distractors) == 3:
                break

        # разбор: состояние стека на каждом шаге
        seen: set[str] = set()
        stack, steps = [start], []
        while stack:
            before = "[" + ", ".join(stack) + "]"
            v = stack.pop()
            if v in seen:
                steps.append(f"стек {before} → снимаем {v}: уже посещена, пропуск")
                continue
            seen.add(v)
            pushed = [u for u in adj[v] if u not in seen]
            stack += pushed
            steps.append(f"стек {before} → снимаем {v}, кладём {', '.join(pushed) if pushed else 'ничего'}")
        body = "\n".join(steps) + f"\nПорядок посещения: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt="Граф задан списками смежности. Что выведет программа?",
                code=code,
                language="python",
                answer_mode="text",
                answer_hint="Вершины одной строкой через пробел в порядке вывода, например: K D P.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
