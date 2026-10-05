"""Контракт банка заданий.

Семейство (Family) = шаблон + генератор параметров + эталонный решатель + генератор дистракторов.
Из семейства порождаются одноразовые экземпляры-клоны (Instance): у каждого свой код/числа и СВОЙ ключ,
сложность b общая для семейства. Ключ получается исполнением ОФЛАЙН (при генерации пула экземпляров),
в рантайме теста код не исполняется. Подробно: _internal/design/spec-testing.md §3.8.

Этот файл — общий контракт между IRT/CAT-движком и авторами семейств. Меняй осторожно.
"""

from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

Format = Literal["predict_output", "bug_line", "sql_result", "parsons", "complexity", "mcq_concept"]
AnswerMode = Literal["text", "choice", "line_choice", "order"]
Grade = Literal["intern", "junior", "middle", "senior"]

GRADES: tuple[str, ...] = ("intern", "junior", "middle", "senior")


@dataclass(frozen=True)
class Option:
    id: str  # стабильный id варианта внутри экземпляра ("a", "b", ...); порядок уже перемешан по seed
    text: str


@dataclass(frozen=True)
class Rendered:
    """То, что видит кандидат. НИКОГДА не содержит ключ и теги дистракторов."""

    prompt: str  # формулировка на русском
    code: str | None = None
    language: str | None = None  # python | javascript | typescript | sql | html | css | text
    options: tuple[Option, ...] = ()  # choice; для line_choice — варианты исправления
    lines: tuple[str, ...] = ()  # bug_line: строки кода; parsons: перемешанные фрагменты
    table: dict[str, Any] | None = None  # sql_result: {"name": str, "columns": [...], "rows": [[...]]}
    answer_mode: AnswerMode = "choice"
    answer_hint: str | None = None  # для text: что именно ввести, например «вывод программы одной строкой»

    def to_public(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "code": self.code,
            "language": self.language,
            "options": [{"id": o.id, "text": o.text} for o in self.options],
            "lines": list(self.lines),
            "table": self.table,
            "answer_mode": self.answer_mode,
            "answer_hint": self.answer_hint,
        }


def canonical(value: Any) -> str:
    """Каноническая строка ответа для сравнения и хеширования."""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Instance:
    family_id: str
    seed: int
    rendered: Rendered
    key: Any  # нормализованный правильный ответ: str | {"line": int, "fix": str} | list[str]
    # канонический (нормализованный) неверный ответ → тег заблуждения (для разбора ошибок)
    distractors: dict[str, str] = field(default_factory=dict)
    explanation: str = ""  # разбор для кандидата после теста (рус.)

    @property
    def key_hash(self) -> str:
        """Хеш ключа — для детектора «чужого ключа» (ответ совпал с ключом экземпляра другого человека)."""
        return hashlib.sha256(f"{self.family_id}|{canonical(self.key)}".encode()).hexdigest()

    @property
    def variant_code(self) -> str:
        """Код варианта, который виден кандидату в углу экрана (#7F3A)."""
        return hashlib.sha256(f"{self.family_id}:{self.seed}".encode()).hexdigest()[:4].upper()


_WS = re.compile(r"\s+")


class Family(ABC):
    """Семейство заданий. Подклассы задают атрибуты класса и реализуют generate()."""

    id: ClassVar[str]  # "py.slicing.step"
    title: ClassVar[str]  # короткое название для лаборатории, рус.
    specs: ClassVar[tuple[str, ...]]  # ("backend_python",) или ("backend_python", "frontend_js") для общих модулей
    module: ClassVar[str]  # код модуля из config/taxonomy.yaml
    format: ClassVar[Format]
    target_grade: ClassVar[Grade]  # экспертная оценка уровня (модифицированный Ангофф)
    position: ClassVar[float] = 0.0  # положение внутри грейда: -1 у нижнего порога … +1 у верхнего
    c: ClassVar[float] = 0.0  # вероятность угадывания (choice с k вариантами → 1/k)
    time_limit_s: ClassVar[int] = 90
    enemies: ClassVar[tuple[str, ...]] = ()  # взаимоисключающие семейства (подсказывают ответ друг другу)
    is_anchor: ClassVar[bool] = False  # якорь шкалы: b не перекалибровывается

    @abstractmethod
    def generate(self, seed: int) -> Instance:
        """Детерминированно по seed породить экземпляр (используй random.Random(seed))."""

    def normalize(self, answer: Any) -> Any:
        """Нормализация ответа кандидата перед сравнением с ключом. По умолчанию — схлопнуть пробелы."""
        if isinstance(answer, str):
            return _WS.sub(" ", answer.strip())
        return answer

    def check(self, inst: Instance, answer: Any) -> tuple[bool, str | None]:
        """(верно?, тег заблуждения если ответ совпал с известным дистрактором)."""
        a = self.normalize(answer)
        if canonical(a) == canonical(inst.key):
            return True, None
        return False, inst.distractors.get(canonical(a))


REGISTRY: dict[str, Family] = {}


def register(cls: type[Family]) -> type[Family]:
    """Декоратор: регистрирует семейство в общем реестре."""
    fam = cls()
    if fam.id in REGISTRY:
        raise ValueError(f"Дублирующийся id семейства: {fam.id}")
    REGISTRY[fam.id] = fam
    return cls
