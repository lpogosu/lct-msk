"""Пул одноразовых экземпляров заданий и экспертные приоры семейств (spec-testing §2.5, §3.2, §3.8, §4).

    cd backend
    uv run python scripts/generate_pool.py                      # 200 экземпляров на семейство, все шкалы
    uv run python scripts/generate_pool.py --spec frontend_js --n 200 --min-free 50 --threads 4
    uv run python scripts/generate_pool.py --stats              # только показать состояние пула
    uv run python scripts/generate_pool.py --rotate-legacy      # заменить свободные экземпляры со старыми seed

Что делает:
1. ItemFamilyParams — по строке на (семейство × шкала) с экспертным приором b = cut(g) + 0.35·pos и σ_b;
   уже существующие строки (в том числе уточнённые калибровкой) не трогаются.
2. ItemInstance — догоняет пул каждого семейства до N экземпляров (rendered — только публичная часть,
   ключ зашифрован, key_hash для детектора «чужого ключа», код варианта) и доливает до N свободных, если
   свободных меньше --min-free. Повторный запуск ничего не меняет (UNIQUE(family_id, seed)).
   Seed экземпляров секретные (HMAC от VZ_APP_SECRET): по коду варианта и открытому коду генератора
   ключ не восстановить. Пул, созданный до этого (seed 0, 1, 2, …), заменяется флагом --rotate-legacy:
   свободные экземпляры со старыми seed удаляются, выданные остаются, пул доливается заново.

Генерация идёт потоками (банк потокобезопасен), запись — в главном потоке, коммит после каждого семейства:
прерванный запуск продолжается с того же места. JS-семействам нужен `node` в PATH.
Ключи шифруются VZ_APP_SECRET — пул, созданный с другим секретом, сервер не расшифрует.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # запуск как `python scripts/generate_pool.py`

from app.db.session import get_sessionmaker  # noqa: E402
from app.services.testing.limits import POOL_MIN_FREE, POOL_SIZE_DEFAULT  # noqa: E402
from app.services.testing.pool import (  # noqa: E402
    EMPTY_STATS,
    PoolReport,
    ensure_pool,
    families_for,
    pool_stats,
    rotate_legacy,
    seed_family_params,
)


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Генерация пула экземпляров заданий и экспертных приоров семейств")
    p.add_argument("--spec", help="Только семейства этой специализации (backend_python | frontend_js)")
    p.add_argument("--family", action="append", default=None, help="Только эти семейства (можно несколько раз)")
    p.add_argument("--n", type=int, default=POOL_SIZE_DEFAULT, help="Экземпляров на семейство (по умолчанию 200)")
    p.add_argument(
        "--min-free", type=int, default=POOL_MIN_FREE, help="Долив, если свободных меньше (по умолчанию 50)"
    )
    p.add_argument("--threads", type=int, default=4, help="Потоков генерации")
    p.add_argument("--stats", action="store_true", help="Только показать состояние пула")
    p.add_argument(
        "--rotate-legacy",
        action="store_true",
        help="Удалить свободные экземпляры с предсказуемыми seed (пул до секретных seed) и долить пул заново",
    )
    return p.parse_args(argv)


def _print_stats(db, spec: str | None, family_ids: list[str] | None) -> None:
    fams = families_for(spec, family_ids)
    stats = pool_stats(db, [f.id for f in fams])
    low = legacy = 0
    for f in fams:
        st = stats.get(f.id, EMPTY_STATS)
        mark = ""
        if st.free < POOL_MIN_FREE:
            mark = "  <- мало свободных"
            low += 1
        if st.legacy_free:
            mark += f"  <- предсказуемых seed: {st.legacy_free}"
            legacy += 1
        print(f"{f.id:40s} всего {st.total:4d}  свободно {st.free:4d}{mark}")
    print(f"Семейств: {len(fams)}, с остатком меньше {POOL_MIN_FREE}: {low}, со старыми seed: {legacy}")
    if legacy:
        print("Старые seed восстанавливаются по коду варианта — замените: generate_pool.py --rotate-legacy")


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    db = get_sessionmaker()()
    try:
        if args.stats:
            _print_stats(db, args.spec, args.family)
            return 0
        created = seed_family_params(db, spec=args.spec, family_ids=args.family)
        db.commit()
        print(f"Параметры семейств: добавлено {created} строк (экспертные приоры)")
        if args.rotate_legacy:
            removed = rotate_legacy(db, spec=args.spec, family_ids=args.family)
            db.commit()
            print(f"Удалено свободных экземпляров с предсказуемыми seed: {removed}")
        started = time.monotonic()
        errors = 0
        generated = 0

        def progress(rep: PoolReport) -> None:
            nonlocal errors, generated
            db.commit()  # прогресс сохраняется по семействам: прерванный запуск продолжится
            generated += rep.generated
            if rep.error:
                errors += 1
                print(f"  x {rep.family_id}: {rep.error}")
            elif rep.generated:
                print(f"  + {rep.family_id}: {rep.generated} (было {rep.total_before}, свободно {rep.free_before})")

        ensure_pool(
            db,
            spec=args.spec,
            family_ids=args.family,
            n=args.n,
            min_free=args.min_free,
            threads=args.threads,
            progress=progress,
        )
        db.commit()
        print(f"Готово за {time.monotonic() - started:.1f} с: сгенерировано {generated}, ошибок {errors}")
        return 1 if errors else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
