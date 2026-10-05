"""Пауза сборщика циклов на время сборки подборки (spec-contact §6.2: p95 ≤ 300 мс на 2000 кандидатов).

Сборка версии создаёт сотни тысяч короткоживущих объектов (признаки, результаты скоринга, объяснения) без
ссылочных циклов: память освобождают счётчики ссылок. Сборщик циклов в это время только мешает: объекты, живые
на момент его запуска, переезжают в старшие поколения, и через одну сборку запускается полный проход по всей
куче процесса (+100–150 мс). Поэтому на время сборки он выключается; включается обратно, когда закончились
все параллельные сборки (счётчик глубины под блокировкой). Если сборщик был выключен извне — не трогаем.
"""

from __future__ import annotations

import gc
import threading
from collections.abc import Iterator
from contextlib import contextmanager

_lock = threading.Lock()
_depth = 0
_we_disabled = False


@contextmanager
def no_cycle_gc() -> Iterator[None]:
    global _depth, _we_disabled
    with _lock:
        if _depth == 0 and gc.isenabled():
            gc.disable()
            _we_disabled = True
        _depth += 1
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0 and _we_disabled:
                _we_disabled = False
                gc.enable()
