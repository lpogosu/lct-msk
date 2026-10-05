"""Прогон CI-гейтов по всем зарегистрированным семействам. Фильтр: pytest -k "py." / -k "js." и т.п."""

import pytest

from app.testing.bank.gates import check_family
from app.testing.bank.registry import all_families

FAMILIES = sorted(all_families().items())


@pytest.mark.parametrize("fid,fam", FAMILIES, ids=[f for f, _ in FAMILIES])
def test_family_passes_gates(fid, fam):
    errors = check_family(fam)
    assert not errors, f"{fid}: " + "; ".join(errors)
