"""Помощники JS-версий общих семейств (`algo.*.js`, `http.ratelimit.token_bucket.js`) — только шкала frontend_js.

Зачем. Общие семейства модулей algo и http_api были написаны на Python и выдавались и фронтендеру: пилот с ИИ-испытуемыми
показал, что кандидат на Frontend (JS/TS) получает код на Python. JS-версия — отдельное семейство с той же задачей,
ловушками, заблуждениями-дистракторами и форматом ответа, но с идиоматичным кодом на JavaScript; Python-оригинал
остаётся у backend_python, qa и devops (каталог — shared/CATALOG.md).

Параметры шкалы (грейд, pos → приор b, c, якорь) перенесены с Python-версии один в один: на шкале frontend_js у них
одна задача, а данных для раздельной калибровки ещё нет (spec-testing §3.2, §3.9). Пересчитать по данным, когда
наберутся ответы.

Ключ и дистракторы — stdout JS-программы в Node (execution.run_js через пакетный раннер frontend_js._common: программы
соседних seed исполняются одним процессом, у каждой свой vm-контекст). Отбор вырожденных экземпляров (цикл «сгенерировать
заново, пока дистракторы не различны») делается ДО Node — исполнением Python-двойника той же задачи из оригинального
семейства (в процессе, доли миллисекунды). В assemble ключ из Node обязан совпасть с ключом Python-двойника, иначе
генерация падает: две реализации на разных языках проверяют друг друга на каждом экземпляре.
"""

from __future__ import annotations

import json
from typing import Any

from app.testing.bank.execution import ExecutionError
from app.testing.bank.families.frontend_js._common import JsExecFamily
from app.testing.bank.families.shared._common import norm_int_seq

JS_SPECS: tuple[str, ...] = ("frontend_js",)


class JsTextIntsFamily(JsExecFamily):
    """JS-семейство, ответ которого — последовательность целых чисел (как TextIntsFamily у Python-версий)."""

    c = 0.0

    def normalize(self, answer: Any) -> Any:
        return norm_int_seq(answer)


def require_same(family_id: str, seed: int, what: str, js_value: Any, py_value: Any) -> None:
    """Ключ (или другой результат) из Node должен совпасть с результатом Python-двойника той же задачи."""
    if js_value != py_value:
        raise ExecutionError(f"{family_id} seed={seed}: {what} в Node {js_value!r} ≠ Python-двойник {py_value!r}")


def js_list(values: Any) -> str:
    """Литерал массива/значения в записи JS: [1, 2, 3], true/false, null."""
    if isinstance(values, bool):
        return "true" if values else "false"
    if values is None:
        return "null"
    if isinstance(values, list | tuple):
        return "[" + ", ".join(js_list(v) for v in values) + "]"
    if isinstance(values, str):
        return json.dumps(values, ensure_ascii=False)
    return str(values)
