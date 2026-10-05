"""Проверки содержимого приглашения: вилка, поля, срок ответа (концепция §4.3, DECISIONS 6).

Результат — Check с ошибками (блок) и предупреждениями (не блок); тексты на русском, готовы для UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from .settings import ContactConfig, get_contact_config
from .texts import decimal_ru, rub, salary_range


@dataclass(frozen=True)
class Issue:
    code: str
    message: str


@dataclass
class Check:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)
    notes: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str) -> None:
        self.errors.append(Issue(code, message))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(Issue(code, message))

    def note(self, code: str, message: str) -> None:
        self.notes.append(Issue(code, message))

    def merge(self, other: Check) -> Check:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.notes.extend(other.notes)
        return self

    @property
    def error_codes(self) -> list[str]:
        return [i.code for i in self.errors]

    @property
    def warning_codes(self) -> list[str]:
        return [i.code for i in self.warnings]

    def messages(self) -> list[str]:
        return [i.message for i in self.errors]


def ratio_exceeds(salary_to: int, salary_from: int, ratio: float) -> bool:
    """to > ratio·from без ошибок плавающей точки на границе (1.6·from допустимо)."""
    return Fraction(salary_to) > Fraction(str(ratio)) * Fraction(salary_from)


def validate_salary_range(
    salary_from: int,
    salary_to: int,
    salary_type: str | None = "net",
    cfg: ContactConfig | None = None,
) -> Check:
    """Вилка: from ≥ 30 000, to ≥ from, шаг 1000, ширина ≤ 1.6× (1.6–2.0× — предупреждение, > 2.0× — блок)."""
    cfg = cfg or get_contact_config()
    s = cfg.salary
    check = Check()
    if salary_type not in s.types:
        options = " или ".join(s.types.values())
        check.error("salary_type", f"Укажите, вилка {options}")
    if salary_from < s.min_from:
        check.error("salary_from_min", f"Нижняя граница вилки — не меньше {rub(s.min_from)}")
    if salary_to < salary_from:
        check.error("salary_order", "Верхняя граница вилки не может быть меньше нижней")
    if salary_from % s.step or salary_to % s.step:
        check.error("salary_step", f"Вилка указывается с шагом {rub(s.step)}")
    if not check.ok or salary_from <= 0:
        return check
    ratio = salary_to / salary_from
    if ratio_exceeds(salary_to, salary_from, s.hard_max_ratio):
        check.error(
            "salary_too_wide",
            f"Вилка {salary_range(salary_from, salary_to)} шире {decimal_ru(s.hard_max_ratio, 1)}× — "
            "сузьте её: кандидат должен видеть реальные условия",
        )
    elif ratio_exceeds(salary_to, salary_from, s.soft_max_ratio):
        check.warn(
            "salary_wide",
            f"Широкая вилка: верхняя граница больше нижней в {decimal_ru(ratio, 2)} раза. "
            f"На узкие вилки (до {decimal_ru(s.soft_max_ratio, 1)}×) кандидаты отвечают охотнее",
        )
    return check


def validate_invitation_content(
    *,
    title: str,
    description: str,
    contact_method: str,
    contact_value: str,
    response_days: int,
    work_format: str | None = None,
    cfg: ContactConfig | None = None,
) -> Check:
    """Содержимое приглашения: заголовок, описание ≥ 50 символов, способ связи, срок 3/5/7 дней."""
    cfg = cfg or get_contact_config()
    inv = cfg.invitation
    check = Check()
    title = (title or "").strip()
    description = (description or "").strip()
    if not title:
        check.error("title_required", "Укажите заголовок приглашения")
    elif len(title) > inv.title_max_len:
        check.error("title_too_long", f"Заголовок — не длиннее {inv.title_max_len} символов")
    if len(description) < inv.description_min_len:
        check.error(
            "description_too_short",
            f"Описание — не короче {inv.description_min_len} символов: сейчас {len(description)}",
        )
    elif len(description) > inv.description_max_len:
        check.error("description_too_long", f"Описание — не длиннее {inv.description_max_len} символов")
    if contact_method not in inv.contact_methods:
        options = ", ".join(inv.contact_methods.values())
        check.error("contact_method", f"Выберите способ связи: {options}")
    if not (contact_value or "").strip():
        check.error("contact_value", "Укажите контакт, по которому кандидат сможет с вами связаться")
    windows = cfg.response.windows_days
    if response_days not in windows:
        options = ", ".join(str(d) for d in windows[:-1]) + f" или {windows[-1]}"
        check.error("response_days", f"Срок ответа — {options} дней")
    if work_format is not None and work_format not in inv.formats:
        check.error("format", "Выберите формат работы: " + ", ".join(inv.formats.values()).lower())
    return check
