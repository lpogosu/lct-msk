# Сторонние компоненты и лицензии

Список библиотек, контейнерных образов, шрифтов и ресурсов, из которых собрано «Взаимно», с версиями и лицензиями. Версии Python-пакетов извлечены скриптом из `backend/uv.lock` (зафиксированное разрешение зависимостей), версии JavaScript-пакетов — из `frontend/package-lock.json`, образы — из `docker-compose.yml` и `backend/Dockerfile`; лицензии Python-пакетов — из метаданных PyPI для зафиксированных версий (с проверкой по установленным пакетам). Состояние на 2026-10-05. Все компоненты имеют открытые лицензии; собственный код проекта — «Взаимно» (хакатон ЛЦТ-2026, спецтрек ФСП).

Содержание: [1. Бэкенд (Python)](#1-бэкенд-python) · [2. Фронтенд (JavaScript)](#2-фронтенд-javascript) · [3. Образы и системные компоненты](#3-образы-и-системные-компоненты) · [4. Шрифты](#4-шрифты) · [5. Иллюстрации и данные](#5-иллюстрации-и-данные) · [6. Замечания по лицензиям](#6-замечания-по-лицензиям) · [7. Как обновить список](#7-как-обновить-список)

## 1. Бэкенд (Python)

Требование проекта: Python не ниже 3.11 (`requires-python`). В Docker-образе — Python 3.12 (`python:3.12-slim`), зависимости ставятся строго по `uv.lock` (`uv sync --frozen --no-dev`). Для `numpy` и `scipy` lock содержит две ветки разрешения (Python 3.11 и Python 3.12 и выше); в образе действует вторая.

### 1.1. Прямые зависимости (`backend/pyproject.toml`)

| Пакет | Версия | Лицензия | Для чего |
|---|---|---|---|
| `alembic` | 1.20.0 | MIT | Миграции схемы БД |
| `argon2-cffi` | 25.1.0 | MIT | Хеширование паролей argon2id |
| `email-validator` | 2.3.0 | Unlicense | Проверка адресов email |
| `fastapi` | 0.142.2 | MIT | HTTP-фреймворк API |
| `httpx` | 0.28.1 | BSD-3-Clause | HTTP-клиент: реестр ФСП, OIDC, LLM, вебхуки ATS |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause | Fernet: шифрование ключей заданий (`app/services/testing/keystore.py`); объявлена напрямую |
| `numpy` | 2.4.6 (Python 3.11) | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Расчёты IRT/CAT (сетка EAP, калибровка) |
| `numpy` | 2.5.3 (Python ≥ 3.12, образ Docker) | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | Расчёты IRT/CAT (сетка EAP, калибровка) |
| `pdfminer.six` | 20260107 | MIT | Исключения разбора PDF (`PDFSyntaxError`); объявлена напрямую |
| `pdfplumber` | 0.11.10 | MIT | Извлечение текста из PDF-резюме |
| `pgvector` | 0.5.0 | MIT | Клиент pgvector (расширение в БД включено; в коде пока не импортируется) |
| `psycopg` | 3.3.6 | LGPL-3.0-only | Драйвер PostgreSQL (psycopg 3, extra `binary`) |
| `pyahocorasick` | 2.3.1 | BSD-3-Clause (и Public Domain) | Поиск словарных фраз (навыки, роли) в тексте брифа и резюме |
| `pydantic` | 2.13.5 | MIT | Модели и валидация DTO |
| `pydantic-settings` | 2.15.0 | MIT | Настройки `VZ_*` из окружения |
| `pyjwt` | 2.15.1 | MIT | Access-токены JWT (extra `crypto`: cryptography) |
| `python-multipart` | 0.0.32 | Apache-2.0 | Приём загрузки файла (резюме) |
| `pyyaml` | 6.0.3 | MIT | Чтение `config/*.yaml` |
| `qrcode` | 8.2 | BSD | QR-код сверки в PDF-профиле (extra `pil`: Pillow) |
| `rapidfuzz` | 3.14.6 | MIT | Нечёткое сравнение: навыки с опечатками, проверка цитат LLM |
| `reportlab` | 5.0.1 | BSD (лицензия ReportLab Inc.) | Генерация PDF-профиля |
| `sqlalchemy` | 2.1.3 | MIT | ORM и доступ к PostgreSQL |
| `starlette` | 1.7.0 | BSD-3-Clause | ASGI-основа FastAPI: статика документации, ответы, конкурентность (`app/main.py`, `app/api/demo.py`); объявлена напрямую |
| `uvicorn` | 0.54.0 | BSD-3-Clause | ASGI-сервер (extra `standard`: httptools, uvloop, watchfiles, websockets, python-dotenv) |

### 1.2. Транзитивные зависимости времени выполнения

Все пакеты, которые попадают в образ, кроме прямых (включая extras: `uvicorn[standard]`, `psycopg[binary]`, `pyjwt[crypto]`, `qrcode[pil]`). Колонка «Нужен для» — прямые зависимости, которые их тянут.

| Пакет | Версия | Лицензия | Нужен для |
|---|---|---|---|
| `annotated-doc` | 0.0.5 | MIT | `fastapi` |
| `annotated-types` | 0.8.0 | MIT | `fastapi`, `pydantic`, `pydantic-settings` |
| `anyio` | 4.15.1 | MIT | `fastapi`, `httpx`, `uvicorn` |
| `argon2-cffi-bindings` | 26.1.0 | MIT | `argon2-cffi` |
| `certifi` | 2026.7.22 | MPL-2.0 | `httpx` |
| `cffi` | 2.1.1 | MIT-0 | `argon2-cffi`, `pdfplumber`, `pyjwt` |
| `charset-normalizer` | 3.5.2 | MIT | `pdfplumber`, `reportlab` |
| `click` | 8.5.0 | BSD-3-Clause | `uvicorn` |
| `colorama` | 0.4.6 | BSD | `qrcode` |
| `cryptography` | 50.0.2 | Apache-2.0 OR BSD-3-Clause | прямая; также `pdfplumber`, `pyjwt` |
| `dnspython` | 2.8.0 | ISC | `email-validator` |
| `h11` | 0.16.0 | MIT | `httpx`, `uvicorn` |
| `httpcore` | 1.0.9 | BSD-3-Clause | `httpx` |
| `httptools` | 0.8.0 | MIT | `uvicorn` |
| `idna` | 3.20 | BSD-3-Clause | `email-validator`, `fastapi`, `httpx`, `uvicorn` |
| `mako` | 1.4.3 | MIT | `alembic` |
| `markupsafe` | 3.0.4 | BSD-3-Clause | `alembic` |
| `opentelemetry-api` | 1.45.0 | Apache-2.0 | `fastapi` |
| `pdfminer-six` | 20260107 | MIT | прямая (`pdfminer.six`); также `pdfplumber` |
| `pillow` | 12.3.0 | MIT-CMU | `pdfplumber`, `qrcode`, `reportlab` |
| `psycopg-binary` | 3.3.6 | LGPL-3.0-only | `psycopg` |
| `pycparser` | 3.0 | BSD-3-Clause | `argon2-cffi`, `pdfplumber`, `pyjwt` |
| `pydantic-core` | 2.46.5 | MIT | `fastapi`, `pydantic`, `pydantic-settings` |
| `pypdfium2` | 5.14.0 | BSD-3-Clause или Apache-2.0 (плюс лицензии вложенных библиотек PDFium) | `pdfplumber` |
| `python-dotenv` | 1.2.4 | BSD-3-Clause | `pydantic-settings`, `uvicorn` |
| `starlette` | 1.7.0 | BSD-3-Clause | прямая; также `fastapi` |
| `typing-extensions` | 4.16.0 | PSF-2.0 | `alembic`, `fastapi`, `httpx`, `psycopg`, `pydantic`, `pydantic-settings`, `sqlalchemy`, `uvicorn` |
| `typing-inspection` | 0.4.4 | MIT | `fastapi`, `pydantic`, `pydantic-settings` |
| `tzdata` | 2026.5 | Apache-2.0 | `psycopg` |
| `uvloop` | 0.23.0 | Apache-2.0 или MIT | `uvicorn` |
| `watchfiles` | 1.3.0 | MIT | `uvicorn` |
| `websockets` | 17.2 | BSD-3-Clause | `uvicorn` |

`colorama` ставится только на Windows (зависимость `qrcode`); `uvloop`, `httptools` — на Linux и macOS.

### 1.3. Инструменты разработки (в образ не входят)

| Пакет | Версия | Лицензия | Тип |
|---|---|---|---|
| `iniconfig` | 2.3.0 | MIT | транзитивная |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | транзитивная |
| `pluggy` | 1.6.0 | MIT | транзитивная |
| `pygments` | 2.21.0 | BSD-2-Clause | транзитивная |
| `pytest` | 9.1.1 | MIT | прямая |
| `pytest-asyncio` | 1.4.0 | Apache-2.0 | прямая |
| `scipy` | 1.17.1 (Python 3.11), 1.18.1 (Python ≥ 3.12) | BSD | прямая (группа `dev`): только `tests/test_matching_scoring.py`, в образ не входит |
| `ruff` | 0.16.10 | MIT | прямая |

## 2. Фронтенд (JavaScript)

`frontend/package.json` (версия приложения 0.1.0), версии — из `package-lock.json` (lockfileVersion 3); сборка `npm run build` (TypeScript и Vite). Окружение сборки: Node.js 22 LTS (проверено на 22.20.0) и npm 11. Фронтенд в разработке: список будет дополняться по мере появления экранов, а таблицы ниже перегенерируются из `package-lock.json`.

### 2.1. Зависимости, попадающие в сборку (`dependencies`)

| Пакет | Версия | Лицензия | Диапазон в `package.json` |
|---|---|---|---|
| `@tanstack/react-query` | 5.104.1 | MIT | `^5.62.0` |
| `clsx` | 2.1.1 | MIT | `^2.1.1` |
| `openapi-fetch` | 0.17.0 | MIT | `^0.17.0` |
| `react` | 18.3.1 | MIT | `^18.3.1` |
| `react-dom` | 18.3.1 | MIT | `^18.3.1` |
| `react-router-dom` | 7.18.4 | MIT | `^7.1.0` |
| `tailwind-merge` | 3.7.0 | MIT | `^3.0.0` |

Всего пакетов, попадающих в сборку, с транзитивными: 15. Лицензии: MIT — 15.

### 2.2. Инструменты сборки и контроля качества (`devDependencies`)

| Пакет | Версия | Лицензия | Диапазон в `package.json` |
|---|---|---|---|
| `@eslint/js` | 9.39.5 | MIT | `^9.17.0` |
| `@types/node` | 22.20.5 | MIT | `^22.10.0` |
| `@types/react` | 18.3.31 | MIT | `^18.3.12` |
| `@types/react-dom` | 18.3.7 | MIT | `^18.3.1` |
| `@vitejs/plugin-react` | 5.2.0 | MIT | `^5.0.0` |
| `autoprefixer` | 10.6.1 | MIT | `^10.4.20` |
| `eslint` | 9.39.5 | MIT | `^9.17.0` |
| `eslint-plugin-react-hooks` | 5.2.0 | MIT | `^5.1.0` |
| `eslint-plugin-react-refresh` | 0.4.26 | MIT | `^0.4.16` |
| `globals` | 15.15.0 | MIT | `^15.14.0` |
| `openapi-typescript` | 7.13.0 | MIT | `^7.13.0` |
| `postcss` | 8.5.29 | MIT | `^8.4.49` |
| `sharp` | 0.34.5 | Apache-2.0 | `^0.34.0` |
| `tailwindcss` | 3.4.19 | MIT | `^3.4.17` |
| `typescript` | 5.9.3 | Apache-2.0 | `~5.9.0` |
| `typescript-eslint` | 8.71.0 | MIT | `^8.18.0` |
| `vite` | 7.3.6 | MIT | `^7.0.0` |

Всего dev-пакетов с транзитивными: 336. Сводка лицензий dev-пакетов:

| Лицензия | Пакетов |
|---|---|
| MIT | 261 |
| Apache-2.0 | 30 |
| ISC | 18 |
| LGPL-3.0-or-later | 10 |
| BSD-2-Clause | 6 |
| Apache-2.0 AND LGPL-3.0-or-later | 3 |
| BSD-3-Clause | 2 |
| Apache-2.0 AND LGPL-3.0-or-later AND MIT | 1 |
| BlueOak-1.0.0 | 1 |
| Python-2.0 | 1 |
| CC-BY-4.0 | 1 |
| 0BSD | 1 |
| (MIT OR CC0-1.0) | 1 |

## 3. Образы и системные компоненты

| Компонент | Версия (источник) | Лицензия | Назначение |
|---|---|---|---|
| PostgreSQL 16 с расширением pgvector | `pgvector/pgvector:0.8.7-pg16` (`docker-compose.yml`, `docker-compose.prod.yml`) | PostgreSQL License (PostgreSQL и pgvector) | База данных |
| PostgreSQL 16 (Keycloak) | `postgres:16.15-alpine` (`docker-compose.prod.yml`, профиль `fsp`) | PostgreSQL License | БД Keycloak на боевой схеме |
| Keycloak | `quay.io/keycloak/keycloak:26.0.8` (профиль `fsp`; локально `start-dev`, в prod `start`) | Apache-2.0 | ФСП ID на демо-стенде |
| Mailpit | `axllent/mailpit:v1.31.4` (`docker-compose.yml`) | MIT | Перехват писем в разработке |
| Python | `python:3.12-slim` (`backend/Dockerfile`, `services/fsp-mock/Dockerfile`) | PSF-2.0 (CPython); базовый образ Debian содержит пакеты под своими лицензиями | Среда выполнения бэкенда и `fsp-mock` |
| uv | `ghcr.io/astral-sh/uv:0.11.12` (`backend/Dockerfile`) | MIT или Apache-2.0 | Установка зависимостей по `uv.lock` |
| Caddy 2 | `caddy:2.10.2-alpine` (`docker-compose.prod.yml`, [DEPLOY.md](DEPLOY.md)) | Apache-2.0 | Обратный прокси, TLS |
| Swagger UI (swagger-ui-dist) | 5.33.1, файлы в `backend/app/static/docs` | Apache-2.0 (лицензия и NOTICE рядом) | Интерактивная документация `/api/docs`, без внешних CDN |
| ReDoc | 2.5.4 (`redoc.standalone.js`), файлы в `backend/app/static/docs` | MIT (лицензия рядом) | Документация `/api/redoc`, без внешних CDN и шрифтов |
| Node.js | 22 LTS на машине сборки и при генерации пула клонов (семейства JavaScript исполняются `node -e`) | MIT-подобная (Node.js license) | Сборка фронтенда, ключи JS-заданий |
| SQLite | Встроен в Python (`sqlite3`) | Public Domain | Исполнение SQL-семейств при генерации ключей заданий |

Образы compose закреплены по версиям (БД, Caddy, Keycloak, Mailpit); плавающим остаётся только `python:3.12-slim` в Dockerfile — для воспроизводимого релиза его нужно закрепить по digest.

## 4. Шрифты

Все шрифты распространяются по SIL Open Font License 1.1 (OFL) и размещены в репозитории вместе с текстом лицензии; внешних обращений к Google Fonts и CDN нет.

| Шрифт | Версия | Где используется | Файлы |
|---|---|---|---|
| Inter | 4.001 (Inter Project Authors) | PDF-профиль (бэкенд, reportlab) | `backend/app/assets/fonts/Inter-*.ttf`, `Inter-OFL.txt` |
| JetBrains Mono | 2.304 (JetBrains Mono Project Authors) | PDF-профиль: числа, код сверки, выписка ФСП | `backend/app/assets/fonts/JetBrainsMono-*.ttf`, `JetBrainsMono-OFL.txt` |
| Onest | woff2 из Google Fonts (Onest Project Authors); версия файла в метаданных не указана | Интерфейс (основной) | `frontend/public/fonts/onest-*.woff2`, `OFL-Onest.txt` |
| JetBrains Mono | woff2 (JetBrains Mono Project Authors) | Интерфейс: числа, коды | `frontend/public/fonts/jetbrains-mono-*.woff2`, `OFL-JetBrainsMono.txt` |

## 5. Иллюстрации и данные

| Ресурс | Происхождение и права |
|---|---|
| 3D-иллюстрации интерфейса (`frontend/public/3d/*.webp`) | Собственные: отрисованы локальным скриптом на `numpy` и `Pillow` (трассировка полей расстояний), сторонних моделей и сервисов нет |
| Иконка `favicon.svg` | Собственная |
| Данные ФСП в демо | Синтетические: вымышленные спортсмены, команды и номера ЕКП (после года стоит «99»), фамилии собраны из ИТ-терминов |
| Демо-компании и кандидаты | Вымышленные; домены в зоне `.example`; реальных брендов и ФИО нет |
| Справочники (навыки, города, имена для разбора резюме, матрицы весов) | Собственные: `config/*.yaml` и `backend/app/services/resume/names_data.py` |
| Банк заданий | Собственные генераторы (`backend/app/testing/bank/families`); эталонный код и ключи порождаются исполнением |
| Эмбеддинги, предобученные модели, внешние датасеты | Не используются |

## 6. Замечания по лицензиям

- **LGPL-3.0: `psycopg` и `psycopg-binary`** (драйвер PostgreSQL; бинарная сборка включает libpq). Используются как неизменённые библиотеки, подключаемые динамически, что допускается лицензией. Лицензия отличается от MIT, BSD и Apache: при распространении образов нужно сохранить уведомления и обеспечить доступ к исходникам библиотеки (они публичны).
- **MPL-2.0: `certifi`** (набор корневых сертификатов) — слабый копилефт на уровне файлов; библиотека не изменялась.
- **MIT-CMU: `pillow`**, **PSF-2.0: `typing-extensions`**, **ISC: `dnspython`**, **Unlicense: `email-validator`** — разрешительные лицензии.
- **`pypdfium2`** включает бинарную сборку PDFium (BSD-3-Clause и Apache-2.0) и её собственные зависимости под своими лицензиями; **`numpy`** содержит части под 0BSD, MIT, Zlib и CC0-1.0.
- **Фронтенд, dev-зависимости:** пакеты `@img/sharp-libvips-*` под LGPL-3.0-or-later (бинарная libvips для `sharp`) нужны только скрипту оптимизации изображений `npm run optimize:3d` и в сборку (`dist/`) не попадают.
- **Прямые импорты и `pyproject.toml`:** `cryptography`, `pdfminer.six` и `starlette` объявлены явно (раньше приходили транзитивно); `scipy` перенесён в группу `dev`, потому что нужен только тестам. Пакет `pgvector` объявлен намеренно: расширение в БД уже включено, клиент понадобится для эмбеддингов (план NLP), в коде пока не импортируется.
- Образы `latest` и плавающие теги не воспроизводимы; закрепить перед релизом.
- Автоматическая проверка лицензий и уязвимостей зависимостей в CI — в разработке.

## 7. Как обновить список

```bash
# Python: версии и зависимости из lock
cd backend
uv lock --check                    # lock актуален
uv tree --locked                   # дерево зависимостей
uv run python -c "import tomllib; d = tomllib.load(open('uv.lock','rb')); [print(p['name'], p['version']) for p in d['package']]"

# JavaScript
cd ../frontend
npm ci
npm ls --all --omit=dev            # дерево зависимостей сборки
npm ls --all                       # вместе с dev
```

Лицензия конкретного Python-пакета: `uv run python -c "from importlib.metadata import metadata; m = metadata('fastapi'); print(m['License-Expression'] or m['License'])"`. Для JavaScript лицензия указана в поле `license` записи пакета в `package-lock.json`.
