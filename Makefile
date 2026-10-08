# «Взаимно» — команды разработки и демо. Нужны: Docker (Compose v2.17+), GNU make; для разработки без контейнеров — uv.
# На Windows без make — эквиваленты в README.md (раздел «Команды без make») и docs/DEPLOY.md.
#
# Стенд целиком в контейнерах (для жюри), из чистого клона:
#   make up          собрать образы и поднять db, mailpit, fsp-mock, backend (миграции при старте), web, pool (пул заданий)
#   make demo        демо-мир: ~300 кандидатов с настоящими тестами, персоны, компании, сценарий (≈ 1 минута)
#   make creds       логины и пароли демо-аккаунтов (печатает пароли в консоль)
#   → http://localhost (интерфейс), http://localhost/api/docs, Mailpit http://localhost:8025
#
# Разработка с бэкендом на хосте (контейнер backend при этом не нужен: порт 8000 занимает uvicorn):
#   make up-infra migrate seed dev      (+ cd frontend && npm run dev)
#
#   make migrate     применить миграции Alembic (с хоста)
#   make gen-pool    пул одноразовых экземпляров заданий с хоста (≈ 3 минуты в первый раз)
#   make seed        демо-мир с хоста
#   make dev         бэкенд с автоперезагрузкой в DEMO_MODE (http://localhost:8000/api/docs)
#   make loadtest    нагрузочный тест (loadtest/, docs/LOAD_TEST.md)

BACKEND := backend
UV      := cd $(BACKEND) && uv run
export PYTHONUTF8 := 1
export PYTHONIOENCODING := utf-8

COMPOSE ?= docker compose
USERS   ?= 50
RUNTIME ?= 3m

.PHONY: help up up-infra down ps logs demo creds reset-demo-stack migrate gen-pool seed reset-demo demo-accounts test lint typecheck e2e validate jury-check dev loadtest-accounts loadtest docs-pdf

help:
	@echo "make up | demo | creds | ps | logs | down | up-infra | migrate | gen-pool | seed | reset-demo | test | lint | validate | dev | loadtest-accounts | loadtest"

# Весь стенд в контейнерах: интерфейс на http://localhost (порт VZ_WEB_PORT), пул заданий генерируется фоном (docker compose logs -f pool)
up:
	$(COMPOSE) up -d --build

# Только инфраструктура: бэкенд запускается локально (make dev) и работает с БД на localhost:5432
up-infra:
	$(COMPOSE) up -d db mailpit fsp-mock

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps -a

logs:
	$(COMPOSE) logs -f --tail=100

# Демо-мир внутри контейнера backend (после make up). Ждёт окончания генерации пула, чтобы два процесса не доливали пул одновременно
demo:
	@docker wait $$($(COMPOSE) ps -aq pool) >/dev/null
	$(COMPOSE) exec -T backend python scripts/seed_demo.py

# Логины и пароли демо-аккаунтов (файл в томе контейнера; пароли выводятся из VZ_APP_SECRET)
creds:
	$(COMPOSE) exec -T backend cat var/demo_accounts.md

# Быстрый сброс демо к состоянию seed в контейнерах
reset-demo-stack:
	$(COMPOSE) exec -T backend python scripts/seed_demo.py --reset

# Нагрузочный тест (Locust, loadtest/README в docs/LOAD_TEST.md): сначала make demo, затем аккаунты и прогон
loadtest-accounts:
	$(COMPOSE) exec -T backend python scripts/loadtest_accounts.py --candidates 300 --out var/loadtest_accounts.json
	$(COMPOSE) cp backend:/app/var/loadtest_accounts.json loadtest/accounts.json

loadtest:
	mkdir -p loadtest/out && cd loadtest && uvx --from locust==2.46.7 locust -f locustfile.py --headless -u $(USERS) -r 5 -t $(RUNTIME) 		--host http://localhost:$${VZ_WEB_PORT:-80} --csv out/u$(USERS) --html out/u$(USERS).html

migrate:
	$(UV) alembic upgrade head

# Экземпляры заданий для тестов (идемпотентно; повторный запуск только доливает пул)
gen-pool:
	$(UV) python scripts/generate_pool.py

# Демо-мир (идемпотентно). Логины и пароли — в backend/var/demo_accounts.md (в .gitignore)
seed:
	$(UV) python scripts/seed_demo.py

# Быстрый сброс к состоянию seed (то же, что POST /api/demo/reset): персоны, приглашения, часы
reset-demo:
	$(UV) python scripts/seed_demo.py --reset

# Пересоздать файл с логинами и паролями демо-аккаунтов
demo-accounts:
	$(UV) python scripts/seed_demo.py --credentials-only

test:
	$(UV) pytest -q

lint:
	$(UV) ruff check .

# Статическая типизация бэкенда (mypy; строгий режим на ядрах app/testing и app/matching, см. [tool.mypy] в backend/pyproject.toml)
typecheck:
	$(UV) python scripts/typecheck_strict.py

# Сквозные смоук-тесты Playwright (frontend/e2e): поднимают бэкенд (DEMO_MODE, :8128) и Vite (:5185) сами.
# Нужна БД с демо-миром: make up-infra migrate gen-pool seed. Подробности — docs/ENGINEERING.md
e2e:
	cd frontend/e2e && npm ci --no-audit --no-fund && npx playwright install chromium && node run.mjs

# Валидация решения (симуляция + подбор): docs/VALIDATION.md и backend/eval/out/validation.json
validate:
	$(UV) python -m eval.validate

# Проверка жюри на своих синтетических профилях: make jury-check PROFILES=eval/jury_profiles_example.jsonl
jury-check:
	$(UV) python -m eval.jury_check $(or $(PROFILES),eval/jury_profiles_example.jsonl)

# Бэкенд в DEMO_MODE: автоответчик синтетических кандидатов, «+1 день / +3 дня», режим жюри, приёмник вебхуков
dev:
	cd $(BACKEND) && VZ_DEMO_MODE=true uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# PDF документации: docs/pdf/ (сводный + по документу). Нужны Python (markdown-it-py, pypdf), Node.js, Chrome или Edge и один раз
# `cd docs/pdf-tools && npm install`; подробности в docs/README.md, раздел «PDF-версии»
docs-pdf:
	python docs/build_pdf.py
