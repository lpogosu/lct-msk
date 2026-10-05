"""Сервисный слой механики тестирования: связывает чистые движки app/testing/* с БД и HTTP.

Источники: _internal/design/spec-testing.md §2.2–2.5, §3, §4; 01-concept.md §2.4, §3.1, §5.
Формулы здесь НЕ повторяются: выбор задания, оценка, стоп, решение, доборы, детекторы и правила
квалификации — в app/testing (cat, irt, session, detectors, calibration, survey). Здесь — хранение
состояния, выдача экземпляров (FOR UPDATE SKIP LOCKED), сборка входов движка из БД и DTO.

Контракт для других модулей — app/services/testing/qualification.py: get_qualification_snapshot().
"""
