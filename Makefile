# «Взаимно» — команды разработки и демо. Нужны: Docker, uv (https://docs.astral.sh/uv/), GNU make.
# На Windows без make — эквиваленты в PowerShell указаны в README.md (раздел «Команды без make»).
#
#   make up          поднять БД, почтовый перехватчик Mailpit и мок реестра ФСП (docker compose)
#   make migrate     применить миграции Alembic
#   make gen-pool    пул одноразовых экземпляров заданий (≈3 минуты в первый раз)
#   make seed        демо-мир: ~300 кандидатов с настоящими тестами, персоны, компании, сценарий
#   make dev         бэкенд с автоперезагрузкой в DEMO_MODE (http://localhost:8000/api/docs)
#
# Первый запуск: make up migrate seed dev

BACKEND := backend
UV      := cd $(BACKEND) && uv run
export PYTHONUTF8 := 1
export PYTHONIOENCODING := utf-8

.PHONY: help up down migrate gen-pool seed reset-demo demo-accounts test lint validate dev

help:
	@echo "make up | down | migrate | gen-pool | seed | reset-demo | demo-accounts | test | lint | validate | dev"

# Инфраструктура без контейнера бэкенда: бэкенд запускается локально (make dev) и работает с БД на localhost:5432
up:
	docker compose up -d db mailpit fsp-mock

down:
	docker compose down

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

# Валидация решения (симуляция + подбор): docs/VALIDATION.md и backend/eval/out/validation.json
validate:
	$(UV) python -m eval.validate

# Бэкенд в DEMO_MODE: автоответчик синтетических кандидатов, «+1 день / +3 дня», режим жюри, приёмник вебхуков
dev:
	cd $(BACKEND) && VZ_DEMO_MODE=true uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
