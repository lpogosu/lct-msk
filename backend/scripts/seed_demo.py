"""Демо-мир «Взаимно»: пул заданий, ~300 синтетических кандидатов с настоящими тестами, персоны, компании, сценарий.

    cd backend
    uv run python scripts/seed_demo.py                    # полная идемпотентная сборка (первый раз — несколько минут)
    uv run python scripts/seed_demo.py --candidates 40    # малый мир для проверки
    uv run python scripts/seed_demo.py --add-specs qa,devops --candidates 40
                                                          # ДОБАВИТЬ по 40 кандидатов QA и DevOps к собранному миру
                                                          # (аддитивно: популяция, персоны и сценарий не пересобираются)
    uv run python scripts/seed_demo.py --force            # пересобрать сценарий (персоны, брифы, приглашения)
    uv run python scripts/seed_demo.py --reset            # быстрый сброс к состоянию seed (то же, что POST /api/demo/reset)
    uv run python scripts/seed_demo.py --credentials-only # пересоздать backend/var/demo_accounts.md

Что делает (подробности — app/services/demo/world.py, config/demo.yaml):
1. Пул одноразовых экземпляров заданий (как scripts/generate_pool.py) и замена старых предсказуемых seed.
2. Кандидаты из eval/population.py: аккаунт, профиль, опрос, настоящая сессия адаптивного теста на выданных экземплярах,
   ФСП-привязка к спортсменам мок-реестра, регулярные задачи. Процессы-воркеры работают параллельно.
3. Персоны сценария §11 (Анна — сессия на 12-м задании, Илья — SQL не подтверждён), ступенчатые аккаунты, жюри, методист, админ.
4. Шесть вымышленных компаний, брифы с версиями подборки и шортлистами, приглашения во всех статусах, вакансии и отклики, задачи.
5. Логины и пароли — в backend/var/demo_accounts.md (в .gitignore). В stdout пароли не печатаются.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # запуск как `python scripts/seed_demo.py`


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Демо-мир «Взаимно»: seed и сброс")
    p.add_argument("--candidates", type=int, default=None,
                   help="Сколько синтетических кандидатов (по умолчанию из config/demo.yaml); с --add-specs — на каждую специализацию")
    p.add_argument("--add-specs", default=None, metavar="СПЕЦ[,СПЕЦ]",
                   help="Только добавить кандидатов этих специализаций (например qa,devops); --candidates — сколько "
                        "на каждую (по умолчанию из config/demo.yaml → population.extra_specs); мир не пересобирается")
    p.add_argument("--workers", type=int, default=None,
                   help="Процессов-воркеров для создания кандидатов (по умолчанию — половина ядер, 2–8; 1 — в этом процессе)")
    p.add_argument("--force", action="store_true", help="Пересобрать сценарий, даже если он уже собран")
    p.add_argument("--reset", action="store_true", help="Только быстрый сброс (популяция и пул не трогаются)")
    p.add_argument("--skip-pool", action="store_true", help="Не проверять и не доливать пул экземпляров")
    p.add_argument("--credentials-only", action="store_true", help="Только пересоздать файл с логинами и паролями")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    logging.getLogger("app.services.testing.pool").setLevel(logging.ERROR)

    from app.core.settings import get_settings
    from app.db.session import get_sessionmaker
    from app.services.demo import models as _models  # noqa: F401 — таблицы демо в Base.metadata
    from app.services.demo import sink, world

    settings = get_settings()
    if settings.env == "prod":
        print("Отказ: seed демо-мира не запускается при VZ_ENV=prod")
        return 2
    if args.credentials_only:
        print(f"Файл с учётными данными: {world.write_credentials_file(settings)}")
        return 0

    t0 = time.monotonic()

    def say(msg: str) -> None:
        print(f"[{time.monotonic() - t0:6.1f} с] {msg}", flush=True)

    workers = args.workers if args.workers is not None else max(2, min(8, (os.cpu_count() or 4) // 2))
    sink.install()  # доставки вебхуков на демо-хост ATS принимаются на месте
    db = get_sessionmaker()()
    try:
        if args.reset:
            out = world.reset_world(settings, db, say=say)
        else:
            out = world.seed_world(
                settings, db, candidates=args.candidates, workers=workers, say=say,
                force_scenario=args.force, skip_pool=args.skip_pool,
                add_specs=tuple(x.strip() for x in args.add_specs.split(",") if x.strip()) if args.add_specs else (),
            )
        print()
        print(f"Готово за {out['seconds']} с")
        for k, v in out["counts"].items():
            print(f"  {k}: {v}")
        sc = out.get("scenario")
        if sc:
            print("  сценарий:", {k: v for k, v in sc.items() if k != "skipped"})
            for s in sc.get("skipped", []):
                print("   пропущено:", s)
        pop = out.get("population")
        if pop and pop.get("errors"):
            print(f"  ошибки при создании кандидатов ({pop['failed']}):")
            for e in pop["errors"][:10]:
                print("   ", e)
        print(f"Логины и пароли: {out['credentials_file']}")
        return 1 if (pop and pop.get("failed")) else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
