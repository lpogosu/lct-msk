"""Тест Frontend (JS/TS) не показывает код на Python: общие семейства «Алгоритмы» и token bucket имеют JS-версии
(`*.js`), а Python-оригиналы выдаются только бэкенд-шкалам (пилот с ИИ-испытуемыми, VALIDATION §10)."""

from __future__ import annotations

from app.testing.bank.registry import all_families


def test_frontend_families_never_render_python() -> None:
    fe = [f for f in all_families().values() if "frontend_js" in getattr(f, "specs", ())]
    assert fe
    for fam in fe:
        for seed in (1, 2, 3):
            assert fam.generate(seed).rendered.language != "python", fam.id


def test_js_ports_exist_for_shared_algo_module() -> None:
    ids = set(all_families())
    ports = {i for i in ids if i.endswith(".js")}
    assert len(ports) >= 9
    for p in ports:
        assert p.removesuffix(".js") in ids  # Python-оригинал остаётся для backend/qa/devops
