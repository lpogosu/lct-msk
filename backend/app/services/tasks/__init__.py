"""Регулярные короткие задачи [доп.] (концепция §3.1 шаг 14, §3.2 шаг 12, §5.4, DECISIONS 3).

Контракт для подбора (app/services/matching/sources.load_tasks читает task_assignments):
- завершённая задача: status ∈ {auto_scored (тип A), reviewed (тип B)}, score — доля 0..1
  (тип A — 1/0, тип B — сумма рубрики / максимум), finished_at — момент итога, module и family_id — модуль
  теста (подтверждение навыка в K), employer_tasks.tags — навыки задачи (бонус свежести «тег ∈ бриф»);
- проверочный набор по вакансии — назначения с source = "check_set" и need_id брифа.
"""

from __future__ import annotations

from .common import SCORED, SOURCE_CHECK_SET, SOURCE_OFFER

__all__ = ["SCORED", "SOURCE_CHECK_SET", "SOURCE_OFFER"]
