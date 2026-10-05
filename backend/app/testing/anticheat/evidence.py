"""Улики «двойника»: классификация ответа и агрегирование по сессии (spec-antiassist §2.1, §3; killer-features §3.3–3.4).

Что доказывает улика: ответ совпал с ключом текста X′ из буфера обмена, а не с ключом показанного на экране X.
Это наблюдение о тексте, а не вывод «использовал ИИ»: тот же след оставляют IDE и помощь друга.

Правила (все числа — в config/anticheat.yaml):
- ответ двойника ВСЕГДА засчитывается неверным для оценки уровня: это неверный ответ на X;
- первое попадание — «состояние предупреждения» (тост при первом копировании), повтор после предупреждения —
  материал для методиста; до предупреждения уровень не выше «С оговоркой»;
- не больше одного попадания на класс мутации (защита от коррелированных заблуждений);
- знаменатель — все задания с двойником, на которые дан ответ; self_paste и устаревшая копия (другой
  render_id) в улику не идут;
- вес в доверии 0, пока ε не откалибрована по людям (killer-features §3.4): всё это контекст для методиста;
- АВТОМАТИЧЕСКОГО БАНА НЕТ: итог — только предлагаемый уровень, решает методист.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.twins import Twin
from app.testing.bank.base import Family, Instance, canonical

Kind = Literal["correct", "twin_trace", "misconception", "other"]
Matched = Literal["key", "twin_key", "twin_err", "twin_token", "distractor", "none"]
Phase = Literal["before_warning", "after_warning", "unknown"]

BEFORE: Phase = "before_warning"
AFTER: Phase = "after_warning"
UNKNOWN: Phase = "unknown"

_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-zА-Яа-яЁё_]+")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def tokens_of(text: str) -> set[str]:
    return set(_TOKEN.findall(text))


def _equal_up_to_rounding(answer: str, twin_key: str, tolerance: float) -> bool:
    """Ключ — дробь (среднее): чат-бот округляет, поэтому «9586.67» считаем ответом «9586.666…». Целые не размываем.

    tolerance — evidence.rounding_tolerance из config/anticheat.yaml (округление до сотых).
    """
    if "." not in twin_key:
        return False
    a, b = _NUMBER.findall(answer), _NUMBER.findall(twin_key)
    if not b or len(a) != len(b) or _NUMBER.sub("#", answer) != _NUMBER.sub("#", twin_key):
        return False
    return all(abs(float(x) - float(y)) <= tolerance for x, y in zip(a, b, strict=True))


# ----------------------------------------------------------------------------- классификация ответа


@dataclass(frozen=True)
class AnswerClass:
    kind: Kind
    matched: Matched
    # exact (ключ двойника) | err_twin (типичная ошибка на тексте из буфера) | token (значение не с экрана) | None
    strength: str | None = None
    tag: str | None = None  # тег заблуждения для misconception

    @property
    def scored_correct(self) -> bool:
        """Верность для оценки уровня: ответ двойника — это неверный ответ на X, всегда."""
        return self.kind == "correct"

    @property
    def is_exact_twin(self) -> bool:
        return self.kind == "twin_trace" and self.strength == "exact"


def classify_answer_ex(
    answer: Any,
    *,
    key: Any,
    twin_key: Any | None,
    distractors: Mapping[str, str] | None = None,
    normalize: Callable[[Any], Any] | None = None,
    twin_to: str | None = None,
    known_text: str = "",
    err_twin_keys: Collection[str] | None = None,
    rounding_tolerance: float | None = None,
) -> AnswerClass:
    """Ответ против (key, twin_key, distractors): correct | twin_trace | misconception | other.

    Порядок важен: сначала верный, затем известное заблуждение (если ключ двойника случайно совпал с дистрактором,
    не обвиняем), затем ключ двойника, затем слабые следы (только контекст, вес 0): типичная ошибка на тексте из
    буфера (K_err(X′) без пересечения с ошибками на экране) и «значение, которого нет на экране».
    """
    norm = normalize(answer) if normalize is not None else answer
    ca = canonical(norm).strip()
    if ca == canonical(key).strip():
        return AnswerClass("correct", "key")
    if distractors and ca in distractors:
        return AnswerClass("misconception", "distractor", tag=distractors[ca])
    if twin_key is not None:
        ck = canonical(twin_key).strip()
        tol = load_anticheat_config().evidence.rounding_tolerance if rounding_tolerance is None else rounding_tolerance
        if ca == ck or _equal_up_to_rounding(ca, ck, tol):
            return AnswerClass("twin_trace", "twin_key", "exact")
    if err_twin_keys and ca in err_twin_keys:
        return AnswerClass("twin_trace", "twin_err", "err_twin")
    if twin_to:
        seen = tokens_of(known_text) | tokens_of(canonical(key))
        if twin_to in tokens_of(ca) and twin_to not in seen:
            return AnswerClass("twin_trace", "twin_token", "token")
    return AnswerClass("other", "none")


def classify_answer(
    answer: Any,
    *,
    key: Any,
    twin_key: Any | None,
    distractors: Mapping[str, str] | None = None,
    normalize: Callable[[Any], Any] | None = None,
    twin_to: str | None = None,
    known_text: str = "",
) -> Kind:
    return classify_answer_ex(
        answer, key=key, twin_key=twin_key, distractors=distractors, normalize=normalize, twin_to=twin_to,
        known_text=known_text,
    ).kind


def classify_for_twin(fam: Family, inst: Instance, twin: Twin, answer: Any) -> AnswerClass:
    """Классификация по семейству (его normalize) и двойнику экземпляра."""
    return classify_answer_ex(
        answer,
        key=inst.key,
        twin_key=twin.twin_key,
        distractors=inst.distractors,
        normalize=fam.normalize,
        twin_to=twin.changed_literal.to,
        known_text=twin.shown_text,
        err_twin_keys=twin.err_trace_keys,
    )


# ----------------------------------------------------------------------------- вероятности


def p_at_least(k: int, probs: Sequence[float]) -> float:
    """P(не менее k успехов) для независимых испытаний с вероятностями probs (Пуассон-биномиальное, точно)."""
    if k <= 0:
        return 1.0
    if k > len(probs):
        return 0.0
    dist = [1.0]
    for p in probs:
        nxt = [0.0] * (len(dist) + 1)
        for i, q in enumerate(dist):
            nxt[i] += q * (1.0 - p)
            nxt[i + 1] += q * p
        dist = nxt
    return float(min(1.0, max(0.0, sum(dist[k:]))))


def combine_honest(eps: float, family_collision_rate: float = 0.0) -> float:
    """P(честное совпадение на задании): ε модели заблуждений и эмпирическая доля коллизий ключей семейства."""
    return 1.0 - (1.0 - eps) * (1.0 - max(0.0, family_collision_rate))


def family_collision_rates(stats: Iterable[Any]) -> dict[str, float]:
    """{family_id: эмпирическая доля кандидатов-двойников, ключ которых совпал бы с ответом соседнего экземпляра пула}.

    Плотность ключей семейства: берётся из gates.FamilyTwinStats (любой объект с family_id и natural_collision); передаётся в
    aggregate_session(family_collision_rates=…), чтобы вероятность честного совпадения учитывала плотность
    ключей семейства, а не только модель заблуждений.
    """
    return {s.family_id: float(s.natural_collision) for s in stats if getattr(s, "eligible", True)}


_SUP = str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹")


def format_count(x: float) -> str:
    """0,3 · 2,3 · 23 — число случаев на 10 000 сессий с русской десятичной запятой."""
    return f"{x:.0f}" if x >= 10 else f"{x:.1f}".replace(".", ",")


def format_probability(p: float) -> str:
    """0,023 · 3,2·10⁻⁵ — с русской десятичной запятой."""
    if p <= 0:
        return "0"
    if p >= 0.01:
        return f"{p:.3f}".replace(".", ",")
    mant, exp = f"{p:.1e}".split("e")
    return f"{mant.replace('.', ',')}·10{str(int(exp)).translate(_SUP)}"


# ----------------------------------------------------------------------------- наблюдения и улики


@dataclass(frozen=True)
class TwinObservation:
    """Ответ на задание с двойником: всё, что нужно для агрегирования (без доступа к БД)."""

    seq: int
    family_id: str
    variant_code: str
    mutation_class: str
    mutation_subclass: str
    answer: str
    answer_class: AnswerClass
    honest_p: float
    render_id: str | None = None
    twin_render_id: str | None = None
    self_paste: bool = False
    issued_at: datetime | None = None
    answered_at: datetime | None = None
    copy_at: datetime | None = None
    has_twin: bool = True


def observe_answer(
    fam: Family,
    inst: Instance,
    twin: Twin,
    answer: Any,
    *,
    seq: int,
    render_id: str | None = None,
    twin_render_id: str | None = None,
    self_paste: bool = False,
    issued_at: datetime | None = None,
    answered_at: datetime | None = None,
    copy_at: datetime | None = None,
) -> TwinObservation:
    return TwinObservation(
        seq=seq,
        family_id=fam.id,
        variant_code=inst.variant_code,
        mutation_class=twin.mutation_class,
        mutation_subclass=twin.mutation_subclass,
        answer=str(answer),
        answer_class=classify_for_twin(fam, inst, twin, answer),
        honest_p=twin.honest_collision_estimate,
        render_id=render_id,
        twin_render_id=twin_render_id,
        self_paste=self_paste,
        issued_at=issued_at,
        answered_at=answered_at,
        copy_at=copy_at,
    )


@dataclass(frozen=True)
class TwinEvidence:
    """Запись улики по одному заданию (карточка методиста строится по ней и по Twin)."""

    seq: int
    family_id: str
    variant_code: str
    mutation_class: str
    mutation_subclass: str
    answer: str
    answer_class: AnswerClass
    phase: Phase
    honest_p: float
    counted: bool  # учтено в числе попаданий (одно на класс)
    excluded: str | None = None  # self_paste | stale_clip
    render_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "family_id": self.family_id,
            "variant_code": self.variant_code,
            "mutation_class": self.mutation_class,
            "mutation_subclass": self.mutation_subclass,
            "answer": self.answer,
            "kind": self.answer_class.kind,
            "matched": self.answer_class.matched,
            "strength": self.answer_class.strength,
            "phase": self.phase,
            "honest_p": self.honest_p,
            "counted": self.counted,
            "excluded": self.excluded,
            "render_id": self.render_id,
        }


@dataclass(frozen=True)
class SessionTwinSummary:
    state: str  # none | warning | evidence | review_candidate
    suggested_level: str  # confirmed | note | under_review — что предложили бы правила при откалиброванной ε
    proposed_level: str  # фактический: пока ε не откалибрована — confirmed (контекст без веса)
    context_only: bool
    trust_weight: float
    trust_signal: float  # вклад в доверие = вес × (есть улика); при весе 0 равен 0
    n_twin_items: int
    n_counted_hits: int
    p_honest_session: float  # P(не менее k попаданий у честного)
    expected_false_per_10k: float
    hits: tuple[TwinEvidence, ...]
    context: tuple[TwinEvidence, ...]  # слабые следы (значение, которого нет на экране) и исключённые ответы
    items: tuple[TwinEvidence, ...]
    theta_correct: dict[int, bool]  # seq → верно ли для оценки уровня (ответы двойника — неверно)
    a11y_mode: bool
    auto_ban: bool = False  # константа: автобана нет

    @property
    def methodist_attention(self) -> bool:
        return self.state in ("evidence", "review_candidate")

    @property
    def theta_exclusions_if_dismissed(self) -> frozenset[int]:
        """Задания, исключаемые из оценки уровня, если методист снял флаг (status = dismissed)."""
        return frozenset(h.seq for h in self.hits)

    def theta_items(self, dismissed: bool) -> dict[int, bool]:
        """Верность по заданиям для пересчёта уровня: при снятии флага спорные задания исключаются."""
        if not dismissed:
            return dict(self.theta_correct)
        drop = self.theta_exclusions_if_dismissed
        return {s: v for s, v in self.theta_correct.items() if s not in drop}

    def to_dict(self, audience: str = "methodist", cfg: AntiCheatConfig | None = None) -> dict[str, Any]:
        cfg = cfg or load_anticheat_config()
        if audience not in ("methodist", "candidate"):
            # Работодатель улик не видит никогда (spec §2, M5): для него нет представления, только решение методиста.
            raise ValueError(f"улики отдаются только методисту и кандидату, не «{audience}»")
        if audience == "candidate":
            # Кандидат видит наблюдения только после закрытия сессии и только номер задания (spec §2, §2.1).
            if not self.hits:
                return {"observations": []}
            return {
                "observations": [
                    {
                        "variant_code": h.variant_code,
                        "message_ru": cfg.text("candidate_message", variant=h.variant_code),
                        "actions": list(cfg.raw_text("candidate_actions")),
                    }
                    for h in self.hits
                ]
            }
        return {
            "state": self.state,
            "state_ru": cfg.text(f"states_ru.{self.state}"),
            "suggested_level": self.suggested_level,
            "proposed_level": self.proposed_level,
            "proposed_level_ru": cfg.text(f"levels_ru.{self.proposed_level}"),
            "context_only": self.context_only,
            "trust_weight": self.trust_weight,
            "trust_signal": self.trust_signal,
            "n_twin_items": self.n_twin_items,
            "n_counted_hits": self.n_counted_hits,
            "p_honest_session": self.p_honest_session,
            "p_honest_text": cfg.text(
                "probability_line", p=format_probability(self.p_honest_session), n=format_count(self.expected_false_per_10k)
            ),
            "expected_false_per_10k": self.expected_false_per_10k,
            "hits": [h.to_dict() for h in self.hits],
            "context": [h.to_dict() for h in self.context],
            "a11y_mode": self.a11y_mode,
            "auto_ban": False,
            "decision": "только методист",
        }


def _class_key(o: TwinObservation, mode: str) -> str:
    if mode == "family":
        return o.family_id
    if mode == "mutation_class":
        return o.mutation_class
    return o.mutation_subclass


def aggregate_session(
    observations: Sequence[TwinObservation],
    *,
    warned_at: datetime | None = None,
    a11y_mode: bool = False,
    family_collision_rates: Mapping[str, float] | None = None,
    cfg: AntiCheatConfig | None = None,
) -> SessionTwinSummary:
    """Свести ответы на задания с двойником в состояние и предлагаемый уровень (решение — за методистом)."""
    cfg = cfg or load_anticheat_config()
    ev = cfg.evidence
    if ev.auto_ban:
        raise ValueError("auto_ban = true недопустим: автоматического бана нет (spec-antiassist §3)")
    rates = family_collision_rates or {}
    items = sorted((o for o in observations if o.has_twin), key=lambda o: o.seq)

    def excluded_reason(o: TwinObservation) -> str | None:
        if o.self_paste:
            return "self_paste"
        if o.render_id is not None and o.twin_render_id is not None and o.render_id != o.twin_render_id:
            return "stale_clip"
        return None

    exact = [o for o in items if o.answer_class.is_exact_twin and excluded_reason(o) is None]
    # --- фаза: до/после предупреждения
    phases: dict[int, Phase] = {}
    first_hit_seq = exact[0].seq if exact else None
    for o in items:
        if warned_at is not None:
            moment = o.issued_at or o.answered_at
            phases[o.seq] = UNKNOWN if moment is None else (AFTER if moment > warned_at else BEFORE)
        elif o.seq == first_hit_seq:
            phases[o.seq] = BEFORE  # первое попадание само запускает состояние предупреждения
        else:
            phases[o.seq] = AFTER if first_hit_seq is not None and o.seq > first_hit_seq else UNKNOWN

    # --- одно попадание на класс: предпочитаем «после предупреждения», затем более раннее
    best: dict[str, TwinObservation] = {}
    for o in exact:
        k = _class_key(o, ev.one_hit_per)
        cur = best.get(k)
        rank = (phases[o.seq] == AFTER, -o.seq)
        if cur is None or rank > (phases[cur.seq] == AFTER, -cur.seq):
            best[k] = o
    counted_seqs = {o.seq for o in best.values()}

    def evidence_of(o: TwinObservation) -> TwinEvidence:
        return TwinEvidence(
            seq=o.seq,
            family_id=o.family_id,
            variant_code=o.variant_code,
            mutation_class=o.mutation_class,
            mutation_subclass=o.mutation_subclass,
            answer=o.answer,
            answer_class=o.answer_class,
            phase=phases[o.seq],
            honest_p=combine_honest(o.honest_p, rates.get(o.family_id, 0.0)),
            counted=o.seq in counted_seqs,
            excluded=excluded_reason(o),
            render_id=o.render_id,
        )

    all_ev = tuple(evidence_of(o) for o in items)
    hits = tuple(e for e in all_ev if e.counted)
    context = tuple(
        e for e in all_ev if not e.counted and (e.answer_class.kind == "twin_trace" or e.excluded is not None)
    )

    probs = [e.honest_p for e in all_ev]
    k = len(hits)
    p_sess = p_at_least(k, probs) if k else 1.0
    after_hits = [h for h in hits if h.phase == AFTER]
    classes = {h.mutation_subclass for h in hits}

    # --- состояние
    if not hits:
        state = "none"
    elif not after_hits:
        state = "warning"
    else:
        state = "evidence"
        if (
            not a11y_mode
            and k >= ev.review_min_hits
            and len(after_hits) >= ev.review_min_after_warning
            and len(classes) >= ev.review_min_hits
            and p_sess <= ev.review_p_threshold
        ):
            state = "review_candidate"

    levels = ev.levels
    if state == "none":
        suggested = levels["confirmed"]
    elif state == "review_candidate":
        suggested = levels["under_review"]
    elif state == "warning":
        suggested = levels[ev.cap_before_warning]  # попадание до предупреждения: потолок из конфига («С оговоркой»)
    else:
        suggested = levels[ev.cap_a11y_mode] if a11y_mode else levels["note"]  # в режиме доступности — свой потолок
    proposed = suggested if ev.calibrated else levels["confirmed"]
    weight = ev.trust_weight if ev.calibrated else 0.0
    signal = weight * (1.0 if hits else 0.0)

    return SessionTwinSummary(
        state=state,
        suggested_level=suggested,
        proposed_level=proposed,
        context_only=not ev.calibrated,
        trust_weight=weight,
        trust_signal=signal,
        n_twin_items=len(items),
        n_counted_hits=k,
        p_honest_session=p_sess,
        expected_false_per_10k=p_sess * ev.per_10k,
        hits=hits,
        context=context,
        items=all_ev,
        theta_correct={o.seq: o.answer_class.scored_correct for o in items},
        a11y_mode=a11y_mode,
    )
