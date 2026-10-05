"""«Антиподсказчик»: двойник в буфере, улики, акт сверки, «Подтверждение за 5 минут», песочница жюри.

Спецификация: _internal/design/spec-antiassist.md (§2.1, §3, §7), _internal/design/killer-features.md (§2.1 п. 1–3, §3).
Пакет чистый: без БД, HTTP и вызовов LLM; ключи двойников — только исполнением (run_python / run_js / run_sql).
Все пороги и тексты — config/anticheat.yaml. Отчёт о покрытии: `uv run python -m app.testing.anticheat.report`.

Как подключать (сервис тестирования):
1. Выдача пула: `twin = family.twin(instance)` (хук Family.twin → twins.make_twin; None — безопасного двойника нет,
   причина — `try_make_twin(...).reason`; пул-гейт: `try_make_twin(..., pool_key_hashes=gates.pool_answer_hashes(...)[i])`).
   В БД: `twin.twin_key` (шифруется как ключ), `twin.twin_key_hash`, `twin.mutation_json()`; клиенту — `twin.client_clip(render_id)`.
2. Ответ: `observe_answer(family, instance, twin, answer, seq=…, render_id=…, self_paste=…)` → `TwinObservation`;
   ответ двойника для оценки уровня всегда неверный (`AnswerClass.scored_correct`).
3. Закрытие сессии: `aggregate_session(observations, warned_at=…, a11y_mode=…)` → предлагаемый уровень и улики;
   автоматического бана нет, работодателю улики не отдаются (`to_dict("methodist" | "candidate")`).
4. Методист: `build_act_for(...)` — акт сверки. Работодатель после «да»: `build_kit(...)` — «Подтверждение за 5 минут»,
   итог `evaluate_verification(...).to_expert_label()`. Жюри: `build_sandbox(seed)` / `score_sandbox(test, answers)`.
"""

from app.testing.anticheat.act import ReconciliationAct, build_act, build_act_for
from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.evidence import (
    AnswerClass,
    SessionTwinSummary,
    TwinEvidence,
    TwinObservation,
    aggregate_session,
    classify_answer,
    classify_answer_ex,
    observe_answer,
)
from app.testing.anticheat.gates import registry_coverage, twin_gate
from app.testing.anticheat.sandbox import SandboxResult, SandboxTest, build_sandbox, score_sandbox
from app.testing.anticheat.twins import ChangedLiteral, Twin, TwinResult, make_twin, try_make_twin
from app.testing.anticheat.verification import (
    SessionItem,
    VerificationKit,
    VerificationResult,
    build_kit,
    evaluate_verification,
)

__all__ = [
    "AnswerClass",
    "AntiCheatConfig",
    "ChangedLiteral",
    "ReconciliationAct",
    "SandboxResult",
    "SandboxTest",
    "SessionItem",
    "SessionTwinSummary",
    "Twin",
    "TwinEvidence",
    "TwinObservation",
    "TwinResult",
    "VerificationKit",
    "VerificationResult",
    "aggregate_session",
    "build_act",
    "build_act_for",
    "build_kit",
    "build_sandbox",
    "classify_answer",
    "classify_answer_ex",
    "evaluate_verification",
    "load_anticheat_config",
    "make_twin",
    "observe_answer",
    "registry_coverage",
    "score_sandbox",
    "try_make_twin",
    "twin_gate",
]
