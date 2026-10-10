"""Двойники экземпляров в БД: сериализация, предрасчёт пула, достройка на месте с бюджетом времени.

Почему предрасчёт: двойник — это запуски кода (ключи X′ и типичных ошибок K_err), у части семейств они занимают
секунды. Поэтому двойники считаются при подготовке пула (`python -m app.services.anticheat.precompute`), а при
выдаче задания берутся из таблицы. Если для выданного экземпляра двойника ещё нет, он достраивается на месте, но не
дольше runtime.issue.lazy_budget_s: не успели — задание выдаётся без подмены (пропуск, а не ложное срабатывание).

Ключи (ключ экрана, ключ копии, ответы на типичные ошибки по копии) лежат ТОЛЬКО в secret_enc (Fernet, как ключи
экземпляров). Клиенту уходит текст для буфера и render_id — больше ничего (spec-antiassist §7).
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.testing import ItemInstance
from app.services.anticheat.cfg import runtime_config
from app.services.anticheat.models import AnticheatTwin
from app.services.testing.keystore import decrypt_key, encrypt_key
from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.dispatch import family_twin_result, twin_applicable
from app.testing.anticheat.twins import ChangedLiteral, Twin, TwinResult
from app.testing.bank.base import Family, Instance, Rendered
from app.testing.bank.registry import all_families

log = logging.getLogger("vzaimno.anticheat.store")

STATUS_OK = "ok"
STATUS_NONE = "none"


# ----------------------------------------------------------------------------- идентификатор показа


def render_id_for(instance_id: uuid.UUID | str) -> str:
    """Идентификатор показа задания (spec §2.1, M6): экземпляр выдаётся один раз и рендерится один раз."""
    digest = hashlib.sha256(f"render|{instance_id}".encode()).hexdigest()[:10]
    return f"{runtime_config().render_id_prefix}-{digest}"


# ----------------------------------------------------------------------------- сериализация Twin


def twin_to_parts(twin: Twin) -> tuple[dict[str, Any], dict[str, Any]]:
    """(публичная часть для jsonb, секретная часть для шифрования). Ключей в публичной части нет."""
    data = {
        "family_id": twin.family_id,
        "seed": twin.seed,
        "variant_code": twin.variant_code,
        "mutation_class": twin.mutation_class,
        "role": twin.role,
        "tier": twin.tier,
        "shown_text": twin.shown_text,
        "clipboard_text": twin.clipboard_text,
        "changed_literal": twin.changed_literal.to_dict(),
        "key_hash": twin.key_hash,
        "twin_key_hash": twin.twin_key_hash,
        "honest_collision_estimate": twin.honest_collision_estimate,
        "gates": dict(twin.gates),
        "attempts": twin.attempts,
        "rejects": dict(twin.rejects),
        "executor": twin.executor,
        "version": twin.version,
        "k_err_size": twin.k_err_size,
    }
    secret = {"key": twin.key, "twin_key": twin.twin_key, "err_trace_keys": list(twin.err_trace_keys)}
    return data, secret


def _literal(d: dict[str, Any]) -> ChangedLiteral:
    return ChangedLiteral(
        line=int(d["line"]),
        col=int(d["col"]),
        from_=str(d["from"]),
        to=str(d["to"]),
        role=str(d["role"]),
        offset=int(d["offset"]),
        block=str(d["block"]),
        block_line=int(d["block_line"]),
        block_col=int(d["block_col"]),
        ident=int(d.get("ident", 0)),
        column=str(d.get("column", "")),
        row=int(d.get("row", -1)),
    )


def twin_from_parts(data: dict[str, Any], secret: dict[str, Any], rendered: Rendered | None = None) -> Twin:
    return Twin(
        family_id=str(data["family_id"]),
        seed=int(data["seed"]),
        variant_code=str(data["variant_code"]),
        mutation_class=str(data["mutation_class"]),
        role=str(data["role"]),
        tier=int(data["tier"]),
        shown_text=str(data["shown_text"]),
        clipboard_text=str(data["clipboard_text"]),
        changed_literal=_literal(data["changed_literal"]),
        key=str(secret["key"]),
        key_hash=str(data["key_hash"]),
        twin_key=str(secret["twin_key"]),
        twin_key_hash=str(data["twin_key_hash"]),
        honest_collision_estimate=float(data["honest_collision_estimate"]),
        twin_rendered=rendered if rendered is not None else Rendered(prompt=""),
        gates={str(k): bool(v) for k, v in dict(data.get("gates") or {}).items()},
        attempts=int(data.get("attempts", 0)),
        rejects={str(k): int(v) for k, v in dict(data.get("rejects") or {}).items()},
        executor=str(data.get("executor", "")),
        version=str(data["version"]),
        err_trace_keys=tuple(str(x) for x in secret.get("err_trace_keys") or ()),
        k_err_size=int(data.get("k_err_size", 0)),
    )


def twin_from_row(row: AnticheatTwin) -> Twin | None:
    """Twin по строке БД; None — для записи «двойника нет»."""
    if row.status != STATUS_OK or not row.secret_enc:
        return None
    return twin_from_parts(dict(row.data or {}), dict(decrypt_key(row.secret_enc)))


def twin_blob(twin: Twin) -> dict[str, Any]:
    """Самодостаточный JSON двойника для песочницы: публичная часть + шифрованная секретная."""
    data, secret = twin_to_parts(twin)
    return {"data": data, "secret_enc": encrypt_key(secret)}


def twin_from_blob(blob: dict[str, Any]) -> Twin:
    return twin_from_parts(dict(blob["data"]), dict(decrypt_key(str(blob["secret_enc"]))))


# ----------------------------------------------------------------------------- экземпляр из строки пула


def instance_from_row(row: ItemInstance) -> Instance:
    from app.services.testing.sessions import instance_of  # noqa: PLC0415 — sessions импортирует хуки

    return instance_of(row)


def family_of(row: ItemInstance) -> Family | None:
    return all_families().get(row.family_id)


def is_eligible(fam: Family, inst: Instance) -> bool:
    """Возможен ли двойник по форме: свой Family.twin() (QA, DevOps) либо predict_output / sql_result с исполняемым языком."""
    return twin_applicable(fam, inst, load_anticheat_config())


# ----------------------------------------------------------------------------- строки twin


def get_row(db: Session, instance_id: uuid.UUID) -> AnticheatTwin | None:
    return db.get(AnticheatTwin, instance_id)


def pool_hashes(db: Session, family_id: str, exclude: uuid.UUID | None = None) -> frozenset[str]:
    """Хеши ключей других экземпляров семейства: гейт 4 (ключ двойника не должен совпасть с ключом соседа)."""
    q = select(ItemInstance.key_hash).where(ItemInstance.family_id == family_id)
    if exclude is not None:
        q = q.where(ItemInstance.id != exclude)
    return frozenset(db.scalars(q))


def row_from_result(instance_id: uuid.UUID, family_id: str, res: TwinResult) -> AnticheatTwin:
    if res.twin is None:
        return AnticheatTwin(
            instance_id=instance_id,
            family_id=family_id,
            status=STATUS_NONE,
            reason=res.reason[:48],
            data={"attempts": res.attempts, "rejects": dict(res.rejects), "detail": res.detail[:300]},
        )
    data, secret = twin_to_parts(res.twin)
    return AnticheatTwin(
        instance_id=instance_id,
        family_id=family_id,
        status=STATUS_OK,
        twin_version=res.twin.version,
        twin_key_hash=res.twin.twin_key_hash,
        data=data,
        secret_enc=encrypt_key(secret),
        render_id=render_id_for(instance_id),
    )


def save_row(db: Session, row: AnticheatTwin) -> AnticheatTwin:
    """Вставка в SAVEPOINT: гонка двух запросов на один экземпляр не ломает транзакцию, второй берёт готовую строку."""
    try:
        with db.begin_nested():
            db.add(row)
        return row
    except IntegrityError:
        existing = db.get(AnticheatTwin, row.instance_id)
        if existing is None:  # pragma: no cover — строку могли удалить между попытками
            raise
        return existing


# ----------------------------------------------------------------------------- построение с бюджетом времени

_EXECUTOR: ThreadPoolExecutor | None = None
_LOCK = threading.Lock()
_INFLIGHT: dict[uuid.UUID, Future[TwinResult]] = {}
_DONE: dict[uuid.UUID, TwinResult] = {}
_DONE_CAP = 512


def _executor() -> ThreadPoolExecutor:
    global _EXECUTOR  # noqa: PLW0603 — один пул потоков на процесс
    with _LOCK:
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=runtime_config().lazy_workers, thread_name_prefix="twin")
        return _EXECUTOR


def _remember(instance_id: uuid.UUID, res: TwinResult) -> None:
    with _LOCK:
        if len(_DONE) >= _DONE_CAP:
            _DONE.pop(next(iter(_DONE)))
        _DONE[instance_id] = res
        _INFLIGHT.pop(instance_id, None)


def build_with_budget(
    fam: Family, instance_id: uuid.UUID, inst: Instance, pool: Iterable[str], budget_s: float
) -> TwinResult | None:
    """Построить двойник, ожидая не дольше budget_s. None — не успели (расчёт продолжается в потоке, результат
    останется в памяти процесса и пригодится следующему запросу)."""
    with _LOCK:
        done = _DONE.get(instance_id)
        fut = _INFLIGHT.get(instance_id)
    if done is not None:
        return done
    if fut is None:
        hashes = frozenset(pool)

        def work() -> TwinResult:
            res = family_twin_result(fam, inst, pool_key_hashes=hashes)  # Family.twin() первым (решение руководителя)
            _remember(instance_id, res)
            return res

        fut = _executor().submit(work)
        with _LOCK:
            _INFLIGHT[instance_id] = fut
    try:
        return fut.result(timeout=budget_s)
    except FutureTimeout:
        log.warning("Двойник %s: не успели за %.1f с — задание без подмены", fam.id, budget_s)
        return None
    except Exception:  # noqa: BLE001 — сбой исполнителя = нет двойника, тест продолжается
        log.exception("Двойник %s: ошибка построения", fam.id)
        with _LOCK:
            _INFLIGHT.pop(instance_id, None)
        return None


def ensure_twin_row(db: Session, inst_row: ItemInstance, *, budget_s: float | None = None) -> AnticheatTwin | None:
    """Строка двойника экземпляра: из таблицы, иначе достроить на месте (бюджет runtime.issue.lazy_budget_s; 0 —
    выключено). None — нет в таблице и не построено: не подходит по форме, не успели или достройка выключена."""
    row = get_row(db, inst_row.id)
    if row is not None:
        return row
    budget = runtime_config().lazy_budget_s if budget_s is None else budget_s
    if budget <= 0:
        return None  # достройка на месте выключена: двойники берутся только из предрасчёта пула
    fam = family_of(inst_row)
    if fam is None:
        return None
    inst = instance_from_row(inst_row)
    if not is_eligible(fam, inst):
        return None
    res = build_with_budget(fam, inst_row.id, inst, pool_hashes(db, inst_row.family_id, inst_row.id), budget)
    if res is None:
        return None
    return save_row(db, row_from_result(inst_row.id, inst_row.family_id, res))


def shown_count(db: Session, session_id: uuid.UUID) -> int:
    """Сколько двойников уже выдано клиенту в этой сессии (twin_rate_per_session)."""
    return int(
        db.scalar(select(func.count()).select_from(AnticheatTwin).where(AnticheatTwin.shown_in_session_id == session_id))
        or 0
    )


# ----------------------------------------------------------------------------- предрасчёт пула


@dataclass
class PrecomputeReport:
    family_id: str
    candidates: int = 0
    built: int = 0
    none: int = 0
    skipped_ineligible: int = 0
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "candidates": self.candidates,
            "built": self.built,
            "none": self.none,
            "skipped_ineligible": self.skipped_ineligible,
            "seconds": round(self.seconds, 2),
        }


def missing_instances(db: Session, family_id: str, limit: int) -> list[ItemInstance]:
    """Свободные (невыданные) экземпляры семейства без записи о двойнике; новые секретные seed — первыми."""
    q = (
        select(ItemInstance)
        .outerjoin(AnticheatTwin, AnticheatTwin.instance_id == ItemInstance.id)
        .where(
            ItemInstance.family_id == family_id,
            ItemInstance.issued_at.is_(None),
            AnticheatTwin.instance_id.is_(None),
        )
        .order_by(ItemInstance.seed.desc())
        .limit(limit)
    )
    return list(db.scalars(q))


def precompute_twins(
    db: Session,
    *,
    family_ids: Sequence[str] | None = None,
    per_family: int | None = None,
    workers: int | None = None,
    commit: Callable[[], None] | None = None,
    progress: Callable[[PrecomputeReport], None] | None = None,
) -> list[PrecomputeReport]:
    """Построить двойники свободных экземпляров (идемпотентно: уже посчитанные пропускаются).

    Исполнение — в потоках (запуск подпроцесса — десятки миллисекунд, потоки не конфликтуют), запись в БД — в
    вызывающем потоке. commit() вызывается пачками (runtime.precompute.batch_commit): прерванный предрасчёт
    не теряет сделанное."""
    rc = runtime_config()
    per_family = rc.precompute_per_family if per_family is None else per_family
    # Потоков — не меньше конфига и не больше ядер (≤ cap): исполнение двойника ждёт подпроцесс, GIL почти не мешает
    n_workers = workers or max(rc.precompute_workers, min(os.cpu_count() or 1, rc.precompute_workers_cap))
    pool = ThreadPoolExecutor(max_workers=max(1, n_workers), thread_name_prefix="twin-pre")
    reg = all_families()
    wanted = [f for f in sorted(reg) if family_ids is None or f in set(family_ids)]
    reports: list[PrecomputeReport] = []
    try:
        # Этап 1: задания ВСЕХ семейств уходят в пул сразу — тяжёлое семейство (algo.*) не держит остальные в очереди,
        # загрузка ядер ровная. Этап 2: результаты забираются в порядке семейств, запись и коммиты — в этом потоке.
        plan: list[tuple[str, PrecomputeReport, list[tuple[ItemInstance, Any]], float]] = []
        for fid in wanted:
            fam = reg[fid]
            t0 = time.monotonic()
            rep = PrecomputeReport(fid)
            rows = missing_instances(db, fid, per_family)
            rep.candidates = len(rows)
            futures: list[tuple[ItemInstance, Any]] = []
            jobs: list[tuple[ItemInstance, Instance]] = []
            for r in rows:
                inst = instance_from_row(r)
                if is_eligible(fam, inst):
                    jobs.append((r, inst))
                else:
                    rep.skipped_ineligible += 1
            if jobs:
                hashes = {r.id: pool_hashes(db, fid, r.id) for r, _ in jobs}
                futures = [(r, pool.submit(family_twin_result, fam, inst, pool_key_hashes=hashes[r.id])) for r, inst in jobs]
            plan.append((fid, rep, futures, t0))
        pending = 0
        for fid, rep, futures, t0 in plan:
            for r, fut in futures:
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001 — сбой одного экземпляра не останавливает предрасчёт
                    res = TwinResult(None, "executor_failed", detail=f"{type(e).__name__}: {e}")
                save_row(db, row_from_result(r.id, fid, res))
                rep.built += 1 if res.twin is not None else 0
                rep.none += 0 if res.twin is not None else 1
                pending += 1
                if commit is not None and pending >= rc.precompute_batch_commit:
                    db.flush()
                    commit()
                    pending = 0
            if futures:
                db.flush()
                if commit is not None:
                    commit()
                    pending = 0
            rep.seconds = time.monotonic() - t0
            reports.append(rep)
            if progress is not None:
                progress(rep)
    finally:
        pool.shutdown(wait=True)
    return reports
