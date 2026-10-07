# «Взаимно» — обратный подбор ИТ-специалистов

**Взаимно** — платформа, где работодатель не листает резюме, а описывает потребность текстом с обязательной зарплатной вилкой.
Кандидат проходит опрос и адаптивный тест на одноразовых заданиях и получает категорию «специализация × грейд». Система
рекомендует работодателю категории, показывает, сколько кандидатов отрезает вилка, и строит слепую объяснимую подборку: ранг —
сумма проверяемых фактов (тест, стек, достижения ФСП, свежесть, условия), каждый довод ссылается на доказательство. Резюме и
самоописание на категорию и ранг не влияют; отсутствие истории ФСП — не штраф.

Работодатель приглашает с зарплатой, кандидат отвечает «Да / Да, если… / Нет», контакты раскрываются только после взаимного «да»
и отдельного согласия кандидата (152-ФЗ). Обязательные причины отказов с обеих сторон, надёжность компании, вакансии с откликами,
регулярные задания и стандартизированный PDF-профиль с проверкой по QR дополняют основной путь. LLM в тесте и ранжировании
не участвует: только правила, IRT/CAT и детерминированные формулы из `config/*.yaml`.

## Архитектура

```
 Браузер (React + Vite + TS)  ──────────────►  FastAPI  /api/*   (backend/app)
   кандидат · работодатель · лаборатория           │
                                                   ├─ testing   IRT/CAT-движок, банк семейств заданий, «Антиподсказчик»
   Mailpit :8025 ◄── письма с кодами ──────────────├─ matching  разбор брифа, Score, объяснения, версии подборки
                                                   ├─ contact   приглашения, «Да, если…», раскрытие, отклики, вебхуки ATS
   fsp-mock :8001 ◄── реестр достижений ───────────├─ fsp       VSS, адаптер FSPProvider, вход через ФСП ID (Keycloak)
   Keycloak :8080 (профиль fsp) ◄── OIDC ──────────├─ demo      seed, сброс, автоответчик, часы, режим жюри (DEMO_MODE)
                                                   └─ admin     импорт жюри, ранжирование, метрики пар, валидация
                                                   │
                                          PostgreSQL 16 + pgvector      config/*.yaml — все веса, пороги, квоты
```

- `backend/app` — HTTP-слой (`api/`), сервисы (`services/`), движки без БД (`testing/`, `matching/`, `contact/`, `fsp/`), ORM (`models/`).
- `backend/eval` — генератор синтетической популяции с латентной истиной и `make validate` (тот же код движков, что в проде).
- `services/fsp-mock` — мок реестра ФСП; `deploy/keycloak` — realm `fsp`; `frontend/` — интерфейс.

## Быстрый старт — всё в контейнерах (для жюри)

Нужен только Docker (Compose v2.17+): ни Python, ни Node.js на машине не требуются. Проверено на чистом состоянии (отдельный проект compose).

```bash
git clone <репозиторий> vzaimno && cd vzaimno
docker compose up -d --build                                   # или: make up
docker compose exec -T backend python scripts/seed_demo.py     # демо-мир, или: make demo
```

1. `up --build` собирает образы (backend, web, fsp-mock) и поднимает `db`, `mailpit`, `fsp-mock`, `backend` (миграции Alembic при старте),
   `web` (nginx: интерфейс и прокси `/api`) и одноразовую задачу `pool` (пул заданий теста, ≈ 2,5 минуты: `docker compose logs -f pool`).
   Сборка с тёплым кэшем образов ≈ 1 минута.
2. `seed_demo.py` наполняет демо-мир (≈ 1 минута; ждите конца `pool`: `make demo` ждёт сам).
3. Откройте **http://localhost** (порт — `VZ_WEB_PORT`, по умолчанию 80). API: http://localhost/api/docs, письма с кодами: http://localhost:8025.
4. Логины и пароли демо-аккаунтов: `docker compose exec -T backend cat var/demo_accounts.md` (или `make creds`; пароли выводятся из `VZ_APP_SECRET`).

Сервисы перезапускаются сами (`restart: unless-stopped`), у всех есть healthcheck: `docker compose ps`. Число процессов бэкенда — `VZ_WORKERS`
(по умолчанию 4; на слабом ноутбуке `VZ_WORKERS=1`). Подробности, ресурсы, боевая схема — [docs/DEPLOY.md](docs/DEPLOY.md); нагрузка — [docs/LOAD_TEST.md](docs/LOAD_TEST.md).

Разработка с бэкендом на хосте (нужны [uv](https://docs.astral.sh/uv/), Node.js; контейнер `backend` не поднимайте — порт 8000 занят uvicorn):

```bash
make up-infra        # PostgreSQL, Mailpit, мок реестра ФСП
make migrate seed    # схема БД + демо-мир (первый раз ≈ 5–8 минут)
make dev             # бэкенд в DEMO_MODE: http://localhost:8000/api/docs
cd frontend && npm install && npm run dev      # http://localhost:5173
```

Пароли демо-аккаунтов при запуске с хоста — в `backend/var/demo_accounts.md` (создаёт `make seed`, в git не попадает; в консоль не печатаются).

## Проверьте сами за 5 минут

**Жюри без регистрации: откройте `/jury`** (только DEMO_MODE; на главной появляется ссылка «Вы из жюри?»). Одна кнопка — одноразовый
кандидат с готовым опросом и начатым тестом: **экспресс** (до 8 заданий, ≈ 4 минуты, тот же движок и банк; категорию не присваивает,
при недостатке точности честно пишет «грейд не определён точно, нужен полный тест») или **полный**. Рядом — «Попробовать списать»,
вход в демо-компанию и в лабораторию без пароля, инструкция проверки на своих профилях. Сессии помечены `demo_assisted` и в калибровку
и метрики не попадают. Регистрация в DEMO_MODE не требует кода из письма (`VZ_DEMO_AUTO_VERIFY_EMAIL=false` вернёт код).

Все компании, люди и контакты вымышленные. Логины без паролей отдаёт `GET /api/demo/accounts`, пароли лежат в
`backend/var/demo_accounts.md`. Письма с кодами подтверждения — в Mailpit: http://localhost:8025.

| Роль | Логин | Что посмотреть |
|---|---|---|
| Администратор | `admin@demo.example.com` | `/api/admin/*`: импорт ваших кандидатов и брифов, ранжирование, метрики по размеченным парам |
| Жюри (методист) | `jury@demo.example.com` | Лаборатория `/api/lab/*`: трасса каждой сессии теста, банк семейств, флаги, валидация |
| Работодатель «Северный код» | `hr@severny-kod.example` | Бриф «платёжный шлюз», версии подборки, шортлист, канбан приглашений, вакансии |
| Анна (кандидат) | `anna@demo.example.com` | Backend Middle, призёр ФСП, тест на 12-м задании: два верных ответа — «Middle подтверждён» |
| Илья (кандидат) | `ilya@demo.example.com` | Backend Middle без ФСП, SQL не подтверждён: отклик на вакансию, задача на 10 минут |
| Ступенчатые кандидаты | `stage1-registered@…`, `stage2-survey@…`, `stage3-midtest@…`, `stage4-category@…` | Войти на любом шаге пути: только зарегистрирован / опрос пройден / середина теста / с категорией |
| Мария | `maria@demo.example.com` | Не создана заранее: зарегистрируйтесь сами, код придёт в Mailpit |

Сквозной путь за пять минут:

1. **Свой бриф.** Войдите как `hr@severny-kod.example`, опишите потребность: «Бэкенд в команду платёжного шлюза. Python, FastAPI,
   PostgreSQL, опыт от 2 лет. Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки» → чипы с цитатами, категории и охват
   вилки, подборка v1, закрепите троих (среди них Анна) и пригласите.
2. **Ответ кандидата.** Анна («Да, если: от 270 000 и удалёнка») или любой из ~300 синтетических кандидатов: на приглашения жюри
   автоответчик отвечает за 30–90 секунд — «Да», «Да, если: от min» или «Нет» с причиной.
3. **Взаимное «да».** Работодатель принимает встречное условие — у обеих сторон раскрываются контакты и запись о согласии.
4. **Время.** `POST /api/demo/clock` с `{"preset": "plus_3d"}` («+1 день / +3 дня / сброс»): сроки приглашений и откликов идут вперёд.
5. **Свои данные.** Администратор загружает ваш набор «бриф × кандидат» (формат — `backend/eval/pairs_test.jsonl`, схема
   `backend/eval/pairs_test.schema.json`):

   ```bash
   # 1. войти и получить токен: POST /api/auth/login  → access_token
   # 2. импорт; кандидаты без теста попадут в группу «категория по опросу, не подтверждена» и окажутся ниже подтверждённых
   curl -X POST http://localhost:8000/api/admin/import -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
        -d "{\"name\": \"жюри\", \"pairs\": $(python -c "import json;print(json.dumps([json.loads(l) for l in open('backend/eval/pairs_test.jsonl',encoding='utf-8')][:300]))")}"
   # 3. топ-N по брифу с компонентами Score и «за/против» (JSON или ?format=csv)
   curl -X POST "http://localhost:8000/api/admin/eval/rank?format=csv" -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" -d '{"brief_id": "b01", "top": 10}'
   # 4. P@5, P@10, nDCG@10 со средним по брифам и 95% бутстреп-ДИ
   curl -X POST http://localhost:8000/api/admin/eval/pairs -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{}'
   ```

   Тот же набор проверяется воспроизводимо: `make validate` пишет `backend/eval/out/validation.json` и `docs/VALIDATION.md`;
   `GET /api/admin/validation` отдаёт последний прогон.

Что ещё есть в DEMO_MODE (все маршруты `/api/demo/*` отвечают 404, пока он выключен):

- `POST /api/demo/reset` — быстрый сброс к состоянию seed (≈ 30 секунд): персоны, приглашения, отклики, часы; «Мария» удаляется.
  Перед показом — `make reset-demo`. Режим жюри при сбросе сохраняет своё значение.
- `POST /api/demo/jury-mode` — «режим жюри»: к текущему заданию доступна подсказка `GET /api/demo/jury/hint`; такие сессии
  помечены `demo_assisted` и исключены из метрик. Так жюри в роли кандидата не застревает на тесте.
- Общие кнопки (часы, сброс, автоответчик, переключатель режима жюри) на локальном стенде доступны любой роли; на боевом
  стенде (`VZ_ENV=prod` с `VZ_DEMO_MODE=true`) — только `admin` (`admin@demo.example.com`) или заголовку `X-Demo-Key`
  (`VZ_DEMO_CONTROL_KEY`). Подсказка кандидату в режиме жюри остаётся доступной владельцу сессии.
- `GET /api/demo/webhook-sink` — последние 20 вебхуков ATS. Укажите в профиле компании
  `https://ats.vzaimno-demo.example/api/demo/webhook-sink` — и события будут видны сразу, с проверкой подписи.

## Документация API

- Swagger UI: http://localhost:8000/api/docs
- ReDoc: http://localhost:8000/api/redoc
- OpenAPI: http://localhost:8000/api/openapi.json

## Документы

| Файл | О чём |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | устройство системы и потоки данных |
| [docs/API.md](docs/API.md) | обзор HTTP-API |
| [docs/TESTING_MECHANISM.md](docs/TESTING_MECHANISM.md) | адаптивный тест, одноразовые клоны, «Антиподсказчик» |
| [docs/MATCHING.md](docs/MATCHING.md) | разбор брифа, формула Score, объяснения |
| [docs/FSP_INTEGRATION.md](docs/FSP_INTEGRATION.md) | достижения ФСП, VSS, вход через ФСП ID |
| [docs/SECURITY.md](docs/SECURITY.md) | модель угроз, приватность, 152-ФЗ |
| [docs/VALIDATION_PLAN.md](docs/VALIDATION_PLAN.md), [docs/VALIDATION.md](docs/VALIDATION.md) | протокол валидации и её результаты |
| [services/fsp-mock/README.md](services/fsp-mock/README.md) | мок реестра ФСП |
| [deploy/keycloak/README.md](deploy/keycloak/README.md) | realm `fsp` (ФСП ID) |
| `config/*.yaml` | все веса, пороги, квоты и справочники; демо-мир — `config/demo.yaml` |

## Команды

| Команда | Что делает |
|---|---|
| `make up` / `make down` | поднять / остановить PostgreSQL, Mailpit, fsp-mock |
| `make migrate` | миграции Alembic |
| `make gen-pool` | пул одноразовых экземпляров заданий (`seed` доливает его сам) |
| `make seed` | демо-мир; идемпотентно, повторный запуск пропускает созданное (`--force` пересобирает сценарий) |
| `make reset-demo` | быстрый сброс демо к состоянию seed |
| `make demo-accounts` | пересоздать файл с логинами и паролями |
| `make test` / `make lint` | `pytest -q` и `ruff check .` |
| `make validate` | симуляция и подбор → `docs/VALIDATION.md` |
| `make dev` | бэкенд с автоперезагрузкой в DEMO_MODE |

### Команды без make (Windows, PowerShell)

```powershell
docker compose up -d db mailpit fsp-mock                       # make up-infra
cd backend
uv run alembic upgrade head                                    # make migrate
uv run python scripts/generate_pool.py                         # make gen-pool
$env:PYTHONUTF8 = "1"; uv run python scripts/seed_demo.py      # make seed
uv run python scripts/seed_demo.py --reset                     # make reset-demo
uv run python scripts/seed_demo.py --credentials-only          # make demo-accounts
uv run pytest -q                                               # make test
uv run ruff check .                                            # make lint
uv run python -m eval.validate                                 # make validate
$env:VZ_DEMO_MODE = "true"; uv run uvicorn app.main:app --reload --port 8000   # make dev
```

Целиком в контейнерах: `docker compose up -d --build`, затем `docker compose exec -T backend python scripts/seed_demo.py` (раздел «Быстрый старт»). Переменные окружения `VZ_*` описаны в `.env.example`.
