"""Семейства-«сценарии»: точный решатель по ПОКАЗАННОМУ тексту и «двойник в буфере» для нейтральных форматов.

Зачем. Общий мутатор двойников (anticheat.twins) умеет только исполняемый код (python / javascript) и таблицы sqlite.
Задания QA и DevOps показывают Dockerfile, nginx.conf, cron-выражение, YAML, файл с pytest-тестами: их «ответ» не печатает
исполнение показанного текста. Здесь ключ считает решатель `solve(сценарий, текст)`, который РАЗБИРАЕТ именно показанный
текст (исполняет тесты, парсит конфиг, считает по таблице), а не внутренние параметры генератора. Поэтому:
- ключ экземпляра воспроизводим по тому, что видит кандидат (проверяется и при построении двойника);
- двойник = тот же показанный текст с ОДНИМ изменённым числовым литералом и ключом, пересчитанным тем же решателем
  (spec-antiassist §2.1, §7); гейты те же, что у общего мутатора: ключи различны, ключ двойника не равен дистрактору и
  не равен ключу типичной ошибки (K_err — те же «мутанты-заблуждения»), правка в один токен, длина ±1, без путаемых глифов.

Дистракторы — тоже результат решателя с включённым заблуждением (`solve(..., mutant=тег)`): мутант, который не
применим к экземпляру, возвращает None и пропускается.

Контракт семейства-наследника:
    make(rng) -> Scenario        параметры и показанный текст; GenerationRetry — перегенерировать
    prompt(sc) -> str            формулировка (не зависит от литералов, которые мутирует двойник)
    solve(sc, code, mutant=None) точный ответ (строка) по тексту code; mutant — тег из `misconceptions`
    why(sc, raw_key) -> str      разбор
"""

from __future__ import annotations

import dataclasses
import random
import re
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from app.testing.bank.base import Instance, canonical
from app.testing.bank.families.backend_python._common import (
    NORMALIZERS,
    BankFamily,
    GenerationRetry,
    norm_output,
    rng_for,
)

if TYPE_CHECKING:
    from app.testing.anticheat.twins import Twin, TwinResult


class ScenarioError(Exception):
    """Решатель не смог разобрать текст (для двойника — отбраковка кандидата, для генерации — перегенерация)."""


@dataclass(frozen=True)
class Scenario:
    code: str  # показанный текст; именно его разбирает solve и именно в нём двойник меняет литерал
    data: Any = None  # параметры из условия (не мутируются двойником)


# ---------------------------------------------------------------- нормализация ответов (расширение NORMALIZERS)

_INT = re.compile(r"-?\d+")
_PYTEST_WORD = re.compile(r"(\d+)\s*(failed|passed|skipped|xfailed|xpassed|errors?|deselected|warnings?)", re.I)
_PYTEST_ORDER = ("failed", "passed", "skipped", "deselected", "xfailed", "xpassed", "warnings", "error")
_DT_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T]+(\d{1,2}):(\d{2})")
_DT_RU = re.compile(r"(\d{1,2})\.(\d{2})\.(\d{4})[ ,T]+(\d{1,2}):(\d{2})")
_FRACTION = re.compile(r"(\d+)\s*(?:/|из)\s*(\d+)")
_WS = re.compile(r"\s+")
# Кириллические двойники латинских букв и «длинные» тире: кандидат мог набрать «С» вместо «C».
_LOOKALIKE = str.maketrans("АВЕКМНОРСТУХаеорсух", "ABEKMHOPCTYXaeopcyx")


def norm_ints(answer: Any) -> Any:
    """Последовательность целых: «3, 4 и 5», «3 4 5», «[3, 4, 5]» → «3 4 5». Ничего похожего на число — как есть."""
    if isinstance(answer, list | tuple):
        answer = " ".join(str(x) for x in answer)
    if not isinstance(answer, str):
        return answer
    nums = _INT.findall(answer)
    if not nums:
        return _WS.sub(" ", answer.strip())
    return " ".join(str(int(n)) for n in nums)


def norm_pytest_summary(answer: Any) -> Any:
    """«2 failed, 3 passed in 0.02s» → «2 failed, 3 passed» в порядке итоговой строки pytest."""
    if not isinstance(answer, str):
        return answer
    found: dict[str, int] = {}
    for n, word in _PYTEST_WORD.findall(answer):
        w = word.lower()
        w = "error" if w.startswith("error") else "warnings" if w.startswith("warning") else w
        found[w] = found.get(w, 0) + int(n)
    if not found:
        return _WS.sub(" ", answer.strip())
    return ", ".join(f"{found[w]} {w}" for w in _PYTEST_ORDER if w in found)


def norm_datetimes(answer: Any) -> Any:
    """Моменты времени: «2026-03-02 09:30», «02.03.2026 9:30» → «2026-03-02 09:30», через « ; »."""
    if isinstance(answer, list | tuple):
        answer = " ; ".join(str(x) for x in answer)
    if not isinstance(answer, str):
        return answer
    hits: list[tuple[int, str]] = []
    for m in _DT_ISO.finditer(answer):
        y, mo, d, h, mi = m.groups()
        hits.append((m.start(), f"{y}-{mo}-{d} {int(h):02d}:{mi}"))
    for m in _DT_RU.finditer(answer):
        d, mo, y, h, mi = m.groups()
        hits.append((m.start(), f"{y}-{mo}-{int(d):02d} {int(h):02d}:{mi}"))
    if not hits:
        return _WS.sub(" ", answer.strip())
    return " ; ".join(t for _, t in sorted(hits))


def norm_fraction(answer: Any) -> Any:
    """«5/8», «5 из 8», «5 / 8» → «5/8»."""
    if not isinstance(answer, str):
        return answer
    m = _FRACTION.search(answer)
    return f"{int(m.group(1))}/{int(m.group(2))}" if m else _WS.sub(" ", answer.strip())


_BRANCH = re.compile(r"(\d+)\s*[-:.]?\s*(T|F|Т|true|false|истина|ложь)\b", re.I)


def norm_branches(answer: Any) -> Any:
    """Непокрытые ветви: «12F, 15 T», «12-ложь 15-истина» → «12F 15T»; «нет»/пусто → «нет»."""
    if isinstance(answer, list | tuple):
        answer = " ".join(str(x) for x in answer)
    if not isinstance(answer, str):
        return answer
    text = answer.strip()
    if text.lower() in ("", "нет", "no", "none", "-", "—"):
        return "нет"
    hits = {(int(n), 0 if o.lower() in ("t", "т", "true", "истина") else 1) for n, o in _BRANCH.findall(text)}
    if not hits:
        return _WS.sub(" ", text)
    return " ".join(f"{n}{'T' if k == 0 else 'F'}" for n, k in sorted(hits))


def norm_latin(answer: Any) -> Any:
    """Слова-идентификаторы (имена тестов, блоков, стадий): регистр сохраняется, кириллические двойники → латиница."""
    if not isinstance(answer, str):
        return answer
    return _WS.sub(" ", answer.translate(_LOOKALIKE).replace(",", " ").replace(";", " ").strip())


NORMALIZERS_EXT: dict[str, Callable[[Any], Any]] = {
    **NORMALIZERS,
    "ints": norm_ints,
    "pytest": norm_pytest_summary,
    "datetimes": norm_datetimes,
    "fraction": norm_fraction,
    "branches": norm_branches,
    "latin": norm_latin,
}


# ---------------------------------------------------------------- базовый класс

_DEFAULT_LITERAL = re.compile(r"(?<![\w.])\d+(?=[A-Za-z%]{0,2}(?!\w|\.\d))")


class ModelTwinMixin:
    """«Двойник в буфере» для семейств, у которых ключ считает решатель по ПОКАЗАННОМУ тексту, а не исполнение кода.

    Подмешивается к Family/BankFamily. Семейство даёт: scenario_for(seed) -> Scenario (code = показанный текст,
    data = то, что не мутируется), solve(sc, code, mutant), misconceptions, normalize(). Всё остальное (перебор
    литералов, гейты, K_err) здесь. Подробно — в докстринге модуля."""

    misconceptions: ClassVar[dict[str, str]] = {}

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        """Куда можно подставлять литерал-двойник: диапазоны [начало, конец) в code (по умолчанию — целые числа ≥ 2)."""
        return [m.span() for m in _DEFAULT_LITERAL.finditer(code)]

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: Any) -> list[str]:
        """Новые значения для литерала code[span]. По умолчанию — число с относительным изменением 12–80%.

        Семейство переопределяет (вместе с twin_spans), если ответ зависит от НЕчисловых токенов показанного текста
        (имя изменённого файла, метка стадии): возвращайте правдоподобные замены из того же словаря, что и в генераторе.
        """
        old = code[span[0] : span[1]]
        return self._propose(old, rng, p) if self._literal_ok(old, p.min_value) else []

    def twin(self, inst: Instance, seed: int | None = None) -> Twin | None:
        return self.try_twin(inst, seed).twin

    def try_twin(self, inst: Instance, seed: int | None = None) -> TwinResult:  # noqa: C901, PLR0912, PLR0915
        """Двойник или причина отказа (коды те же, что у anticheat.twins.try_make_twin)."""
        # Отложенный импорт (как в Family.twin): нет цикла семейства ↔ anticheat.
        from app.testing.anticheat.config import load_anticheat_config  # noqa: PLC0415
        from app.testing.anticheat.twins import (  # noqa: PLC0415
            ChangedLiteral,
            Twin,
            TwinResult,
            _edit_gates,
            _line_col,
            compose_text,
            hash_answer,
            honest_collision_estimate,
        )

        r = inst.rendered
        if r.answer_mode != "text":
            return TwinResult(None, "answer_mode_not_text")
        if not r.code:
            return TwinResult(None, "no_code")
        cfg = load_anticheat_config()
        p = cfg.twins
        try:
            sc = self.scenario_for(inst.seed)
            if sc.code != r.code:
                return TwinResult(None, "key_not_reproducible", detail="code")
            raw = self.solve(sc, r.code)
        except (ScenarioError, RuntimeError) as e:
            return TwinResult(None, "executor_failed", detail=str(e)[:200])
        key_norm = canonical(inst.key)
        if raw is None or canonical(self.normalize(raw)) != key_norm:
            return TwinResult(None, "key_not_reproducible")  # ключ не воспроизводится по показанному тексту

        def kerr(code: str) -> dict[str, str]:
            out: dict[str, str] = {}
            for tag in self.misconceptions:
                try:
                    val = self.solve(sc, code, tag)
                except ScenarioError:
                    continue
                if val is not None:
                    out.setdefault(canonical(self.normalize(val)), tag)
            return out

        shown = compose_text(r, [])
        base_k_err = kerr(r.code)
        distractors = set(inst.distractors)
        rng_base = f"{self.id}#{inst.seed if seed is None else seed}#twin"
        order_rng = random.Random(rng_base + "#order")
        spans = list(self.twin_spans(sc, r.code))
        order_rng.shuffle(spans)
        rejects: dict[str, int] = {}
        attempts = 0
        for ci, (a, b) in enumerate(spans[: p.max_literals]):
            old = r.code[a:b]
            proposals = self.twin_values(sc, r.code, (a, b), random.Random(f"{rng_base}#{ci}"), p)
            for new in proposals[: p.max_values_per_literal]:
                if attempts >= p.max_attempts:
                    return TwinResult(None, "all_candidates_rejected", attempts, rejects, "scenario_solver")
                new_code = r.code[:a] + new + r.code[b:]
                try:
                    raw_t = self.solve(sc, new_code)
                except ScenarioError:
                    rejects["exec_failed"] = rejects.get("exec_failed", 0) + 1
                    continue
                if raw_t is None:
                    rejects["exec_failed"] = rejects.get("exec_failed", 0) + 1
                    continue
                twin_key = self.normalize(raw_t)
                tk = canonical(twin_key)
                reason = (
                    "empty_key" if not tk else "same_key" if tk == key_norm
                    else "in_distractors" if tk in distractors
                    else "in_k_err" if tk in base_k_err
                    else None
                )
                if reason != "same_key":
                    attempts += 1  # правка без смены ключа стоит только времени решателя: потолок попыток её не считает
                if reason:
                    rejects[reason] = rejects.get(reason, 0) + 1
                    continue
                twin_rendered = dataclasses.replace(r, code=new_code)
                clip = compose_text(twin_rendered, [])
                off = shown.blocks["code"][0] + a
                gates = _edit_gates(shown.text, clip.text, off, old, new, cfg)
                if not all(gates.values()):
                    rejects["edit_gate"] = rejects.get("edit_gate", 0) + 1
                    continue
                line, col = _line_col(shown.text, off)
                b_line, b_col = _line_col(shown.text, off, shown.blocks["code"][0])
                changed = ChangedLiteral(line, col, old, new, "value", off, "code", b_line, b_col, a)
                dense = rejects.get("in_distractors", 0) + rejects.get("in_k_err", 0)
                est = honest_collision_estimate("data_literal", 1, dense, attempts, cfg)
                known = {key_norm, *base_k_err, *distractors}
                err_trace = tuple(sorted(k for k in kerr(new_code) if k not in known and k != tk))
                twin = Twin(
                    family_id=self.id,
                    seed=inst.seed,
                    variant_code=inst.variant_code,
                    mutation_class="data_literal",
                    role="value",
                    tier=1,
                    shown_text=shown.text,
                    clipboard_text=clip.text,
                    changed_literal=changed,
                    key=key_norm,
                    key_hash=hash_answer(self.id, inst.key),
                    twin_key=tk,
                    twin_key_hash=hash_answer(self.id, twin_key),
                    honest_collision_estimate=est,
                    twin_rendered=twin_rendered,
                    gates={"g1_keys_differ": True, "g2_not_in_distractors": True, "g3_not_in_k_err": True, **gates},
                    attempts=attempts,
                    rejects=dict(rejects),
                    executor="scenario_solver",
                    version=p.version,
                    err_trace_keys=err_trace,
                    k_err_size=len(base_k_err),
                )
                return TwinResult(twin, "ok", attempts, rejects, "scenario_solver")
        reason = "no_candidates" if not spans else "all_candidates_rejected"
        return TwinResult(None, reason, attempts, rejects, "scenario_solver")

    @staticmethod
    def _literal_ok(text: str, min_value: int) -> bool:
        return text.isdigit() and not text.startswith("0") and int(text) >= min_value

    @staticmethod
    def _propose(old: str, rng: random.Random, p: Any) -> list[str]:
        """Новые значения целого литерала: относительное изменение в [rel_min, rel_max], длина ±1, без путаемых глифов."""
        from app.testing.anticheat import literals as lit  # noqa: PLC0415

        v = int(old)
        lo = max(p.min_value, int(v * (1 - p.rel_change_max)))
        hi = min(p.max_value, max(lo + 1, int(v * (1 + p.rel_change_max)) + 1))
        out: list[str] = []
        for _ in range(80):
            n = rng.randint(lo, hi)
            s = str(n)
            if (
                n != v
                and p.rel_change_min <= abs(n - v) / v <= p.rel_change_max
                and abs(len(s) - len(old)) <= p.max_length_delta
                and lit.glyph_ok(old, s, p.confusable_pairs)
                and s not in out
            ):
                out.append(s)
            if len(out) >= p.max_values_per_literal:
                break
        return out


class ScenarioFamily(ModelTwinMixin, BankFamily):
    """Семейство с ответом-текстом, решателем по показанному тексту и двойником в буфере."""

    language: ClassVar[str] = "text"
    answer_hint: ClassVar[str] = ""
    min_distractors: ClassVar[int] = 2  # меньше — экземпляр слишком «угадываем»: перегенерируем

    # --- что реализует семейство
    @abstractmethod
    def make(self, rng: random.Random) -> Scenario: ...

    @abstractmethod
    def prompt(self, sc: Scenario) -> str: ...

    @abstractmethod
    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None: ...

    @abstractmethod
    def why(self, sc: Scenario, raw_key: str) -> str: ...

    def hint(self, sc: Scenario) -> str:
        return self.answer_hint

    # --- генерация
    def normalize(self, answer: Any) -> Any:
        return NORMALIZERS_EXT[self.answer_kind](answer)

    def build(self, rng: random.Random, seed: int) -> Instance:
        return self._finish(seed, self.make(rng))

    def _finish(self, seed: int, sc: Scenario) -> Instance:
        try:
            raw = self.solve(sc, sc.code)
            wrongs = [(self.solve(sc, sc.code, m), m) for m in self.misconceptions]
        except ScenarioError as e:
            raise GenerationRetry from e
        if raw is None:
            raise GenerationRetry
        inst = self.text_instance(
            seed,
            prompt=self.prompt(sc),
            code=sc.code,
            language=self.language,
            raw_key=raw,
            wrongs=wrongs,
            why=self.why(sc, raw),
            answer_hint=self.hint(sc),
        )
        if len(inst.distractors) < self.min_distractors:
            raise GenerationRetry
        return inst

    def scenario_for(self, seed: int) -> Scenario:
        """Сценарий экземпляра: тот же цикл перегенерации, что и в generate() (детерминизм по seed)."""
        rng = rng_for(self.id, seed)
        for _ in range(self.max_attempts):
            try:
                sc = self.make(rng)
                self._finish(seed, sc)
                return sc
            except GenerationRetry:
                continue
        raise RuntimeError(f"{self.id}: не удалось собрать сценарий для seed={seed}")


__all__ = [
    "ModelTwinMixin",
    "NORMALIZERS_EXT",
    "Scenario",
    "ScenarioError",
    "ScenarioFamily",
    "norm_branches",
    "norm_datetimes",
    "norm_fraction",
    "norm_ints",
    "norm_latin",
    "norm_output",
    "norm_pytest_summary",
]
