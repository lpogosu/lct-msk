"""Реестр персон пилота: 12 честных (4 специализации × 3 грейда) и 2 «списывающих».

Профиль знаний — единственный источник правды: раздел персон в PROTOCOL.md собирается из него
(`python -m eval.agent_pilot.personas --md`), агент читает свою карточку командой `pilot.py persona --persona P01`.
Опрос заполняется по тому же профилю (ответы опроса на категорию не влияют: «заявлено» — серые чипы, CLAUDE.md).
Реальных ФИО и брендов нет: персоны — только коды и роли.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field

GRADE_RANK = {"none": 0, "intern": 1, "junior": 2, "middle": 3, "senior": 4}
SPEC_NAMES = {
    "backend_python": "Backend (Python)",
    "frontend_js": "Frontend (JS/TS)",
    "qa": "QA / тестирование",
    "devops": "DevOps / SRE",
}


@dataclass(frozen=True)
class Persona:
    id: str
    kind: str  # honest | cheater
    spec: str
    grade: str  # заявленный грейд (выбор на экране К3)
    intended: str  # «истинный» уровень, который персона должна изображать
    exp_months: int
    strong: tuple[str, ...]
    weak: tuple[str, ...]
    misconceptions: tuple[str, ...]
    actions: dict[str, str] = field(default_factory=dict)
    techs: dict[str, str] = field(default_factory=dict)
    note: str = ""

    @property
    def title(self) -> str:
        who = "Списывающий" if self.kind == "cheater" else "Кандидат"
        return f"{who} {self.id}: {SPEC_NAMES[self.spec]}, уровень {self.intended}"


# Действия опроса по специализации и уровню (коды — config/testing.yaml → survey.actions)
_ACTIONS = {
    "backend_python": {
        "junior": {"server_api": "sometimes", "sql_db": "sometimes", "algorithms": "sometimes"},
        "middle": {"server_api": "regularly", "sql_db": "regularly", "queues_cache": "sometimes", "ci_cd": "sometimes"},
        "senior": {
            "server_api": "regularly", "sql_db": "regularly", "queues_cache": "regularly",
            "ci_cd": "regularly", "monitoring": "sometimes",
        },
    },
    "frontend_js": {
        "junior": {"ui_layout": "regularly", "spa_components": "sometimes", "browser_debug": "sometimes"},
        "middle": {"ui_layout": "regularly", "spa_components": "regularly", "browser_debug": "regularly", "ci_cd": "sometimes"},
        "senior": {
            "ui_layout": "regularly", "spa_components": "regularly", "browser_debug": "regularly",
            "ci_cd": "regularly", "autotests": "sometimes",
        },
    },
    "qa": {
        "junior": {"manual_testing": "regularly", "autotests": "sometimes", "sql_db": "sometimes"},
        "middle": {"manual_testing": "regularly", "autotests": "regularly", "sql_db": "sometimes", "ci_cd": "sometimes"},
        "senior": {
            "manual_testing": "sometimes", "autotests": "regularly", "sql_db": "regularly",
            "ci_cd": "regularly", "server_api": "sometimes",
        },
    },
    "devops": {
        "junior": {"ci_cd": "sometimes", "containers_infra": "sometimes"},
        "middle": {"ci_cd": "regularly", "containers_infra": "regularly", "monitoring": "sometimes"},
        "senior": {
            "ci_cd": "regularly", "containers_infra": "regularly", "monitoring": "regularly", "queues_cache": "sometimes",
        },
    },
}
# Технологии опроса (коды — survey.technologies)
_TECHS = {
    "backend_python": ("python", "django_fastapi", "postgresql", "linux_git"),
    "frontend_js": ("javascript", "typescript", "react", "html_css"),
    "qa": ("python", "test_frameworks", "postgresql", "linux_git"),
    "devops": ("linux_git", "docker", "kubernetes", "python"),
}


def _mk(pid, kind, spec, grade, intended, months, strong, weak, mis, note="") -> Persona:  # type: ignore[no-untyped-def]
    techs = {t: "now" for t in _TECHS[spec]}
    if intended == "junior":
        techs.pop(_TECHS[spec][-1], None)  # у младшего стек уже
    return Persona(
        pid, kind, spec, grade, intended, months, tuple(strong), tuple(weak), tuple(mis),
        actions=dict(_ACTIONS[spec][intended]), techs=techs, note=note,
    )


_SENIOR_NOTE = "Отвечай как сильный специалист, но допускай 1–2 промаха на редких деталях; ошибки специально не придумывай."

PERSONAS: dict[str, Persona] = {
    p.id: p
    for p in [
        _mk("P01", "honest", "backend_python", "junior", "junior", 10,
            ["базовый синтаксис Python: типы, списки и словари, циклы, срезы", "простые SELECT/WHERE/ORDER BY, один JOIN",
             "основные коды ответа HTTP (200, 201, 404, 500)", "git add/commit/push/branch"],
            ["изменяемые аргументы по умолчанию, замыкания, генераторы, декораторы",
             "GROUP BY/HAVING, оконные функции, NULL в условиях, индексы",
             "идемпотентность, кеширование, пагинация, тонкости 401/403/409/422", "сложность алгоритмов, rebase/reset"],
            ["путает == и is; считает, что list.sort() возвращает список", "думает, что WHERE x = NULL находит NULL",
             "считает 401 и 403 взаимозаменяемыми"]),
        _mk("P02", "honest", "backend_python", "middle", "middle", 36,
            ["Python на рабочем уровне: исключения, генераторы, контекстные менеджеры, изменяемость, dict/set",
             "JOIN, GROUP BY/HAVING, подзапросы, NULL-логика, базовые индексы",
             "REST, коды ответа, идемпотентность PUT/DELETE, пагинация, JWT на уровне «как пользоваться»",
             "базовые алгоритмы и оценка сложности"],
            ["оконные функции и планы запросов", "тонкости asyncio и GIL, метаклассы",
             "уровни изоляции транзакций, rate limit и кеш-заголовки в деталях",
             "git rebase -i, reflog, cherry-pick в нестандартных случаях"],
            ["путает READ COMMITTED и REPEATABLE READ", "считает, что GIL делает любой код потокобезопасным"]),
        _mk("P03", "honest", "backend_python", "senior", "senior", 84,
            ["глубокий Python: дескрипторы, MRO, GIL и asyncio, генераторы, мемоизация, __slots__",
             "SQL: оконные функции, CTE, планы, индексы, уровни изоляции и аномалии",
             "HTTP/API: кеш-заголовки, идемпотентность, ETag, rate limit, версии, 409/422/429",
             "алгоритмы и структуры данных, амортизированная сложность; git и Linux уверенно"],
            ["редкие нюансы: экзотика regex, специфика чужих СУБД"],
            ["практически нет; ошибается только на очень узких деталях, как живой сеньор"], _SENIOR_NOTE),
        _mk("P04", "honest", "frontend_js", "junior", "junior", 9,
            ["HTML-теги и семантика, базовый CSS (блочная модель, flex на уровне «выровнять»)",
             "переменные, функции, массивы, map/filter в JS", "основы React: компонент, props, useState"],
            ["замыкания, this, прототипы, event loop, промисы и порядок микрозадач",
             "useEffect и зависимости, ключи списков, мемоизация",
             "специфичность селекторов, grid, позиционирование, контексты z-index", "CORS, кеш, коды ответа"],
            ["считает == и === почти одинаковыми; думает, что var и let ведут себя одинаково",
             "считает setState синхронным"]),
        _mk("P05", "honest", "frontend_js", "middle", "middle", 40,
            ["JS: замыкания, this, промисы и async/await, деструктуризация, reduce",
             "React: хуки, зависимости useEffect, ключи, контролируемые поля", "CSS: flex/grid, специфичность, адаптивность",
             "HTTP: методы, коды, CORS на уровне «знаю, зачем»"],
            ["порядок микро- и макрозадач в хитрых примерах", "тонкости useMemo/useCallback, concurrent-режим",
             "приоритеты каскадных слоёв, перформанс отрисовки", "алгоритмы сложнее базовых"],
            ["путает порядок вывода setTimeout(0) и Promise.then во вложенных примерах",
             "думает, что useMemo гарантирует отсутствие пересчёта"]),
        _mk("P06", "honest", "frontend_js", "senior", "senior", 90,
            ["event loop, микрозадачи, прототипы, this, замыкания в сложных примерах",
             "React: рендер и reconciliation, устаревшие замыкания в хуках, мемоизация",
             "CSS: каскад, специфичность, контексты наложения, grid/flex", "HTTP/CORS/кеш, алгоритмы и сложность"],
            ["редкие подробности браузерных API"], ["практически нет; промахи только на узких деталях"], _SENIOR_NOTE),
        _mk("P07", "honest", "qa", "junior", "junior", 8,
            ["тест-кейс, чек-лист, баг-репорт (шаги, ожидаемое и фактическое)",
             "классы эквивалентности и границы на простых примерах", "простой SELECT и коды 200/404/500", "базовые git-команды"],
            ["pytest-фикстуры, параметризация, моки и подмены",
             "причинно-следственные таблицы, попарное тестирование, покрытие ветвей и условий",
             "идемпотентность, 401/403/409/422, пагинация, JWT", "JOIN/GROUP BY, NULL"],
            ["считает, что 100% покрытия строк означает отсутствие багов", "путает мок и стаб",
             "думает, что 401 и 403 — одно и то же"]),
        _mk("P08", "honest", "qa", "middle", "middle", 38,
            ["тест-дизайн: границы, классы, таблицы решений, попарное", "pytest: фикстуры, параметризация, monkeypatch, моки",
             "API-проверки: статусы, идемпотентность, пагинация, авторизация",
             "SQL: JOIN, GROUP BY, NULL; git и Linux на рабочем уровне"],
            ["покрытие условий и MC/DC, мутационное тестирование", "оконные функции, планы запросов",
             "тонкости нестабильных тестов и изоляции окружений"],
            ["путает покрытие операторов и ветвей на примерах с or", "считает мок и шпиона (spy) одним и тем же"]),
        _mk("P09", "honest", "qa", "senior", "senior", 96,
            ["тест-дизайн глубоко: MC/DC, границы для составных условий, попарное", "pytest, моки и шпионы, изоляция, нестабильные тесты",
             "API: идемпотентность, кеш, rate limit, JWT, версии", "SQL, git, Linux уверенно"],
            ["редкие нюансы инструментов"], ["практически нет"], _SENIOR_NOTE),
        _mk("P10", "honest", "devops", "junior", "junior", 8,
            ["docker run/ps/logs, образ против контейнера",
             "основные команды Linux: ls, cd, grep, chmod в простом виде", "основные коды ответа HTTP; git add/commit/push"],
            ["слои образа и кеш сборки, multi-stage, ENTRYPOINT против CMD", "YAML-тонкости, nginx location, cron-выражения",
             "Kubernetes: probes, requests/limits, Service", "права chmod 755, umask, pipe и коды возврата в bash"],
            ["думает, что данные контейнера сохраняются после удаления без тома", "путает ENTRYPOINT и CMD",
             "считает, что chmod 777 — безопасное решение"]),
        _mk("P11", "honest", "devops", "middle", "middle", 42,
            ["Docker: слои, кеш, тома, сети, compose (depends_on, healthcheck)", "CI: пайплайны, кеш артефактов, секреты",
             "Linux/bash: права, pipe, коды возврата, cron", "nginx: location, proxy_pass; HTTP-коды, кеш"],
            ["Kubernetes: тонкости probes, limits, HPA, сетевые политики", "оптимизация образов, rootless, namespaces",
             "редкие подвохи bash (set -euo pipefail)"],
            ["путает liveness и readiness в сценарии перезапуска", "считает, что depends_on ждёт готовности сервиса"]),
        _mk("P12", "honest", "devops", "senior", "senior", 96,
            ["Docker и k8s глубоко: probes, requests/limits и QoS, rolling update, PDB",
             "CI/CD, кеш, секреты, стратегии выкатки", "Linux/bash: права, сигналы, коды возврата, cron-подвохи",
             "nginx, HTTP-кеш, rate limit; git"],
            ["редкие версионно-специфичные подробности"], ["практически нет"], _SENIOR_NOTE),
        _mk("C01", "cheater", "backend_python", "middle", "junior", 14,
            ["реально знает на уровне Junior (синтаксис, простой SQL)"],
            ["всё, что выше: берёт Middle, потому что «ассистент всё решит»"],
            ["сам правильно ответить на задания Middle не может"],
            "Списывающий: на КАЖДОМ задании копирует текст (команда copy) и «спрашивает ассистента» — решает ровно тот "
            "текст, что лежит в буфере (двойник), и отвечает по нему."),
        _mk("C02", "cheater", "frontend_js", "middle", "junior", 12,
            ["реально знает на уровне Junior (вёрстка, простой JS)"],
            ["event loop, хуки, каскад на уровне Middle"],
            ["сам правильно ответить на задания Middle не может"],
            "Списывающий (смешанный): копирует и решает копию на 2 из 3 заданий; на каждом третьем (seq кратен 3) отвечает "
            "сам, по экрану."),
    ]
}


def md() -> str:
    out: list[str] = []
    for p in PERSONAS.values():
        out.append(f"#### {p.title}")
        out.append(
            f"- Заявляет грейд в тесте: **{p.grade}**; стаж по опросу: {p.exp_months} мес.; "
            f"«истинный» уровень персоны: **{p.intended}**."
        )
        out.append("- Знает хорошо: " + "; ".join(p.strong) + ".")
        out.append("- Не знает или знает плохо: " + "; ".join(p.weak) + ".")
        out.append("- Типичные заблуждения: " + "; ".join(p.misconceptions) + ".")
        if p.note:
            out.append(f"- Особенность: {p.note}")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    if "--md" in sys.argv:
        print(md())
    else:
        print(json.dumps({k: asdict(v) for k, v in PERSONAS.items()}, ensure_ascii=False, indent=1))
