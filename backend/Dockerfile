# syntax=docker/dockerfile:1.7
# Бэкенд «Взаимно»: python 3.12-slim + uv, зависимости строго по uv.lock, процесс от непривилегированного пользователя.
#
# Сборка через compose (рекомендуется): docker compose build backend
# Вручную: docker build --build-context config=../config -t vzaimno-backend .
#   (config/*.yaml лежит вне каталога backend и передаётся отдельным контекстом «config»)

FROM ghcr.io/astral-sh/uv:0.11.12 AS uv

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    VZ_CONFIG_DIR=/app/config

COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /app

# Сначала только зависимости — слой кэшируется, пока не меняется uv.lock
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Код приложения и миграции (без .venv, тестов и кэшей)
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY --from=config . ./config

RUN groupadd --gid 10001 vz \
    && useradd --uid 10001 --gid vz --no-create-home --home-dir /app --shell /usr/sbin/nologin vz \
    && find /app -name "__pycache__" -type d -prune -exec rm -rf {} + \
    && chown -R vz:vz /app

USER vz

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=10 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/live', timeout=3).status == 200 else 1)"

# Миграции применяются при каждом старте (идемпотентно), затем сервер
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
