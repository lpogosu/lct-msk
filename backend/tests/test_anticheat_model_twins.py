"""Двойник в буфере для семейств с моделью по показанному тексту (ModelTwinMixin): React-состояние, пагинация.

Ключ двойника считает решатель по изменённому тексту; проверяем детерминизм, различие ключей, гейты и воспроизводимость
ключа экземпляра решателем."""

import pytest

from app.testing.anticheat.dispatch import family_twin_result, overrides_twin
from app.testing.bank.base import canonical
from app.testing.bank.registry import all_families

FAMILIES = [
    "react.batched_updates",
    "react.stale_closure",
    "react.controlled_input",
    "http.pagination.offset_math",
]
SEEDS = range(8)


@pytest.mark.parametrize("fid", FAMILIES)
def test_model_twin(fid: str) -> None:
    fam = all_families()[fid]
    assert overrides_twin(fam)
    built = 0
    for seed in SEEDS:
        inst = fam.generate(seed)
        sc = fam.scenario_for(seed)
        assert canonical(fam.normalize(fam.solve(sc, inst.rendered.code))) == canonical(inst.key)
        res = family_twin_result(fam, inst)
        if res.twin is None:
            continue
        built += 1
        t = res.twin
        assert t.twin_key_hash != t.key_hash
        assert all(t.gates.values())
        again = family_twin_result(fam, inst).twin
        assert again is not None and again.twin_key == t.twin_key and again.clipboard_text == t.clipboard_text
        assert t.clipboard_text != t.shown_text
    assert built >= len(SEEDS) // 2
