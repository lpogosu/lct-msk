"""algo.stack_queue.trace.js — JS-версия трассировки стека (LIFO) и очереди (FIFO) для шкалы frontend_js.

Та же задача, что у algo.stack_queue.trace, на массивах JS: стек — push/pop, очередь — push/shift. Ключ и дистракторы —
stdout программы и её мутантов в Node; ключ сверяется с пошаговой моделью из разбора (shared/_js.py).
"""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import JsSpec
from app.testing.bank.families.shared._common import explain, norm_int_seq, text_distractors
from app.testing.bank.families.shared._js import JS_SPECS, JsTextIntsFamily, js_list, require_same

_NAMES = (("stack", "queue"), ("undo", "jobs"), ("history", "inbox"), ("calls", "tasks"), ("st", "q"))

HINTS = {
    "stack_as_queue": "стек — LIFO: pop() снимает последний добавленный элемент, а не первый.",
    "queue_as_stack": "очередь — FIFO: shift() забирает самый ранний элемент (начало массива), а не последний "
    "добавленный.",
    "both_swapped": "перепутаны обе структуры: стек отдаёт последний добавленный (LIFO), очередь — первый (FIFO).",
}
_TAGS = ("stack_as_queue", "queue_as_stack", "both_swapped")


@register
class StackQueueTraceJs(JsTextIntsFamily):
    id = "algo.stack_queue.trace.js"
    title = "Стек и очередь на массивах JS: что окажется в выводе"
    specs = JS_SPECS
    module = "algo"
    format = "predict_output"
    # Шкала frontend_js: грейд, pos и якорь перенесены с Python-версии algo.stack_queue.trace — пересчитать по данным.
    target_grade = "intern"
    position = 0.0
    time_limit_s = 90
    is_anchor = True

    def build(self, rng: random.Random) -> JsSpec:
        # Те же розыгрыши, что у Python-версии (spec-testing §3.8): две выдачи из стека, две из очереди, перекладывание
        # и ещё одна операция не в самом конце (иначе она не влияет на вывод).
        s, q = rng.choice(_NAMES)
        values = rng.sample(range(10, 100), 7)
        initial, extra = values[:5], values[5:]
        ops: list[tuple[str, int]] = [("out_s", 0)] * 2 + [("out_q", 0)] * 2
        rng.shuffle(ops)
        moves = [(rng.choice(("q_from_s", "s_from_q")), 0)]
        moves.append(rng.choice((("push_s", extra[0]), ("push_q", extra[0]), ("q_from_s", 0), ("s_from_q", 0))))
        for mv in moves:
            ops.insert(rng.randint(0, len(ops) - 1), mv)
        render = {
            "out_s": f"out.push({s}.pop());", "out_q": f"out.push({q}.shift());",
            "q_from_s": f"{q}.push({s}.pop());", "s_from_q": f"{s}.push({q}.shift());",
        }
        src = [render[op] if op in render else f"{s if op == 'push_s' else q}.push({v});" for op, v in ops]
        code = (
            f"const {s} = [];\n"
            f"const {q} = [];\n"
            f"for (const x of {js_list(initial)}) {{\n"
            f"  {s}.push(x);\n"
            f"  {q}.push(x);\n"
            "}\n\n"
            "const out = [];\n" + "\n".join(src) + '\nconsole.log(out.join(" "));'
        )
        # Заблуждения, воплощённые в коде: стек как очередь (pop → shift), очередь как стек (shift → pop), обе сразу.
        stack_q = code.replace(f"{s}.pop()", f"{s}.shift()")
        queue_s = code.replace(f"{q}.shift()", f"{q}.pop()")
        both = stack_q.replace(f"{q}.shift()", f"{q}.pop()")
        progs = {"key": code, "stack_as_queue": stack_q, "queue_as_stack": queue_s, "both_swapped": both}
        return JsSpec(code, progs, {"s": s, "q": q, "initial": initial, "ops": ops, "src": src})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        s, q, initial = d["s"], d["q"], d["initial"]
        key = norm_int_seq(outs["key"])
        distractors = text_distractors(key, [(outs[t], t) for t in _TAGS], norm_int_seq)
        st, qu, out, steps = list(initial), list(initial), [], []
        for (op, v), line in zip(d["ops"], d["src"], strict=True):
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
            steps.append(f"{line}  →  {s} = {js_list(st)}, {q} = {js_list(qu)}, out = {js_list(out)}")
        require_same(self.id, seed, "ключ", key, " ".join(map(str, out)))
        body = (
            f"После цикла {s} = {js_list(initial)} (вершина — справа), {q} = {js_list(initial)} (голова — слева). "
            "pop() снимает последний элемент массива (LIFO), shift() — первый (FIFO).\n"
            + "\n".join(steps) + f"\nВывод: {key}."
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=f"Что выведет программа? Массив {s} используется как стек, {q} — как очередь.",
                code=spec.code,
                language="javascript",
                answer_mode="text",
                answer_hint="Числа одной строкой через пробел — так, как их напечатает console.log(out.join(\" \")).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
