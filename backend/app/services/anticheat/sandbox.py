"""«Попробуй списать»: песочница жюри (killer-features §2.1 п. 3). Только в DEMO_MODE (роутер отвечает 404 иначе).

Мини-тест из 4 заданий РАЗНЫХ семейств, у каждого есть двойник; набор уникален для проверяющего (случайное зерно,
выдаётся сервером). Проверяющий списывает как хочет — копирует в чат-бот, делает скриншот, решает в IDE. После ответов
видит диагноз по каждому заданию и акт сверки для пойманного. Сессия помечена demo_assisted и в метрики не входит;
ключи на клиент не уходят до отправки ответов (после отправки тест закрыт: ключи в акте — часть демонстрации).

Сборка параллельная и ограничена по времени (runtime.sandbox.build_budget_s): у части семейств двойник считается десятки
секунд, поэтому на каждое задание берётся несколько запасных семейств, побеждает первое справившееся. Двойник строится
через dispatch.family_twin_result: Family.twin() первым (QA, DevOps), затем общий мутатор.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import random
import secrets
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import ApiError, conflict, not_found
from app.services.anticheat import evidence as ev
from app.services.anticheat import store
from app.services.anticheat.cfg import RuntimeConfig, runtime_config
from app.services.anticheat.models import REVIEW_OPEN, AnticheatSandboxRun
from app.testing.anticheat.config import AntiCheatConfig, load_anticheat_config
from app.testing.anticheat.dispatch import family_twin_result
from app.testing.anticheat.evidence import AFTER, BEFORE, UNKNOWN, AnswerClass
from app.testing.anticheat.sandbox import (
    SandboxItem,
    SandboxResult,
    SandboxTest,
    sandbox_pool,
    score_sandbox,
)
from app.testing.bank.base import Family
from app.testing.bank.registry import all_families

log = logging.getLogger("vzaimno.anticheat.sandbox")

_STEPS_PER_FAMILY = 4  # сколько соседних сидов пробуем у семейства, прежде чем взять другое (как в ядре)


# ----------------------------------------------------------------------------- сборка


def _try_family(fam: Family, base_seed: int, cfg: AntiCheatConfig) -> tuple[Family, int, Any, Any] | None:
    for step in range(_STEPS_PER_FAMILY):
        seed = base_seed + step
        try:
            inst = fam.generate(seed)
            res = family_twin_result(fam, inst, cfg=cfg)
        except Exception:  # noqa: BLE001 — семейство с исключением на сиде: пробуем следующий
            continue
        if res.twin is not None:
            return fam, seed, inst, res.twin
    return None


_BANK_LOCK = threading.Lock()
_BANK: dict[str, SandboxItem] = {}  # «семейство:seed» → готовый экземпляр с двойником (общий запас на процесс)
_WARMING = False


def _bank_language(it: SandboxItem) -> str:
    return it.instance.rendered.language or ""


def bank_size() -> int:
    with _BANK_LOCK:
        return len(_BANK)


def _remember(r: tuple[Family, int, Any, Any]) -> SandboxItem:
    fam, seed, inst, twin = r
    it = SandboxItem(0, fam.id, seed, inst.variant_code, inst, twin)
    with _BANK_LOCK:
        _BANK[f"{fam.id}:{seed}"] = it
    return it


def warm_bank(rc: RuntimeConfig | None = None, cfg: AntiCheatConfig | None = None) -> int:
    """Заранее собрать запас заданий с двойниками (по `bank_per_language` на язык), чтобы мини-тест выдавался сразу.

    Двойник у части семейств считается десятки секунд, поэтому мини-тест собирается из запаса мгновенно, а в запас
    попадают только задания, у которых двойник удалось построить за бюджет. Запас общий на процесс; у проверяющих
    наборы различаются порядком, сочетанием заданий и случайным выбором из запаса. Возвращает размер запаса."""
    global _WARMING  # noqa: PLW0603 — один прогрев за раз
    rc, cfg = rc or runtime_config(), cfg or load_anticheat_config()
    with _BANK_LOCK:
        if _WARMING:
            return len(_BANK)
        _WARMING = True
    ex = ThreadPoolExecutor(max_workers=max(1, rc.sandbox_build_workers), thread_name_prefix="sandbox-warm")
    try:
        reg = all_families()
        pool = sandbox_pool(reg, cfg)
        rng = random.Random("sandbox-bank")
        futures: dict[Future[Any], str] = {}
        for lang, fids in pool.items():
            shuffled = list(fids)
            rng.shuffle(shuffled)
            for fid in shuffled[: rc.sandbox_bank_per_language * 3]:
                futures[ex.submit(_try_family, reg[fid], rng.randrange(10**6), cfg)] = lang
        have: dict[str, int] = {}
        pending = set(futures)
        deadline = time.monotonic() + rc.sandbox_bank_warm_budget_s
        while pending:
            timeout = deadline - time.monotonic()
            if timeout <= 0 or all(have.get(lang, 0) >= rc.sandbox_bank_per_language for lang in pool):
                break
            done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
            for f in done:
                r = f.result()
                lang = futures[f]
                if r is not None and have.get(lang, 0) < rc.sandbox_bank_per_language:
                    _remember(r)
                    have[lang] = have.get(lang, 0) + 1
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
        with _BANK_LOCK:
            _WARMING = False
    log.info("Песочница: запас заданий с двойниками — %d", bank_size())
    return bank_size()


def build_items(reviewer_seed: str, rc: RuntimeConfig, cfg: AntiCheatConfig) -> list[SandboxItem]:
    """4 экземпляра разных семейств с двойниками. Сначала берутся из запаса (мгновенно); недостающие строятся на месте:
    на каждое задание — запасные семейства того же языка, первое готовое побеждает, всё в рамках бюджета времени."""
    reg = all_families()
    pool = sandbox_pool(reg, cfg)
    n = cfg.sandbox.n_items
    order = [lang for lang in cfg.sandbox.language_order if pool.get(lang)] or sorted(pool)
    if not order:
        raise ApiError(503, "sandbox_unavailable", "Песочница недоступна: в банке нет заданий, пригодных для двойника")
    rng = random.Random(f"sandbox#{reviewer_seed}")
    used: set[str] = set()
    picked: dict[int, SandboxItem] = {}
    with _BANK_LOCK:
        banked = list(_BANK.values())
    for i in range(n):
        lang = order[i % len(order)]
        options = sorted(
            (b for b in banked if _bank_language(b) == lang and b.family_id not in used), key=lambda b: (b.family_id, b.seed)
        )
        if options:
            choice = options[rng.randrange(len(options))]
            picked[i] = choice
            used.add(choice.family_id)
    slots: dict[int, list[tuple[Family, int]]] = {}
    for i in range(n):
        if i in picked:
            continue
        lang = order[i % len(order)]
        cands = [f for f in pool[lang] if f not in used]
        if len(cands) < rc.sandbox_candidates_per_slot:
            cands = cands + [f for v in pool.values() for f in v if f not in used and f not in cands]
        rng.shuffle(cands)
        chosen = cands[: rc.sandbox_candidates_per_slot]
        used.update(chosen)
        slots[i] = [(reg[fid], rng.randrange(10**6)) for fid in chosen]

    if slots:
        ex = ThreadPoolExecutor(max_workers=max(1, rc.sandbox_build_workers), thread_name_prefix="sandbox")
        try:
            futures: dict[Future[Any], int] = {}
            for slot_no, cands in slots.items():
                for fam, base in cands:
                    futures[ex.submit(_try_family, fam, base, cfg)] = slot_no
            found: dict[int, SandboxItem] = {}
            pending = set(futures)
            deadline = time.monotonic() + rc.sandbox_build_budget_s
            while pending and len(found) < len(slots):
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                for f in done:
                    r = f.result()
                    slot_no = futures[f]
                    if r is not None:
                        built = _remember(r)  # и в запас: следующий проверяющий получит такое задание сразу
                        if slot_no not in found:
                            found[slot_no] = built
        finally:
            ex.shutdown(wait=False, cancel_futures=True)
        if len(found) < len(slots):
            log.warning("Песочница: собрано %d из %d заданий за бюджет времени", n - len(slots) + len(found), n)
            raise ApiError(503, "sandbox_busy", "Песочница ещё собирается — повторите через несколько секунд")
        picked.update(found)
    return [dataclasses.replace(picked[i], no=i + 1) for i in range(n)]


def _test_id(reviewer_seed: str, items: list[SandboxItem]) -> str:
    return hashlib.sha256("|".join([reviewer_seed, *(f"{i.family_id}:{i.seed}" for i in items)]).encode()).hexdigest()[:12]


def _item_json(it: SandboxItem) -> dict[str, Any]:
    return {
        "no": it.no,
        "family_id": it.family_id,
        "seed": it.seed,
        "variant_code": it.variant_code,
        "twin": store.twin_blob(it.twin),
    }


def _items_from_run(run: AnticheatSandboxRun) -> list[SandboxItem]:
    reg = all_families()
    out: list[SandboxItem] = []
    for j in run.items:
        fam = reg[str(j["family_id"])]
        inst = fam.generate(int(j["seed"]))
        twin = store.twin_from_blob(dict(j["twin"]))
        out.append(SandboxItem(int(j["no"]), fam.id, int(j["seed"]), inst.variant_code, inst, twin))
    return out


def _test_from_run(run: AnticheatSandboxRun, cfg: AntiCheatConfig) -> SandboxTest:
    return SandboxTest(
        test_id=run.test_id,
        reviewer_seed=run.reviewer_seed,
        items=tuple(_items_from_run(run)),
        banner_ru=cfg.text("sandbox.banner"),
        readme_note_ru=cfg.text("sandbox.readme_note"),
    )


# ----------------------------------------------------------------------------- старт


def start(db: Session) -> dict[str, Any]:
    """Новый мини-тест: случайное зерно проверяющего → набор из 4 заданий; на клиент — условия и clip, без ключей."""
    cfg, rc = load_anticheat_config(), runtime_config()
    _prune(db, rc)
    seed = secrets.token_hex(8)
    items = build_items(seed, rc, cfg)
    test_id = _test_id(seed, items)
    db.add(AnticheatSandboxRun(test_id=test_id, reviewer_seed=seed, items=[_item_json(i) for i in items], demo_assisted=True))
    db.flush()
    return client_payload(test_id, items, cfg)


def client_payload(test_id: str, items: list[SandboxItem], cfg: AntiCheatConfig) -> dict[str, Any]:
    """Мини-тест для страницы: условия и clip = {render_id, clipboard_text}. Ядровой SandboxTest.to_client() здесь не
    годится: его clip несёт карту подмены (какой именно литерал отличается), а клиенту её знать незачем."""
    out_items = []
    for it in items:
        pub = it.instance.rendered.to_public()
        out_items.append(
            {
                "no": it.no,
                "variant_code": it.variant_code,
                **{k: pub[k] for k in ("prompt", "code", "language", "table", "answer_mode", "answer_hint")},
                "clip": {"render_id": f"{test_id}:{it.no}", "clipboard_text": it.twin.clipboard_text},
            }
        )
    return {
        "test_id": test_id,
        "banner_ru": cfg.text("sandbox.banner"),
        "readme_note_ru": cfg.text("sandbox.readme_note"),
        "demo_assisted": True,
        "items": out_items,
    }


def _prune(db: Session, rc: RuntimeConfig) -> None:
    cutoff = clock.now() - timedelta(hours=rc.sandbox_retention_hours)
    db.execute(
        delete(AnticheatSandboxRun).where(AnticheatSandboxRun.created_at < cutoff, AnticheatSandboxRun.review_status == REVIEW_OPEN)
    )


def _run(db: Session, test_id: str, *, lock: bool = False) -> AnticheatSandboxRun:
    q = select(AnticheatSandboxRun).where(AnticheatSandboxRun.test_id == test_id)
    if lock:
        q = q.with_for_update()
    run = db.scalars(q).first()
    if run is None:
        raise not_found("sandbox_not_found", "Мини-тест не найден: начните заново")
    return run


# ----------------------------------------------------------------------------- ответы и результат


def _caught_order(result: SandboxResult) -> list[int]:
    return [d.no for d in result.items if d.caught]


def result_payload(run: AnticheatSandboxRun, cfg: AntiCheatConfig, rc: RuntimeConfig) -> dict[str, Any]:
    """Результат с актами (акты строятся заново по сохранённым двойникам: ключи и литерал — только на сервере)."""
    test = _test_from_run(run, cfg)
    result = score_sandbox(test, dict(run.answers or {}), cfg=cfg)
    copied = {int(x) for x in (run.copied or [])}
    by_no = {it.no: it for it in test.items}
    order = _caught_order(result)
    items: list[dict[str, Any]] = []
    for d in result.items:
        act = None
        if d.act is not None and d.answer_class is not None:
            it = by_no[d.no]
            first = bool(order) and order[0] == d.no
            act = ev.act_payload(
                it.twin,
                ident=f"{ev.SANDBOX_PREFIX}{run.test_id}-{d.no}",
                source="sandbox",
                label=rc.text("label_sandbox", no=d.no, variant=d.variant_code),
                answer=d.answer,
                ac=d.answer_class,
                phase=BEFORE if first or d.no not in order else AFTER,
                p_honest=it.twin.honest_collision_estimate,
                render_id=None,
                twin_render_id=None,
                self_paste=False,
                copy_at=None,
                answered_at=run.scored_at,
                warned_at=None,
                summary=None,
                first_hit=first if d.caught else None,
                review_status=run.review_status,
                review_note=run.review_note,
                reviewed_at=run.reviewed_at,
                session_id=None,
                demo_assisted=True,
            )
        items.append(
            {
                "no": d.no,
                "variant_code": d.variant_code,
                "answer": d.answer,
                "kind": d.answer_class.kind if d.answer_class else "blank",
                "matched": d.answer_class.matched if d.answer_class else "none",
                "channel": d.channel,
                "channel_ru": d.channel_ru,
                "caught": d.caught,
                "copied": d.no in copied,
                "diagnosis_ru": d.diagnosis_ru,
                "key_shown": d.key_shown,
                "key_clipboard": d.key_clipboard,
                "act": act,
            }
        )
    return {
        "test_id": run.test_id,
        "demo_assisted": True,
        "total": result.total,
        "caught": result.caught,
        "correct_uncaught": result.correct_uncaught,
        "summary_ru": result.summary_ru,
        "next_step_ru": rc.text("sandbox.next_step_ru"),
        "items": items,
    }


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Что храним в run.result: без актов (они строятся при чтении) — только то, что нужно ленте сверок."""
    return {**{k: v for k, v in payload.items() if k != "items"}, "items": [{**i, "act": None} for i in payload["items"]]}


def score(db: Session, test_id: str, answers: dict[str, str], copied: list[int]) -> dict[str, Any]:
    cfg, rc = load_anticheat_config(), runtime_config()
    run = _run(db, test_id, lock=True)
    if run.result is not None:
        raise conflict("sandbox_already_scored", "Ответы уже отправлены: откройте результат или начните новый мини-тест")
    nos = {int(j["no"]) for j in run.items}
    clean = {str(k): str(v)[: rc.sandbox_max_answer_chars] for k, v in answers.items() if k.isdigit() and int(k) in nos}
    run.answers = clean
    run.copied = sorted({int(c) for c in copied if c in nos})
    run.scored_at = clock.now()
    payload = result_payload(run, cfg, rc)
    run.result = _compact(payload)
    db.flush()
    return payload


def result(db: Session, test_id: str) -> dict[str, Any]:
    cfg, rc = load_anticheat_config(), runtime_config()
    run = _run(db, test_id)
    if run.result is None:
        raise conflict("sandbox_not_scored", "Ответы ещё не отправлены")
    return result_payload(run, cfg, rc)


# ----------------------------------------------------------------------------- лаборатория


_STRENGTH = {"twin_key": "exact", "twin_err": "err_twin", "twin_token": "token"}


def list_rows(db: Session, *, status: str | None, limit: int) -> list[dict[str, Any]]:
    cfg, rc = load_anticheat_config(), runtime_config()
    q = (
        select(AnticheatSandboxRun)
        .where(AnticheatSandboxRun.result.is_not(None))
        .order_by(AnticheatSandboxRun.scored_at.desc())
        .limit(limit)
    )
    if status:
        q = q.where(AnticheatSandboxRun.review_status == status)
    rows: list[dict[str, Any]] = []
    for run in db.scalars(q):
        by_no = {int(j["no"]): j for j in run.items}
        for it in (run.result or {}).get("items", []):
            if it.get("kind") != "twin_trace":
                continue
            matched = str(it["matched"])
            ac = AnswerClass("twin_trace", matched, _STRENGTH.get(matched))  # type: ignore[arg-type]
            src = by_no.get(int(it["no"]), {})
            family_id = str(src.get("family_id", ""))
            rows.append(
                {
                    "id": f"{ev.SANDBOX_PREFIX}{run.test_id}-{it['no']}",
                    "source": "sandbox",
                    "session_id": None,
                    "label": rc.text("label_sandbox", no=it["no"], variant=it["variant_code"]),
                    "seq": it["no"],
                    "variant_code": it["variant_code"],
                    "family_id": family_id,
                    "family_title": ev.family_title(family_id),
                    "mutation_class": str(src.get("twin", {}).get("data", {}).get("mutation_class", "")),
                    "answer": it["answer"],
                    "kind": "twin_trace",
                    "matched": matched,
                    "matched_ru": cfg.text(f"matched_ru.{matched}"),
                    "strength": ac.strength,
                    "headline_ru": ev.headline_for(ac, rc),
                    "phase": UNKNOWN,
                    "phase_ru": cfg.text(f"phase_ru.{UNKNOWN}"),
                    "session_state": "none",
                    "session_state_ru": rc.text("sandbox.state_ru"),
                    "review_status": run.review_status,
                    "review_status_ru": rc.text(f"review_status_ru.{run.review_status}"),
                    "answered_at": run.scored_at,
                    "demo_assisted": True,
                    "_sort": run.scored_at or run.created_at,
                }
            )
    return rows


def _split(ident: str) -> tuple[str, int]:
    body = ident.removeprefix(ev.SANDBOX_PREFIX)
    test_id, _, no = body.rpartition("-")
    if not test_id or not no.isdigit():
        raise not_found("reconciliation_not_found", "Сверка не найдена")
    return test_id, int(no)


def act_for(db: Session, ident: str) -> dict[str, Any]:
    test_id, no = _split(ident)
    run = _run(db, test_id)
    if run.result is None:
        raise not_found("reconciliation_not_found", "Сверка не найдена")
    payload = result_payload(run, load_anticheat_config(), runtime_config())
    for it in payload["items"]:
        if it["no"] == no and it["act"] is not None:
            return dict(it["act"])
    raise not_found("reconciliation_not_found", "Сверка не найдена")


def set_review(db: Session, ident: str, status: str, user_id: uuid.UUID, note: str | None, now: Any) -> None:
    run = _run(db, _split(ident)[0], lock=True)
    run.review_status = status
    run.review_note = note
    run.reviewed_by = user_id
    run.reviewed_at = now
    db.flush()
