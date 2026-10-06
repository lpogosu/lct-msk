"""«Взаимно» — бэкенд."""

from pydantic import BaseModel

# Поля с умолчаниями в ответах API обязательны в OpenAPI (json_schema_serialization_defaults_required): сервер
# всегда присылает такое поле, и клиентские типы (frontend/src/api/schema.d.ts) не должны делать его необязательным —
# иначе в TypeScript приходится проверять то, что гарантировано. Включаем для всех моделей разом, до их объявления:
# на валидацию запросов настройка не влияет, только на JSON Schema режима serialization.
BaseModel.model_config["json_schema_serialization_defaults_required"] = True
