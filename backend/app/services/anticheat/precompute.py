"""Предрасчёт «двойников в буфере» для свободных экземпляров пула.

    cd backend
    uv run python -m app.services.anticheat.precompute                       # все пригодные семейства, по 40 экземпляров
    uv run python -m app.services.anticheat.precompute --family py.defaults.mutable --per-family 100
    uv run python -m app.services.anticheat.precompute --stats               # только покрытие

Запускать после `scripts/generate_pool.py` (и после долива пула): без готового двойника задание уходит кандидату
без подмены (пропуск, а не ошибка). Повторный запуск ничего не меняет: посчитанные экземпляры пропускаются.
Двойник строится так: Family.twin() семейства первым (QA, DevOps), затем общий мутатор (код/SQL) — см. dispatch.py.
Ключи двойников шифруются VZ_APP_SECRET, как ключи экземпляров.
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.services.anticheat import store
from app.services.anticheat.cfg import runtime_config

log = logging.getLogger("vzaimno.anticheat.precompute")

_RUN_LOCK = threading.Lock()
_RUNNING = False


def is_running() -> bool:
    return _RUNNING


def run_background(bind: Any, family_ids: Sequence[str] | None, per_family: int | None) -> bool:
    """Предрасчёт в фоне (кнопка в лаборатории): своя сессия на том же движке. False — уже идёт или сессия запроса
    привязана не к Engine (тесты: транзакция с откатом) — из другого потока её трогать нельзя."""
    global _RUNNING  # noqa: PLW0603 — один фоновый предрасчёт на процесс
    if not isinstance(bind, Engine):
        return False
    with _RUN_LOCK:
        if _RUNNING:
            return False
        _RUNNING = True
    try:
        with Session(bind=bind, autoflush=False, expire_on_commit=False) as s:
            store.precompute_twins(s, family_ids=family_ids, per_family=per_family, commit=s.commit)
            s.commit()
    except Exception:  # noqa: BLE001 — фоновая задача не должна ронять процесс; повторить можно из CLI
        log.exception("Фоновый предрасчёт двойников не удался")
    finally:
        with _RUN_LOCK:
            _RUNNING = False
    return True


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    rc = runtime_config()
    p = argparse.ArgumentParser(description="Предрасчёт двойников в буфере для свободных экземпляров пула")
    p.add_argument("--family", action="append", default=None, help="Только эти семейства (можно несколько раз)")
    p.add_argument("--per-family", type=int, default=rc.precompute_per_family, help="Экземпляров на семейство за запуск")
    p.add_argument("--workers", type=int, default=None, help="Потоков исполнения (по умолчанию по ядрам: runtime.precompute.workers … workers_cap)")
    p.add_argument("--stats", action="store_true", help="Только показать покрытие")
    p.add_argument(
        "--after-seed",
        type=int,
        default=0,
        metavar="СЕК",
        help="Ждать до СЕК секунд, пока seed демо-мира не закончится (demo_state.seeded_at), и только потом считать "
        "двойников: на локальном стенде предрасчёт иначе отнимает процессор у seed и тот идёт вдвое дольше",
    )
    return p.parse_args(argv)


def wait_for_seed(db: Session, max_wait_s: int, poll_s: float = 3.0) -> bool:
    """Подождать конца seed демо-мира (до max_wait_s). True — seed закончился; False — вышло время или демо-таблиц нет."""
    import time  # noqa: PLC0415

    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.exc import SQLAlchemyError  # noqa: PLC0415

    from app.services.demo.models import DemoState  # noqa: PLC0415

    deadline = time.monotonic() + max_wait_s
    while True:
        try:
            seeded_at = db.scalar(select(DemoState.seeded_at).where(DemoState.id == 1))  # столбец, а не объект: без кэша сессии
        except SQLAlchemyError:
            db.rollback()
            return False
        if seeded_at is not None:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)


def main(argv: Sequence[str] | None = None) -> int:
    from app.db.session import get_sessionmaker  # noqa: PLC0415 — CLI подключает БД только при запуске
    from app.services.anticheat import coverage  # noqa: PLC0415

    args = _parse(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    db = get_sessionmaker()()
    try:
        if not args.stats and args.after_seed > 0:
            done = wait_for_seed(db, args.after_seed)
            print("seed демо закончен — считаю двойников" if done else "seed не дождались — считаю двойников сейчас", flush=True)
        if not args.stats:

            def say(rep: store.PrecomputeReport) -> None:
                print(
                    f"{rep.family_id:36s} к расчёту {rep.candidates:4d}  готово {rep.built:4d}  без двойника {rep.none:4d}"
                    f"  не по форме {rep.skipped_ineligible:4d}  {rep.seconds:6.1f} с",
                    flush=True,
                )

            store.precompute_twins(
                db, family_ids=args.family, per_family=args.per_family, workers=args.workers, commit=db.commit, progress=say
            )
            db.commit()
        out = coverage.coverage(db)
        t = out["totals"]
        print(
            f"Семейств с двойником: {t['families_with_twin']} из {t['families']}; свободных экземпляров {t['pool_free']}, "
            f"двойников готово {t['twins_ok']}, ждут расчёта {t['pending']}, выдано кандидатам {t['shown']}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
