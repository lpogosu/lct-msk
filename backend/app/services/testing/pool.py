"""Пул одноразовых экземпляров и экспертные приоры семейств (spec-testing §2.5, §3.2, §3.8, §4).

- generate_rows(): экземпляры семейства по seed → строки ItemInstance (rendered — только публичная часть,
  ключ зашифрован, key_hash для детектора «чужого ключа», теги дистракторов — только на сервере).
- pool_seed(): seed боевого экземпляра — СЕКРЕТНЫЙ: HMAC(VZ_APP_SECRET, семейство, номер) в [2^50, 2^62).
  Почему: код генераторов открыт (ТЗ, Керкгоффс), а код варианта = sha256(семейство:seed)[:4] виден на
  экране. При seed 0, 1, 2, … кандидат перебором находил seed по коду варианта и получал ключ, запустив
  открытый генератор у себя (проверено: доли секунды). Секрет — только seed; всё остальное открыто.
- ensure_pool(): идемпотентно догоняет пул до N экземпляров на семейство и доливает, когда свободных < 50.
  Генерация идёт потоками (банк потокобезопасен: перехват вывода без redirect_stdout, свой цикл asyncio),
  запись в БД — в вызывающем потоке.
- rotate_legacy(): удалить невыданные экземпляры со старыми предсказуемыми seed (0 … 2^50) — потом
  ensure_pool дольёт пул секретными seed (scripts/generate_pool.py --rotate-legacy).
- seed_family_params(): ItemFamilyParams с экспертным приором b = cut(g) + 0.35·pos, σ_b по конфигу,
  по строке на каждую шкалу (общие модули — своя b на шкале каждой специализации). Уже уточнённые
  калибровкой параметры не перезаписываются.
- issue_instance(): следующий невыданный экземпляр семейства, SELECT … FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.enums import FamilyStatus
from app.models.testing import ItemFamilyParams, ItemInstance
from app.services.testing.keystore import encrypt_key
from app.services.testing.limits import POOL_MIN_FREE, POOL_SIZE_DEFAULT
from app.testing.bank.base import Family, Instance
from app.testing.bank.registry import all_families
from app.testing.irt import Scale, b_from_expert, load_scale

log = logging.getLogger("vzaimno.testing.pool")

# Боевые seed — в [2^50, 2^62): выше превью лаборатории (limits.PREVIEW_SEED_BASE ≈ 10^9) и выше модуля
# seed задач типа A (tasks: −(2^40 + h), h < 2^48 — не совпадают даже по модулю), влезают в BIGINT.
# Всё, что в [0, 2^50), — старые предсказуемые seed (пул до секретных seed), их чинит rotate_legacy().
SEED_FLOOR = 1 << 50
SEED_CEIL = 1 << 62
_SEED_LABEL = b"vzaimno/item-pool-seed/v1"
_TOP_UP_LOCK = threading.Lock()
_TOPPING_UP: set[str] = set()  # семейства, которые сейчас доливает фоновая задача этого процесса


def _seed_secret() -> bytes:
    return get_settings().app_secret.get_secret_value().encode("utf-8")


def pool_seed(family_id: str, index: int, secret: bytes | None = None) -> int:
    """Секретный seed index-го экземпляра семейства: детерминированно при известном секрете, но без
    секрета не угадывается и не перебирается (2^62 значений) — ни по коду варианта, ни по тексту."""
    key = secret if secret is not None else _seed_secret()
    mac = hmac.new(key, _SEED_LABEL + b"|" + f"{family_id}|{index}".encode(), hashlib.sha256).digest()
    return SEED_FLOOR + int.from_bytes(mac[:8], "big") % (SEED_CEIL - SEED_FLOOR)


def pool_seeds(family_id: str, indices: Iterable[int]) -> list[int]:
    secret = _seed_secret()
    return [pool_seed(family_id, i, secret) for i in indices]


def families_for(spec: str | None = None, family_ids: Iterable[str] | None = None) -> list[Family]:
    """Семейства банка (по специализации и/или списку id), в детерминированном порядке."""
    fams = all_families()
    wanted = set(family_ids) if family_ids is not None else None
    out = [
        f
        for fid, f in sorted(fams.items())
        if (spec is None or spec in f.specs) and (wanted is None or fid in wanted)
    ]
    return out


def instance_row(inst: Instance) -> dict[str, Any]:
    """Экземпляр → строка item_instances. В rendered только то, что видит кандидат (Rendered.to_public)."""
    return {
        "id": uuid.uuid4(),
        "family_id": inst.family_id,
        "seed": inst.seed,
        "rendered": inst.rendered.to_public(),
        "key_enc": encrypt_key(inst.key),
        "key_hash": inst.key_hash,
        "distractors": dict(inst.distractors),
        "explanation": inst.explanation or None,
        "variant_code": inst.variant_code,
    }


def generate_rows(fam: Family, seeds: Sequence[int]) -> list[dict[str, Any]]:
    return [instance_row(fam.generate(seed)) for seed in seeds]


@dataclass(frozen=True)
class PoolReport:
    family_id: str
    total_before: int
    free_before: int
    generated: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "total_before": self.total_before,
            "free_before": self.free_before,
            "generated": self.generated,
            "error": self.error,
        }


@dataclass(frozen=True)
class FamilyPoolStats:
    total: int  # все экземпляры семейства (в том числе выданные и экземпляры задач типа A)
    free: int  # невыданные
    secret: int  # с секретным seed (боевой пул): их число — следующий номер для pool_seed()
    legacy_free: int  # невыданные с предсказуемым seed (0 … 2^50) — подлежат замене


EMPTY_STATS = FamilyPoolStats(0, 0, 0, 0)


def pool_stats(db: Session, family_ids: Iterable[str] | None = None) -> dict[str, FamilyPoolStats]:
    """family_id → (всего, свободных, с секретным seed, свободных с предсказуемым seed)."""
    unissued = ItemInstance.issued_at.is_(None)
    secret = ItemInstance.seed >= SEED_FLOOR
    legacy = ItemInstance.seed.between(0, SEED_FLOOR - 1)
    q = select(
        ItemInstance.family_id,
        func.count(),
        func.count().filter(unissued),
        func.count().filter(secret),
        func.count().filter(unissued, legacy),
    ).group_by(ItemInstance.family_id)
    if family_ids is not None:
        q = q.where(ItemInstance.family_id.in_(list(family_ids)))
    return {fid: FamilyPoolStats(int(t), int(f), int(s), int(lg)) for fid, t, f, s, lg in db.execute(q)}


@dataclass(frozen=True)
class PoolReadiness:
    ready: bool
    families_total: int
    families_ready: int
    progress: float  # 0…1: доля семейств с достаточным запасом
    eta_s: int  # ориентир ожидания, 0 — готово
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "families_total": self.families_total,
            "families_ready": self.families_ready,
            "progress": self.progress,
            "eta_s": self.eta_s,
            "message": self.message,
        }


def readiness(db: Session, spec: str | None = None) -> PoolReadiness:
    """Готов ли пул одноразовых заданий (#14): после `docker compose up` его ≈ 2 минуты генерирует сервис pool.
    Пока не готов — тест не стартует, а экран говорит «готовим задания», а не падает ошибкой."""
    from app.services.testing.limits import POOL_READY_MIN_FREE, POOL_READY_SHARE, POOL_WARMUP_ETA_S

    fams = [f.id for f in families_for(spec)]
    stats = pool_stats(db, fams) if fams else {}
    ready_n = sum(1 for fid in fams if stats.get(fid, EMPTY_STATS).free >= POOL_READY_MIN_FREE)
    total = len(fams)
    share = ready_n / total if total else 1.0
    ok = share >= POOL_READY_SHARE
    return PoolReadiness(
        ready=ok,
        families_total=total,
        families_ready=ready_n,
        progress=round(share, 3),
        eta_s=0 if ok else max(5, round(POOL_WARMUP_ETA_S * (1 - share))),
        message="Пул заданий готов" if ok else "Готовим одноразовые задания… около 2 минут. Страница обновится сама",
    )


def plan_indices(total: int, free: int, next_index: int, n: int, min_free: int) -> list[int]:
    """Какие номера экземпляров догенерировать: пул до n экземпляров (прерванную генерацию продолжаем
    со следующего номера), затем долив до n свободных, когда свободных меньше min_free.

    Номера секретных seed идут подряд 0, 1, 2, …: вставка семейства атомарна, а параллельный долив
    вычисляет те же номера → те же seed → ON CONFLICT DO NOTHING, дыр не бывает."""
    if total < n:
        k = n - total
    elif free < min_free:
        k = n - free
    else:
        k = 0
    return list(range(next_index, next_index + k))


def ensure_pool(
    db: Session,
    *,
    spec: str | None = None,
    family_ids: Iterable[str] | None = None,
    n: int = POOL_SIZE_DEFAULT,
    min_free: int = POOL_MIN_FREE,
    threads: int = 4,
    progress: Callable[[PoolReport], None] | None = None,
) -> list[PoolReport]:
    """Довести пул каждого семейства до n экземпляров; долить, если свободных меньше min_free.

    Повторный запуск ничего не меняет (seed уникален в семействе: UNIQUE(family_id, seed)).
    Ошибка генерации одного семейства не роняет остальные — она попадает в отчёт.
    """
    fams = families_for(spec, family_ids)
    stats = pool_stats(db, [f.id for f in fams])
    jobs: list[tuple[Family, list[int], FamilyPoolStats]] = []
    for f in fams:
        st = stats.get(f.id, EMPTY_STATS)
        seeds = pool_seeds(f.id, plan_indices(st.total, st.free, st.secret, n, min(min_free, n)))
        if seeds:
            log.info("Семейство %s: догенерация %d экземпляров (всего %d, свободно %d)", f.id, len(seeds), st.total, st.free)
        jobs.append((f, seeds, st))

    def run(job: tuple[Family, list[int], FamilyPoolStats]) -> tuple[str, list[dict[str, Any]], str | None]:
        fam, seeds, _ = job
        if not seeds:
            return fam.id, [], None
        try:
            return fam.id, generate_rows(fam, seeds), None
        except Exception as e:  # noqa: BLE001 — отчёт по семейству, остальные продолжают
            return fam.id, [], f"{type(e).__name__}: {e}"

    reports: list[PoolReport] = []
    with ThreadPoolExecutor(max_workers=max(1, threads)) as ex:
        for (_fam, _, st), (fid, rows, err) in zip(jobs, ex.map(run, jobs), strict=True):
            if rows:
                stmt = pg_insert(ItemInstance).values(rows).on_conflict_do_nothing(
                    index_elements=["family_id", "seed"]
                )
                db.execute(stmt)
                db.flush()
            rep = PoolReport(fid, st.total, st.free, len(rows), err)
            if err:
                log.warning("Пул семейства %s не сгенерирован: %s", fid, err)
            reports.append(rep)
            if progress is not None:
                progress(rep)
    return reports


def seed_family_params(
    db: Session,
    *,
    spec: str | None = None,
    family_ids: Iterable[str] | None = None,
    scale: Scale | None = None,
) -> int:
    """Экспертные приоры по каждой шкале (§3.2). Существующие строки не трогаем. Возвращает число новых."""
    scale = scale or load_scale()
    fams = families_for(spec, family_ids)
    existing = {
        (fid, s)
        for fid, s in db.execute(select(ItemFamilyParams.family_id, ItemFamilyParams.specialization))
    }
    created = 0
    for f in fams:
        for s in f.specs:
            if spec is not None and s != spec:
                continue
            if (f.id, s) in existing:
                continue
            db.add(
                ItemFamilyParams(
                    family_id=f.id,
                    specialization=s,
                    module=f.module,
                    b=round(b_from_expert(f.target_grade, f.position, scale), 4),
                    sigma_b=scale.sigma_b_agree,
                    is_anchor=bool(getattr(f, "is_anchor", False)),
                    status=FamilyStatus.operational.value,  # §2.4: на старте OPERATIONAL(prior)
                    source="prior",
                    n_responses=0,
                    cusum_s=0.0,
                    exposure_30d=0.0,
                    params_version=1,
                )
            )
            created += 1
    db.flush()
    return created


def rotate_legacy(db: Session, *, spec: str | None = None, family_ids: Iterable[str] | None = None) -> int:
    """Удалить НЕвыданные экземпляры с предсказуемым seed (0 … 2^50, пул до секретных seed). Выданные
    остаются (на них ссылаются ответы и разбор); экземпляры задач типа A (seed < 0) не трогаются.
    После этого ensure_pool() дольёт пул секретными seed. Возвращает число удалённых строк."""
    ids = [f.id for f in families_for(spec, family_ids)]
    if not ids:
        return 0
    res = db.execute(
        delete(ItemInstance).where(
            ItemInstance.family_id.in_(ids),
            ItemInstance.issued_at.is_(None),
            ItemInstance.seed.between(0, SEED_FLOOR - 1),
        )
    )
    db.flush()
    return int(res.rowcount or 0)


def needs_top_up(db: Session, family_id: str, min_free: int = POOL_MIN_FREE) -> bool:
    free = db.scalar(
        select(func.count()).where(ItemInstance.family_id == family_id, ItemInstance.issued_at.is_(None))
    )
    return int(free or 0) < min_free


def top_up_background(bind: Any, family_ids: Sequence[str]) -> None:
    """Догенерация пула после ответа (BackgroundTasks): своя сессия на том же движке.

    Если сессия запроса привязана не к Engine, а к соединению (тесты: транзакция с откатом), ничего не
    делаем — из другого потока это соединение трогать нельзя; пул тогда доливает scripts/generate_pool.py.
    Параллельный долив того же семейства безопасен: seed уникален, вставка ON CONFLICT DO NOTHING.
    """
    if not isinstance(bind, Engine) or not family_ids:
        return
    # Пока семейство доливается, новые GET next того же семейства не запускают ещё одну генерацию
    # (в пределах процесса; между процессами дубли гасит ON CONFLICT DO NOTHING)
    with _TOP_UP_LOCK:
        mine = [f for f in family_ids if f not in _TOPPING_UP]
        _TOPPING_UP.update(mine)
    if not mine:
        return
    try:
        with Session(bind=bind) as s:
            ensure_pool(s, family_ids=mine, threads=1)
            s.commit()
    except Exception:  # noqa: BLE001 — фоновая задача не должна ронять процесс; пул дольёт скрипт
        log.exception("Фоновая догенерация пула не удалась: %s", mine)
    finally:
        with _TOP_UP_LOCK:
            _TOPPING_UP.difference_update(mine)


def issue_instance(
    db: Session,
    family_id: str,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime,
) -> ItemInstance | None:
    """Следующий невыданный экземпляр семейства. Параллельные сессии не получат один и тот же экземпляр:
    строку блокирует FOR UPDATE, занятые соседом строки пропускаются (SKIP LOCKED).

    Порядок — по убыванию seed (обратный проход по индексу): секретные seed (≥ 2^50) выдаются раньше
    старых предсказуемых, которые остаются только запасом до rotate_legacy()."""
    inst = db.scalars(
        select(ItemInstance)
        .where(ItemInstance.family_id == family_id, ItemInstance.issued_at.is_(None))
        .order_by(ItemInstance.seed.desc())
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if inst is None:
        return None
    if 0 <= int(inst.seed) < SEED_FLOOR:
        log.warning(
            "Семейство %s: выдан экземпляр с предсказуемым seed — запустите scripts/generate_pool.py --rotate-legacy",
            family_id,
        )
    inst.issued_at = now
    inst.issued_to_session_id = session_id
    inst.issued_to_user_id = user_id
    db.flush()
    return inst
