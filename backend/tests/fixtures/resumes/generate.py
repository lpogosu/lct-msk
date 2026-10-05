"""Генератор синтетических резюме для тестов разбора (концепция §3.1.1). Все люди и компании вымышлены:
фамилии — из ИТ-терминов, компании — несуществующие (CLAUDE.md: никаких реальных ФИО и брендов).

Запуск (из backend/):  uv run python -m tests.fixtures.resumes.generate
PDF детерминированы (invariant=1): повторный запуск даёт те же байты, их можно хранить в git.

1. backend_middle.pdf   — «Фамилия Имя Отчество» в шапке, разделы как на hh.ru, два места работы с пересечением
                          (совместительство), «Март 2021 — настоящее время», навыки списком, софт-скиллы.
2. frontend_junior.pdf  — «Имя Фамилия», даты «03.2023 – по н.в.», контакты в отдельном разделе, Telegram-ссылка.
3. fullstack_senior.pdf — две страницы, строка «ФИО: …», английские заголовки вперемешку с русскими,
                          годы без месяцев («2016–2019», «с 2019 по 2021»), дата рождения (маскируется для LLM).
4. scan_no_text.pdf     — только картинка без текстового слоя: разбор обязан вернуть понятную ошибку.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.services.pdf.fonts import register_fonts

OUT = Path(__file__).resolve().parent
W, H = A4

BACKEND_MIDDLE: list[tuple[str, str]] = [
    ("h1", "Кортежев Денис Павлович"),
    ("p", "Python-разработчик (Middle)"),
    ("p", "Москва, готов к гибридному формату"),
    ("p", "Телефон: +7 (915) 204-18-73"),
    ("p", "Email: d.kortezhev@example.com"),
    ("p", "Telegram: @kortezhev_dev"),
    ("gap", ""),
    ("h2", "Опыт работы — 4 года 7 месяцев"),
    ("p", "Март 2021 — настоящее время"),
    ("b", "ООО «Северный код»"),
    ("p", "Backend-разработчик"),
    ("p", "Сервис платёжного шлюза на FastAPI и PostgreSQL, очереди на Kafka, Docker и CI в GitLab."),
    ("p", "Сократил время ответа API в 3 раза: индексы, кэш в Redis, асинхронные воркеры Celery."),
    ("gap", ""),
    ("p", "Сентябрь 2020 — Май 2021"),
    ("b", "ООО «Тихая гавань софт»"),
    ("p", "Python-разработчик (совместительство)"),
    ("p", "Парсеры и REST API на Django, отчёты на SQL, unit-тесты на pytest."),
    ("gap", ""),
    ("h2", "Образование"),
    ("p", "2016–2020"),
    ("p", "Северо-Уральский университет информатики, прикладная информатика, бакалавр"),
    ("gap", ""),
    ("h2", "Ключевые навыки"),
    ("p", "Python, FastAPI, Django, PostgreSQL, Redis, Kafka, Docker, Git, Linux, SQL, REST API, pytest"),
    ("gap", ""),
    ("h2", "Личные качества"),
    ("p", "Ответственность, работа в команде, аналитическое мышление, наставничество младших коллег."),
]

FRONTEND_JUNIOR: list[tuple[str, str]] = [
    ("h1", "Алиса Компонентова"),
    ("p", "Junior Frontend-разработчик"),
    ("gap", ""),
    ("h2", "Контакты"),
    ("p", "alisa.komponentova@example.org"),
    ("p", "8 921 555-01-47"),
    ("p", "t.me/alisa_komponent"),
    ("p", "Санкт-Петербург, удалённо или гибрид"),
    ("gap", ""),
    ("h2", "О себе"),
    ("p", "Начинающий фронтенд-разработчик. Быстро учусь, внимательна к деталям, люблю аккуратную вёрстку."),
    ("gap", ""),
    ("h2", "Опыт работы"),
    ("p", "03.2023 – по н.в."),
    ("b", "ООО «Пиксель и точка»"),
    ("p", "Frontend-разработчик"),
    ("p", "Личный кабинет на React и TypeScript, компоненты на Tailwind, тесты на Jest."),
    ("gap", ""),
    ("p", "06.2022 – 08.2022"),
    ("b", "ООО «Стажёрская лаборатория»"),
    ("p", "Стажёр-верстальщик"),
    ("p", "Адаптивная вёрстка на HTML и CSS, доработки на JavaScript."),
    ("gap", ""),
    ("h2", "Навыки"),
    ("p", "JavaScript, TypeScript, React, Redux, HTML, CSS, Tailwind CSS, Jest, Git, Figma, Webpack"),
    ("gap", ""),
    ("h2", "Образование"),
    ("p", "Балтийский университет связи, информационные системы, 2023"),
]

FULLSTACK_SENIOR_P1: list[tuple[str, str]] = [
    ("h1", "Резюме"),
    ("p", "ФИО: Шаблонов Михаил Андреевич"),
    ("p", "Дата рождения: 14.03.1990"),
    ("p", "Город: Казань"),
    ("p", "Почта: mikhail.shablonov@example.net, телефон +79035550199"),
    ("p", "GitHub: github.com/shablonov-dev"),
    ("gap", ""),
    ("h2", "Summary"),
    ("p", "Senior fullstack-разработчик: Python на бэкенде, React на фронтенде. Тимлид группы из 4 человек."),
    ("p", "Лидерские качества, системное мышление, умею давать обратную связь."),
    ("gap", ""),
    ("h2", "Experience"),
    ("p", "с 2019 по 2021"),
    ("b", "АО «Облачный конвейер»"),
    ("p", "Ведущий разработчик"),
    ("p", "Микросервисы на Python и Go, Kubernetes, PostgreSQL, ClickHouse, мониторинг в Grafana."),
    ("gap", ""),
    ("p", "2016–2019"),
    ("b", "ООО «Фабрика интерфейсов»"),
    ("p", "Fullstack-разработчик"),
    ("p", "SPA на React и TypeScript, бэкенд на Django REST framework, деплой через Docker."),
]

FULLSTACK_SENIOR_P2: list[tuple[str, str]] = [
    ("p", "Январь 2021 — сейчас"),
    ("b", "ООО «Квантовый склад»"),
    ("p", "Тимлид"),
    ("p", "Архитектура складской системы: FastAPI, React, PostgreSQL, Kafka, Kubernetes."),
    ("gap", ""),
    ("h2", "Skills"),
    ("p", "Python, FastAPI, Django, Go, React, TypeScript, PostgreSQL, ClickHouse, Kafka, Kubernetes, Docker"),
    ("gap", ""),
    ("h2", "Образование"),
    ("p", "Казанский университет информационных технологий, программная инженерия, магистр, 2013"),
    ("gap", ""),
    ("h2", "Языки"),
    ("p", "Английский — B2"),
]


def _draw_page(c: canvas.Canvas, lines: list[tuple[str, str]]) -> None:
    f = register_fonts()
    y = H - 60
    for kind, text in lines:
        if kind == "gap":
            y -= 8
            continue
        if kind == "h1":
            c.setFont(f.bold, 20)
            y -= 6
        elif kind == "h2":
            c.setFont(f.semi, 13)
            y -= 4
        elif kind == "b":
            c.setFont(f.semi, 10.5)
        else:
            c.setFont(f.text, 10.5)
        c.drawString(56, y, text)
        y -= 22 if kind == "h1" else 16


def _write(name: str, pages: list[list[tuple[str, str]]]) -> Path:
    path = OUT / name
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle("Резюме")
    for lines in pages:
        _draw_page(c, lines)
        c.showPage()
    c.save()
    return path


def _write_scan(name: str) -> Path:
    """«Скан»: только графика, ни одного символа текста."""
    path = OUT / name
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setFillGray(0.85)
    for i in range(30):
        c.rect(56, H - 80 - i * 22, 420 - (i * 37) % 160, 9, stroke=0, fill=1)
    c.showPage()
    c.save()
    return path


def main() -> None:
    for p in (
        _write("backend_middle.pdf", [BACKEND_MIDDLE]),
        _write("frontend_junior.pdf", [FRONTEND_JUNIOR]),
        _write("fullstack_senior.pdf", [FULLSTACK_SENIOR_P1, FULLSTACK_SENIOR_P2]),
        _write_scan("scan_no_text.pdf"),
    ):
        print(p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
