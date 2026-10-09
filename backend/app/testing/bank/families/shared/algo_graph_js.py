"""algo.graph.dfs_stack.js — JS-версия: порядок обхода графа в глубину с явным стеком (и чем он отличается от BFS
и рекурсии). Отбор экземпляров — по Python-двойнику (шаблоны algo.graph.dfs_stack), ключ и дистракторы — исполнением
JS в Node (shared/_js.py).
"""

from __future__ import annotations

import json
import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import JsExecFamily, JsSpec, fill
from app.testing.bank.families.shared import algo_graph as py
from app.testing.bank.families.shared._common import exec_capture, explain, text_distractors
from app.testing.bank.families.shared._js import JS_SPECS, require_same

_FUNCS = ("dfs", "walk", "traverse", "visitAll")

_ITERATIVE = """function <<fn>>(start) {
  const seen = new Set();
  const stack = [start];
  const order = [];
  while (stack.length > 0) {
    const v = stack.pop();
    if (seen.has(v)) {
      continue;
    }
    seen.add(v);
    order.push(v);
    for (const u of graph[v]) {
      if (!seen.has(u)) {
        stack.push(u);
      }
    }
  }
  return order;
}"""
# Модели кандидата с заблуждениями — тоже исполняются на том же графе.
_MARK_ON_PUSH = """function <<fn>>(start) {
  const seen = new Set([start]);
  const stack = [start];
  const order = [];
  while (stack.length > 0) {
    const v = stack.pop();
    order.push(v);
    for (const u of graph[v]) {
      if (!seen.has(u)) {
        seen.add(u);
        stack.push(u);
      }
    }
  }
  return order;
}"""
_RECURSIVE = """function <<fn>>(start) {
  const order = [];
  const go = (v) => {
    order.push(v);
    for (const u of graph[v]) {
      if (!order.includes(u)) {
        go(u);
      }
    }
  };
  go(start);
  return order;
}"""
_BFS = """function <<fn>>(start) {
  const seen = new Set([start]);
  const queue = [start];
  const order = [];
  while (queue.length > 0) {
    const v = queue.shift();
    order.push(v);
    for (const u of graph[v]) {
      if (!seen.has(u)) {
        seen.add(u);
        queue.push(u);
      }
    }
  }
  return order;
}"""

HINTS = {
    "recursive_order": "явный стек переворачивает порядок соседей: последний добавленный сосед извлекается "
    "первым, поэтому порядок отличается от рекурсивного DFS.",
    "bfs_order": "стек (LIFO: push/pop) даёт обход в глубину; в ширину (BFS) обходит очередь (FIFO: push/shift).",
    "mark_on_push": "здесь вершина помечается при извлечении из стека, а не при добавлении: вершина может "
    "лежать в стеке несколько раз, и посещается та копия, что извлечена раньше.",
}
_MODELS = {"recursive_order": _RECURSIVE, "bfs_order": _BFS, "mark_on_push": _MARK_ON_PUSH}
_PY_MODELS = {"recursive_order": py._RECURSIVE, "bfs_order": py._BFS, "mark_on_push": py._MARK_ON_PUSH}


@register
class DfsStackOrderJs(JsExecFamily):
    id = "algo.graph.dfs_stack.js"
    title = "Обход графа в глубину с явным стеком на JS: порядок вершин"
    specs = JS_SPECS
    module = "algo"
    format = "predict_output"
    # Шкала frontend_js: грейд, pos и якорь перенесены с Python-версии algo.graph.dfs_stack — пересчитать по данным.
    target_grade = "middle"
    position = 0.0
    c = 0.0
    time_limit_s = 150
    is_anchor = True

    # Ответ — буквы вершин: та же терпимая нормализация, что у Python-версии (кириллические двойники, «Ответ:»).
    normalize = py.DfsStackOrder.normalize

    def build(self, rng: random.Random) -> JsSpec:
        fn = rng.choice(_FUNCS)
        while True:  # те же розыгрыши, что у Python-версии
            nodes = rng.sample(py._LETTERS, 7)
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
            # отбор: три заблуждения дают три разных неверных ответа (Python-двойник, как в оригинале)
            py_graph = "graph = {\n" + "".join(f'    "{v}": {adj[v]!r},\n'.replace("'", '"') for v in adj) + "}\n"
            py_call = f'\n\nprint(" ".join(f("{start}")))'
            py_key = self.normalize(exec_capture(py_graph + "\n\n" + py._ITERATIVE.format(fn="f") + py_call))
            alt = [(exec_capture(py_graph + "\n\n" + m.format(fn="f") + py_call), t) for t, m in _PY_MODELS.items()]
            if len(text_distractors(py_key, alt, self.normalize)) == 3:
                break
        graph = "const graph = {\n" + "".join(f"  {v}: {json.dumps(adj[v])},\n" for v in adj) + "};"
        call = f'console.log({fn}("{start}").join(" "));'

        def program(template: str) -> str:
            return graph + "\n\n" + fill(template, fn=fn) + "\n\n" + call

        code = program(_ITERATIVE)
        progs = {"key": code, **{t: program(m) for t, m in _MODELS.items()}}
        return JsSpec(code, progs, {"adj": adj, "start": start, "py_key": py_key})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        adj, start = d["adj"], d["start"]
        key = self.normalize(outs["key"])
        require_same(self.id, seed, "ключ", key, d["py_key"])
        distractors = text_distractors(key, [(outs[t], t) for t in _MODELS], self.normalize)
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
                code=spec.code,
                language="javascript",
                answer_mode="text",
                answer_hint="Вершины одной строкой через пробел в порядке вывода, например: K D P.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
