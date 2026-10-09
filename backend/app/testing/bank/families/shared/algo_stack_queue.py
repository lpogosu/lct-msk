"""algo.stack_queue.* — трассировка стека (LIFO) и очереди (FIFO)."""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import (
    PY_SPECS,
    TextIntsFamily,
    exec_capture,
    explain,
    norm_int_seq,
    text_distractors,
)

_NAMES = (("stack", "queue"), ("undo", "jobs"), ("history", "inbox"), ("calls", "tasks"), ("st", "q"))

HINTS = {
    "stack_as_queue": "стек — LIFO: pop() снимает последний добавленный элемент, а не первый.",
    "queue_as_stack": "очередь — FIFO: popleft() забирает самый ранний элемент, а не последний добавленный.",
    "both_swapped": "перепутаны обе структуры: стек отдаёт последний добавленный (LIFO), очередь — "
    "первый (FIFO).",
}


@register
class StackQueueTrace(TextIntsFamily):
    id = "algo.stack_queue.trace"
    title = "Стек и очередь: что окажется в выводе"
    specs = PY_SPECS  # фронтендеру — JS-версия «<id>.js» (shared/_js.py)
    module = "algo"
    format = "predict_output"
    target_grade = "intern"
    position = 0.0
    time_limit_s = 90
    is_anchor = True

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        s, q = rng.choice(_NAMES)
        values = rng.sample(range(10, 100), 7)
        initial, extra = values[:5], values[5:]
        ops: list[tuple[str, int]] = [("out_s", 0)] * 2 + [("out_q", 0)] * 2
        rng.shuffle(ops)
        moves = [(rng.choice(("q_from_s", "s_from_q")), 0)]
        moves.append(rng.choice((("push_s", extra[0]), ("push_q", extra[0]), ("q_from_s", 0), ("s_from_q", 0))))
        # перекладывания и добавления — не в самом конце, иначе они не влияют на вывод
        for mv in moves:
            ops.insert(rng.randint(0, len(ops) - 1), mv)
        render = {
            "out_s": f"out.append({s}.pop())", "out_q": f"out.append({q}.popleft())",
            "q_from_s": f"{q}.append({s}.pop())", "s_from_q": f"{s}.append({q}.popleft())",
        }
        src = [render[op] if op in render else f"{s if op == 'push_s' else q}.append({v})" for op, v in ops]
        code = (
            "from collections import deque\n\n"
            f"{s} = []\n"
            f"{q} = deque()\n"
            f"for x in {initial}:\n"
            f"    {s}.append(x)\n"
            f"    {q}.append(x)\n\n"
            "out = []\n" + "\n".join(src) + "\nprint(*out)"
        )
        key = norm_int_seq(exec_capture(code))
        stack_q = code.replace(f"{s}.pop()", f"{s}.pop(0)")
        queue_s = code.replace(f"{q}.popleft()", f"{q}.pop()")
        both = stack_q.replace(f"{q}.popleft()", f"{q}.pop()")
        distractors = text_distractors(
            key,
            [(exec_capture(stack_q), "stack_as_queue"), (exec_capture(queue_s), "queue_as_stack"),
             (exec_capture(both), "both_swapped")],
            norm_int_seq,
        )
        prompt = f"Что выведет программа? {s} используется как стек, {q} — как очередь."
        st, qu, out, steps = list(initial), list(initial), [], []
        for (op, v), line in zip(ops, src, strict=True):
            if op == "out_s":
                out.append(st.pop())
            elif op == "out_q":
                out.append(qu.pop(0))
            elif op == "q_from_s":
                qu.append(st.pop())
            elif op == "s_from_q":
                st.append(qu.pop(0))
            elif op == "push_s":
                st.append(v)
            else:
                qu.append(v)
            steps.append(f"{line}  →  {s}={st}, {q}={qu}, out={out}")
        body = (
            f"После цикла {s} = {initial} (вершина — справа), {q} = {initial} (голова — слева). "
            "pop() снимает последний элемент списка (LIFO), popleft() — первый элемент очереди (FIFO).\n"
            + "\n".join(steps) + f"\nВывод: {key}."
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code,
                language="python",
                answer_mode="text",
                answer_hint="Числа одной строкой через пробел — так, как их напечатает print(*out).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
