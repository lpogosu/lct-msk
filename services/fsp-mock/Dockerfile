# Демо-реестр ФСП (мок). Сборка из корня репозитория:
#   docker build -t vzaimno/fsp-mock services/fsp-mock
#   docker run --rm -p 8001:8001 vzaimno/fsp-mock
# Порт 8001 внутри контейнера — контракт docker-compose.yml (сервис fsp-mock, VZ_FSP_REGISTRY_URL).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FSP_MOCK_PUBLIC_URL=http://localhost:8001

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY fsp_mock ./fsp_mock

USER nobody
EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=2)"
CMD ["uvicorn", "fsp_mock.main:app", "--host", "0.0.0.0", "--port", "8001"]
