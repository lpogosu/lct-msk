"""Одноразовое расширение алиасов config/skills.yaml (идемпотентно: существующие алиасы пропускаются).

Источники: сленговые написания рынка (ручной список) и частоты нераспознанных терминов из data/market/unmatched_terms.json
(термины с ≥ 8 упоминаний в открытых вакансиях). Печатает, что добавлено и что пропущено.
"""
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.matching.skills import normalize_alias  # noqa: E402

P = Path(__file__).resolve().parents[3] / "config" / "skills.yaml"
ADD: dict[str, list[str]] = {
    "asyncio": ["асинк", "асинке", "асинхронщина", "асинхронка", "асинхронный код", "async io"],
    "drf": ["дрф", "django rest"],
    "pytest": ["пайтест", "питест", "пайтесте"],
    "postgresql": ["pg", "postgre sql", "постгри"],
    "kubernetes": ["k8", "kubectl", "кубернетесе", "куб ернетес"],
    "docker": ["докеры", "докеризация", "docker swarm", "докер композ"],
    "ci_cd": ["ci-cd", "cicd", "си ай си ди", "сиай сиди", "автоматизация сборки и деплоя", "автоматизация деплоя"],
    "gitlab_ci": ["gitlab runner", "gitlab-runner", "гитлаб ci", "гитлаб си ай"],
    "rabbitmq": ["раббит", "раббитмк", "rabbit mq", "rabbit-mq", "раббитмq"],
    "helm": ["хельм", "helm charts", "helm-чарты"],
    "victoriametrics": ["victoria metrics", "victoria-metrics", "виктория метрикс"],
    "load_testing": ["нагрузочное", "нагрузочного", "нагрузочные тесты", "нагрузочных тестов", "тестировщик нагрузки",
                     "нагрузочным тестированием", "стресс-тестирование"],
    "api_testing": ["rest api тесты", "api тесты", "тестирование rest", "тестирование апи", "rest-тесты", "тесты api",
                    "тестирования rest"],
    "manual_testing": ["ручные тесты", "ручных тестов", "ручные", "мануальное тестирование", "мануальный тестировщик",
                       "manual testing", "manual qa", "тестовая документация", "тестовой документации",
                       "чек-лист", "тест-кейс", "тестовые сценарии", "баг-репорт", "баг-трекинг"],
    "test_automation": ["автотестер", "автотестирование", "автотестирования", "автоматизатор тестирования",
                        "автоматизированное тестирование", "автоматизированного тестирования", "автоматизация тестов",
                        "автотест", "qa automation", "automation qa", "aqa"],
    "networking_ops": ["dhcp", "cisco", "mikrotik", "микротик", "eltex", "bgp", "ospf", "lan", "wi-fi", "wifi",
                       "коммутаторы", "маршрутизаторы", "сетевое оборудование", "сетевого оборудования"],
    "virtualization": ["vsphere", "esxi", "vmware vsphere", "виртуальные машины", "виртуальных машин"],
    "siem": ["splunk", "qradar", "arcsight"],
    "git": ["svn", "subversion", "mercurial"],
    "design_patterns": ["mvc", "mvvm", "mvp", "mvi"],
    "llm": ["gigachat", "gigachat api", "chatgpt", "deepseek", "yandexgpt", "gpt", "rag", "генеративные модели"],
    "jira": ["redmine", "трекер задач", "trello"],
    "javascript": ["жс", "джс"],
    "angular": ["ангулар", "ангуляре"],
    "cypress": ["сайпресс", "цайпресс"],
    "playwright": ["плейрайт", "плейврайт"],
    "github_actions": ["github action", "гитхаб экшнс", "gh actions", "github workflows"],
    "yandex_cloud": ["yandex.cloud", "яндекс.клауд", "yc"],
    "elasticsearch": ["elastic search", "эластиксёрч", "эластиксерч", "elk-стек"],
    "scikit_learn": ["scikit", "сайкит лёрн", "сайкит лерн"],
    "gradient_boosting": ["катбуст", "лайтгбм", "бустинг", "бустинги"],
    "pytorch": ["торч", "pytorch lightning"],
    "swiftui": ["свифтюай", "свифт ui"],
    "tech_docs": ["uml", "bpmn", "c4 model", "диаграммы uml"],
    "highload": ["high load", "high-load"],
    "ddd": ["предметно-ориентированное проектирование"],
    "nginx": ["nginx plus", "энжинкс"],
    "terraform": ["терраформом", "terragrunt", "opentofu"],
    "ansible": ["ansible playbook", "плейбуки", "плейбук"],
    "lang_other_delphi": [],
}
NEW = {
    "delphi": "{ name: Delphi, cat: lang_other, untestable: true, aliases: [delphi, делфи, object pascal] }",
    "appium": "{ name: Appium, cat: qa, untestable: true, aliases: [appium, аппиум, uiautomator, espresso] }",
    "itil": "{ name: ITIL и ITSM, cat: process, untestable: true, aliases: [itil, itsm, service desk, сервис деск, helpdesk, хелпдеск] }",
    "scada": "{ name: SCADA и АСУ ТП, cat: embedded, untestable: true, aliases: [scada, скада, асу тп, modbus, plc, программируемые контроллеры] }",
}


def q(a: str) -> str:
    return f'"{a}"' if re.search(r"[,:#\[\]{}&*!|>'\"%@]|^[-?\d]", a) else a


def main() -> None:
    text = P.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    taken = {normalize_alias(a) for s in data["skills"].values() for a in s.get("aliases", [])}
    for v in data["roles"].values():
        taken |= {normalize_alias(a) for a in v.get("aliases", [])}
    added = skipped = 0
    for sid, aliases in ADD.items():
        if sid not in data["skills"]:
            continue
        fresh = []
        for a in aliases:
            n = normalize_alias(a)
            if n in taken:
                skipped += 1
                continue
            taken.add(n)
            fresh.append(a)
        if not fresh:
            continue
        m = re.search(rf"^  {re.escape(sid)}: \{{.*?aliases: \[(.*)\] \}}$", text, re.M)
        assert m, sid
        new_line = text[m.start():m.end()]
        new_line = new_line[: -len("] }")] + ", " + ", ".join(q(a) for a in fresh) + "] }"
        text = text[: m.start()] + new_line + text[m.end():]
        added += len(fresh)
    # новые навыки — перед блоком ролей
    block = ["  # ---------------------------------------------------------------- Из открытых вакансий (data/market/)"]
    for sid, line in NEW.items():
        if sid not in data["skills"]:
            block.append(f"  {sid}: {line}")
    if len(block) > 1:
        text = text.replace("\nroles:\n", "\n" + "\n".join(block) + "\n\nroles:\n", 1)
    P.write_text(text, encoding="utf-8")
    print("added", added, "skipped(existing)", skipped, "new skills", len(block) - 1)


if __name__ == "__main__":
    main()
