"""k-анонимность для агрегатов панели ФСП (docs/FSP_INTEGRATION.md, «Панель ФСП: кадровый резерв»).

Правило: ячейка, где людей (или компаний) меньше k, отдаётся как «<k» без числа. Ноль не скрываем — он никого не
раскрывает. Вторичное подавление нужно для взаимоисключающих строк: если скрыта ровно одна ячейка, а итог виден,
её можно вычесть, поэтому скрываем ещё и наименьшую из видимых.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from typing import TypeVar

from pydantic import BaseModel, Field

K = TypeVar("K", bound=Hashable)


class Cell(BaseModel):
    """Число в ячейке панели. n = None, если ячейка скрыта по k-анонимности (тогда text = «<k»)."""

    n: int | None = Field(description="Число или null, если скрыто (меньше порога k)")
    text: str = Field(description="Что показать: число или «<5»")
    suppressed: bool = Field(default=False, description="Ячейка скрыта по k-анонимности")


def mask(n: int, k: int) -> Cell:
    """Одна ячейка: 1…k−1 → «<k»; 0 и ≥k показываем."""
    if 0 < n < k:
        return Cell(n=None, text=f"<{k}", suppressed=True)
    return Cell(n=n, text=str(n))


def hidden(k: int) -> Cell:
    return Cell(n=None, text=f"<{k}", suppressed=True)


def mask_partition(values: Mapping[K, int], k: int, *, complementary: bool = True) -> dict[K, Cell]:
    """Ячейки взаимоисключающего разбиения (округа, статусы): обычное подавление + вторичное."""
    out = {key: mask(n, k) for key, n in values.items()}
    small = [key for key, n in values.items() if 0 < n < k]
    if complementary and len(small) == 1:
        shown = [(n, key) for key, n in values.items() if n >= k]
        if shown:
            out[min(shown, key=lambda t: t[0])[1]] = hidden(k)
    return out


def fold_small_rows(rows: dict[K, int], k: int) -> tuple[dict[K, int], int]:
    """Строки с n ≥ k оставляем, остальные сливаем в «прочие». Если «прочие» сами получились в диапазоне 1…k−1,
    домешиваем в них наименьшую видимую строку, пока сумма не станет ≥ k (иначе скрытое выводится из итога)."""
    shown = {key: n for key, n in rows.items() if n >= k}
    other = sum(n for key, n in rows.items() if key not in shown)
    while 0 < other < k and shown:
        smallest = min(shown, key=lambda key: shown[key])
        other += shown.pop(smallest)
    return shown, other
