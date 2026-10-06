"""Данные для страницы жюри «Попробуй списать» (killer-features §2.1 п. 3).

Мини-тест из 4 экземпляров РАЗНЫХ семейств, у каждого есть двойник; набор уникален для каждого проверяющего
(зерно проверяющего → семейства и сиды), результат детерминирован по зерну. Проверяющий списывает как хочет;
после ответов видит диагноз по каждому заданию и акт сверки:
- ответ = ключ двойника → «вы копировали текст — пойман двойником»;
- ответ верный → «скриншот и ручной ввод двойник не ловит — работает подтверждение за 5 минут»;
- иначе — честно: двойник не сработал.
Сессии песочницы помечаются demo_assisted и исключаются из метрик. Ключи на клиент не уходят
(SandboxTest.to_client() их не содержит). Поведение copy на мобильных не проверено: в README «с ноутбука».
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.testing.anticheat.act import ReconciliationAct, build_act
from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.dispatch import family_twin_result, twin_applicable
from app.testing.anticheat.evidence import AnswerClass, classify_for_twin
from app.testing.anticheat.twins import Twin
from app.testing.bank.base import Family, Instance
from app.testing.bank.registry import all_families


@dataclass(frozen=True)
class SandboxItem:
    no: int  # 1…n
    family_id: str
    seed: int
    variant_code: str
    instance: Instance
    twin: Twin

    def to_client(self, render_id: str | None = None) -> dict[str, Any]:
        """Задание для страницы: условие и clip (текст X′ + карта подмены). Ключей здесь нет."""
        out = self.instance.rendered.to_public()
        out.update(no=self.no, variant_code=self.variant_code, clip=self.twin.client_clip(render_id))
        return out


@dataclass(frozen=True)
class SandboxTest:
    test_id: str
    reviewer_seed: str
    items: tuple[SandboxItem, ...]
    banner_ru: str
    readme_note_ru: str
    demo_assisted: bool = True  # сессия исключается из метрик

    def to_client(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "banner_ru": self.banner_ru,
            "readme_note_ru": self.readme_note_ru,
            "demo_assisted": self.demo_assisted,
            "items": [it.to_client(render_id=f"{self.test_id}:{it.no}") for it in self.items],
        }


_POOL_CACHE: dict[tuple[str, ...], dict[str, list[str]]] = {}


def sandbox_pool(registry: Mapping[str, Family], cfg: AntiCheatConfig) -> dict[str, list[str]]:
    """Семейства, подходящие по форме для двойника, сгруппированные по языку (без исполнения)."""
    cache_key = (*sorted(registry), *cfg.twins.supported_formats, *cfg.twins.eligible_languages)
    if cache_key in _POOL_CACHE:
        return {k: list(v) for k, v in _POOL_CACHE[cache_key].items()}
    pool: dict[str, list[str]] = {}
    for fid in sorted(registry):
        fam = registry[fid]
        if fam.format not in cfg.twins.supported_formats:
            continue
        try:
            inst = fam.generate(0)
        except Exception:  # noqa: BLE001
            continue
        if twin_applicable(fam, inst, cfg):  # в том числе семейства со своим Family.twin() (QA, DevOps)
            pool.setdefault(inst.rendered.language or "", []).append(fid)
    _POOL_CACHE[cache_key] = {k: list(v) for k, v in pool.items()}
    return pool


def build_sandbox(
    reviewer_seed: int | str,
    *,
    registry: Mapping[str, Family] | None = None,
    cfg: AntiCheatConfig | None = None,
) -> SandboxTest:
    """Мини-тест песочницы для проверяющего: 4 экземпляра из разных семейств, у каждого двойник."""
    cfg = cfg or load_anticheat_config()
    reg = registry if registry is not None else all_families()
    sp = cfg.sandbox
    rng = random.Random(f"sandbox#{reviewer_seed}")
    pool = sandbox_pool(reg, cfg)
    order = [lang for lang in sp.language_order if pool.get(lang)] or sorted(pool)
    if not order:
        raise ValueError("нет семейств, пригодных для двойника")
    items: list[SandboxItem] = []
    used: set[str] = set()
    attempts = 0
    while len(items) < sp.n_items and attempts < sp.max_build_attempts:
        lang = order[len(items) % len(order)]
        candidates = [f for f in pool[lang] if f not in used]
        if not candidates:
            candidates = [f for v in pool.values() for f in v if f not in used]
            if not candidates:
                break
        fid = candidates[rng.randrange(len(candidates))]
        fam = reg[fid]
        base_seed = rng.randrange(10**6)
        for step in range(4):
            attempts += 1
            seed = base_seed + step
            inst = fam.generate(seed)
            res = family_twin_result(fam, inst, cfg=cfg)  # Family.twin() первым, затем общий мутатор
            if res.twin is not None:
                items.append(SandboxItem(len(items) + 1, fid, seed, inst.variant_code, inst, res.twin))
                used.add(fid)
                break
        else:
            used.add(fid)  # семейство не дало двойника на этих сидах: пробуем другое
    if len(items) < sp.n_items:
        raise ValueError(f"не удалось собрать {sp.n_items} заданий с двойниками (собрано {len(items)})")
    test_id = hashlib.sha256(
        "|".join([str(reviewer_seed), *(f"{i.family_id}:{i.seed}" for i in items)]).encode()
    ).hexdigest()[:12]
    return SandboxTest(
        test_id=test_id,
        reviewer_seed=str(reviewer_seed),
        items=tuple(items),
        banner_ru=cfg.text("sandbox.banner"),
        readme_note_ru=cfg.text("sandbox.readme_note"),
    )


# ----------------------------------------------------------------------------- оценка


@dataclass(frozen=True)
class ItemDiagnosis:
    no: int
    variant_code: str
    answer: str
    answer_class: AnswerClass | None
    channel: str  # clipboard_text | screen_or_retyped | misconception | unknown
    channel_ru: str
    caught: bool
    diagnosis_ru: str
    key_shown: str
    key_clipboard: str
    act: ReconciliationAct | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "no": self.no,
            "variant_code": self.variant_code,
            "answer": self.answer,
            "kind": self.answer_class.kind if self.answer_class else "blank",
            "matched": self.answer_class.matched if self.answer_class else "none",
            "channel": self.channel,
            "channel_ru": self.channel_ru,
            "caught": self.caught,
            "diagnosis_ru": self.diagnosis_ru,
            "key_shown": self.key_shown,
            "key_clipboard": self.key_clipboard,
            "act": self.act.to_dict("methodist") if self.act else None,
        }


@dataclass(frozen=True)
class SandboxResult:
    test_id: str
    items: tuple[ItemDiagnosis, ...]
    total: int
    caught: int
    correct_uncaught: int
    summary_ru: str
    demo_assisted: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "demo_assisted": self.demo_assisted,
            "total": self.total,
            "caught": self.caught,
            "correct_uncaught": self.correct_uncaught,
            "summary_ru": self.summary_ru,
            "items": [i.to_dict() for i in self.items],
        }


def score_sandbox(
    test: SandboxTest,
    answers: Mapping[int | str, Any],
    *,
    registry: Mapping[str, Family] | None = None,
    cfg: AntiCheatConfig | None = None,
) -> SandboxResult:
    """Диагноз по каждому заданию: какой канал сработал и поймал ли его двойник."""
    cfg = cfg or load_anticheat_config()
    reg = registry if registry is not None else all_families()
    by_no = {str(k): v for k, v in answers.items()}
    diagnoses: list[ItemDiagnosis] = []
    for it in test.items:
        fam = reg[it.family_id]
        raw = by_no.get(str(it.no))
        text = "" if raw is None else str(raw)
        c = it.twin.changed_literal
        base = {"no": it.no, "variant_code": it.variant_code, "answer": text, "key_shown": it.twin.key,
                "key_clipboard": it.twin.twin_key}
        if not text.strip():
            diagnoses.append(
                ItemDiagnosis(
                    answer_class=None, channel="unknown", channel_ru=cfg.text("sandbox.channel_ru.unknown"),
                    caught=False, diagnosis_ru=cfg.text("sandbox.blank"), **base,
                )
            )
            continue
        ac = classify_for_twin(fam, it.instance, it.twin, text)
        act: ReconciliationAct | None = None
        if ac.kind == "twin_trace":
            channel, caught = "clipboard_text", ac.strength == "exact"
            if caught:
                msg = cfg.text("sandbox.caught", answer=text, key=it.twin.key)
            elif ac.strength == "err_twin":
                msg = cfg.text("sandbox.err_twin")
            else:
                msg = cfg.text("sandbox.token", to=c.to)
            act = build_act(it.twin, answer=text, answer_class=ac, cfg=cfg)
        elif ac.kind == "correct":
            channel, caught, msg = "screen_or_retyped", False, cfg.text("sandbox.correct")
        elif ac.kind == "misconception":
            channel, caught, msg = "misconception", False, cfg.text("sandbox.misconception")
        else:
            channel, caught, msg = "unknown", False, cfg.text("sandbox.other")
        diagnoses.append(
            ItemDiagnosis(
                answer_class=ac, channel=channel, channel_ru=cfg.text(f"sandbox.channel_ru.{channel}"),
                caught=caught, diagnosis_ru=msg, act=act, **base,
            )
        )
    caught_n = sum(d.caught for d in diagnoses)
    correct_n = sum(1 for d in diagnoses if d.answer_class is not None and d.answer_class.kind == "correct")
    return SandboxResult(
        test_id=test.test_id,
        items=tuple(diagnoses),
        total=len(diagnoses),
        caught=caught_n,
        correct_uncaught=correct_n,
        summary_ru=cfg.text("sandbox.summary", caught=caught_n, total=len(diagnoses), correct=correct_n),
        demo_assisted=test.demo_assisted,
    )
