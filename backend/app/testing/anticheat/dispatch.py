"""Единая точка построения двойника: сначала `Family.twin()`, затем общий мутатор.

Семейства с особым форматом данных (QA и DevOps: Dockerfile, nginx.conf, cron, файл с pytest-тестами; базовый класс
families/_scenario.ScenarioFamily) переопределяют `Family.twin()`: их ключ считает решатель по показанному тексту, а
не исполнение кода, поэтому общий мутатор (twins.try_make_twin) для них «язык не исполняется» (language = text).
Остальные семейства `Family.twin()` не переопределяют — для них это тот же общий мутатор (Family.twin → make_twin),
поэтому вызываем его напрямую: так сохраняются гейт пула (ключ двойника ≠ ключам соседей) и коды причин отказа.

Порядок (решение руководителя проекта): `family.twin(instance)` первым; общий мутатор — только если вернулся None.
Кто строит двойники (выдача задания, предрасчёт пула, песочница, «Подтверждение за 5 минут») — вызывает ЭТИ функции,
а не try_make_twin напрямую: иначе семейства с собственным двойником остались бы без него.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.twins import Twin, TwinResult, structural_reason, try_make_twin
from app.testing.bank.base import Family, Instance


def overrides_twin(fam: Family) -> bool:
    """Семейство строит двойник само (переопределён Family.twin), а не общим мутатором."""
    return type(fam).twin is not Family.twin


def twin_applicable(fam: Family, inst: Instance, cfg: AntiCheatConfig | None = None) -> bool:
    """Двойник возможен по самой форме задания: свой двойник семейства либо подходящий для общего мутатора формат."""
    cfg = cfg or load_anticheat_config()
    return overrides_twin(fam) or structural_reason(fam, inst, cfg) is None


def family_twin_result(
    fam: Family,
    inst: Instance,
    *,
    pool_key_hashes: Iterable[str] | None = None,
    cfg: AntiCheatConfig | None = None,
) -> TwinResult:
    """Двойник экземпляра: Family.twin() первым, затем общий мутатор (если форма ему подходит).

    Причина отказа (TwinResult.reason): у семейства со своим двойником — его причина (`try_twin`, если есть), иначе
    причина общего мутатора."""
    cfg = cfg or load_anticheat_config()
    if overrides_twin(fam):
        own = getattr(fam, "try_twin", None)
        res = own(inst) if callable(own) else TwinResult(fam.twin(inst), "ok")
        if res.twin is not None:
            return res if res.reason == "ok" else TwinResult(res.twin, "ok", res.attempts, res.rejects, res.executor)
        if structural_reason(fam, inst, cfg) is not None:
            return res  # общий мутатор тут бессилен (язык text и т. п.): честная причина — от самого семейства
    return try_make_twin(fam, inst, cfg=cfg, pool_key_hashes=pool_key_hashes)


def variant_twin(
    fam: Family,
    inst: Instance,
    *,
    avoid: Iterable[int],
    salt: str,
    cfg: AntiCheatConfig | None = None,
) -> Twin | None:
    """Вариант для «Подтверждения за 5 минут»: один изменённый литерал, не тот, что у настоящего двойника (avoid).

    Для общего мутатора — try_make_twin(strict=False) с другой солью; для семейства со своим двойником — Family.twin()
    с производным seed (литерал выбирается детерминированно от seed), пока не найдётся литерал вне avoid."""
    cfg = cfg or load_anticheat_config()
    avoid_set = set(avoid)
    if overrides_twin(fam):
        for k in range(cfg.verification.variant_tries):
            seed = int(hashlib.sha256(f"{salt}|{fam.id}|{inst.seed}|{k}".encode()).hexdigest()[:8], 16)
            twin = fam.twin(inst, seed=seed)
            if twin is not None and twin.changed_literal.ident not in avoid_set:
                return twin
        if structural_reason(fam, inst, cfg) is not None:
            return None
    return try_make_twin(fam, inst, cfg=cfg, strict=False, avoid=avoid_set, salt=salt).twin
