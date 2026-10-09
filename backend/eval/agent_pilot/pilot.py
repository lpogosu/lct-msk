"""CLI пилота с ИИ-испытуемыми: агент-персона проходит НАСТОЯЩИЙ адаптивный тест через настоящий HTTP API.

Запуск из каталога backend/ (агенты-персоны вызывают его через Bash):

    uv run python -m eval.agent_pilot.pilot persona --persona P01
    uv run python -m eval.agent_pilot.pilot start   --persona P01 [--attempt 1|2]
    uv run python -m eval.agent_pilot.pilot answer  --persona P01 [--attempt N] --answer '...'
    uv run python -m eval.agent_pilot.pilot copy    --persona C01 [--attempt N]      # только «списывающие»
    uv run python -m eval.agent_pilot.pilot result  --persona P01 [--attempt N]
    uv run python -m eval.agent_pilot.pilot status | reset | prepare-config           # для оркестратора

Что видит персона — ровно то, что показывает экран: формулировка, код, варианты, формат ответа, лимит времени.
Ключей, верных ответов, сложности, модуля и тегов заблуждений в выводе нет; результат — как на экране К5.

Повторный тест (attempt 2) — ВТОРОЙ аккаунт той же персоны (pilot-<persona>-a2): подтверждённый грейд правила пересдачи
не допускают (qualification.start_test: только SURVEY_DONE | NOT_CONFIRMED), поэтому сдвиг часов не помог бы. Второй
аккаунт получает другие одноразовые клоны сам. Сессии помечаются demo_assisted = true прямой записью в БД пилота — так же,
как экспресс и «режим жюри», они выпадают из калибровки, нормы семейств, экспозиции и метрик (catalog.py, lab.py).

Пароли пилотных аккаунтов — в backend/var/pilot_accounts.json (в .gitignore), в вывод не попадают.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import secrets
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND))
VAR = BACKEND / "var"
ACCOUNTS = VAR / "pilot_accounts.json"
STATE_DIR = VAR / "pilot_state"
RUNS_DIR = VAR / "pilot_runs"
PILOT_CONFIG = VAR / "pilot_config"

API = os.environ.get("PILOT_API", "http://127.0.0.1:8133/api").rstrip("/")
DB_URL = os.environ.get("PILOT_DB_URL", "postgresql+psycopg://vzaimno:vzaimno@localhost:5432/vzaimno_pilot")
EMAIL_DOMAIN = "demo.example.com"


class PilotError(Exception):
    pass


def out(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n")


def fail(msg: str, **extra: Any) -> None:
    out({"status": "error", "message": msg, **extra})
    raise SystemExit(2)


# --------------------------------------------------------------------------- состояние на диске


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


@contextlib.contextmanager
def _accounts_lock(timeout_s: float = 30.0):
    """Персоны идут параллельно: read-modify-write файла аккаунтов без блокировки терял записи (пилот, P12)."""
    lock = ACCOUNTS.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                lock.unlink(missing_ok=True)  # осиротевшая блокировка упавшего процесса
                continue
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def email_of(pid: str, attempt: int) -> str:
    return f"pilot-{pid.lower()}{'' if attempt == 1 else f'-a{attempt}'}@{EMAIL_DOMAIN}"


def state_path(pid: str, attempt: int) -> Path:
    return STATE_DIR / f"{pid}_a{attempt}.json"


def run_path(pid: str, attempt: int) -> Path:
    return RUNS_DIR / f"{pid}_a{attempt}.json"


def load_state(pid: str, attempt: int | None) -> dict[str, Any]:
    if attempt is None:  # последняя начатая попытка
        found = sorted(STATE_DIR.glob(f"{pid}_a*.json"))
        if not found:
            fail("Тест не начат: сначала команда start")
        return _read(found[-1], {})
    st = _read(state_path(pid, attempt), None)
    if st is None:
        fail("Тест не начат: сначала команда start", persona=pid, attempt=attempt)
    return st


def save_state(st: dict[str, Any]) -> None:
    _write(state_path(st["persona"], st["attempt"]), st)


# --------------------------------------------------------------------------- HTTP


class Api:
    def __init__(self) -> None:
        import httpx

        self.c = httpx.Client(base_url=API, timeout=120)
        self.token: str | None = None
        self.creds: tuple[str, str] | None = None  # для одного повторного входа, когда access-токен истёк

    def call(self, method: str, path: str, ok: tuple[int, ...] = (200, 201), **kw: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            r = self.c.request(method, path, headers=headers, **kw)
            if r.status_code == 401 and self.creds and not path.startswith("/auth/"):
                self.login(*self.creds)
                r = self.c.request(method, path, headers={"Authorization": f"Bearer {self.token}"}, **kw)
        except PilotError:
            raise
        except Exception as e:  # noqa: BLE001
            raise PilotError(f"Сервер недоступен ({API}): {type(e).__name__}. Запущен ли бэкенд пилота?") from e
        if r.status_code in ok:
            return r.json() if r.content else None
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        err = body.get("error", body) if isinstance(body, dict) else body
        raise PilotError(f"{method} {path} -> {r.status_code}: {json.dumps(err, ensure_ascii=False)[:400]}")

    def login(self, email: str, password: str) -> None:
        self.token = self.call("POST", "/auth/login", json={"email": email, "password": password})["access_token"]
        self.creds = (email, password)


def ensure_account(api: Api, email: str) -> None:
    """Вход в пилотный аккаунт; нет аккаунта — регистрация (DEMO_MODE: почта подтверждается сама)."""
    accounts = _read(ACCOUNTS, {})
    if email in accounts:
        api.login(email, accounts[email])
        return
    password = "Pl" + secrets.token_urlsafe(12).replace("_", "x").replace("-", "y") + "7"
    docs = api.call("GET", "/consents/documents")
    version = next(d["version"] for d in docs if d["purpose"] == "processing")
    api.call(
        "POST", "/auth/register", ok=(201,),
        json={"email": email, "password": password, "role": "candidate", "consent_personal_data": True,
              "consent_text_version": version, "birth_year": 1995},
    )
    with _accounts_lock():
        accounts = _read(ACCOUNTS, {})
        accounts[email] = password
        _write(ACCOUNTS, accounts)
    api.login(email, password)


def relogin(api: Api, st: dict[str, Any]) -> None:
    """Токен из состояния (логин ограничен 5 раз за 5 минут на email); истёк — один повторный вход внутри Api.call."""
    api.token = st.get("token")
    api.creds = (st["email"], _read(ACCOUNTS, {})[st["email"]])
    if api.token is None:
        api.login(*api.creds)
        st["token"] = api.token
        save_state(st)


# --------------------------------------------------------------------------- БД пилота (метка, аудит, сброс)


def db_session():  # type: ignore[no-untyped-def]
    if "pilot" not in DB_URL.rsplit("/", 1)[-1]:
        raise PilotError("PILOT_DB_URL должен указывать на отдельную БД пилота (в имени есть «pilot»): боевую БД не трогаем")
    os.environ["VZ_DATABASE_URL"] = DB_URL
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    return Session(create_engine(DB_URL))


def tag_session(session_id: str) -> bool:
    from sqlalchemy import update

    from app.models.testing import TestSession

    with db_session() as db:
        db.execute(update(TestSession).where(TestSession.id == session_id).values(demo_assisted=True))
        db.commit()
    return True


def audit(session_id: str) -> dict[str, Any]:
    """Служебные данные для анализа (персоне НЕ показываются): верность ответов, модули, флаги, улики антиподсказчика."""
    from sqlalchemy import select

    from app.models.testing import Flag, TestResponse, TestSession
    from app.services.anticheat.models import AnticheatEvidence

    with db_session() as db:
        s = db.get(TestSession, session_id)
        resp = db.scalars(select(TestResponse).where(TestResponse.session_id == session_id).order_by(TestResponse.seq)).all()
        flags = db.scalars(select(Flag).where(Flag.session_id == session_id)).all()
        ev = db.scalars(select(AnticheatEvidence).where(AnticheatEvidence.session_id == session_id).order_by(AnticheatEvidence.seq)).all()
        return {
            "session": {
                "status": s.status, "decision": s.decision, "kind": s.kind, "n_items": s.n_items, "theta": s.theta, "se": s.se,
                "p_pass": s.p_pass, "trust": s.trust, "demo_assisted": s.demo_assisted,
            },
            "responses": [
                {"seq": r.seq, "family_id": r.family_id, "module": r.module, "is_pretest": r.is_pretest,
                 "is_correct": r.is_correct, "rt_ms": r.rt_ms, "distractor_tag": r.distractor_tag}
                for r in resp
            ],
            "flags": [{"kind": f.kind, "severity": f.severity, "value": f.value, "status": f.status} for f in flags],
            "evidence": [
                {"seq": e.seq, "kind": e.kind, "matched": e.matched, "strength": e.strength, "honest_p": e.honest_p}
                for e in ev
            ],
        }


def drop_accounts(emails: list[str]) -> int:
    from sqlalchemy import delete

    from app.models.users import User

    with db_session() as db:
        res = db.execute(delete(User).where(User.email.in_(emails)))
        db.commit()
        return int(res.rowcount or 0)


# --------------------------------------------------------------------------- экран задания


def shown_item(item: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    """Ровно то, что видит кандидат (ItemOut без clip и без служебных дедлайнов)."""
    keys = ("seq", "variant_code", "prompt", "code", "language", "options", "lines", "table", "answer_mode", "answer_hint",
            "answer_format", "time_limit_s")
    view = {k: item[k] for k in keys if item.get(k) not in (None, [], "")}
    view["progress"] = session["progress"]["text"]
    return view


def compose_shown(item: dict[str, Any]) -> str:
    """Что попадёт в буфер, если защита от подсказчика для задания не включена: обычный текст экрана."""
    parts = [item.get("prompt") or ""]
    if item.get("code"):
        parts.append(item["code"])
    if item.get("table"):
        parts.append(json.dumps(item["table"], ensure_ascii=False))
    return "\n\n".join(parts)


def fetch_next(api: Api, st: dict[str, Any]) -> dict[str, Any]:
    nxt = api.call("GET", f"/tests/sessions/{st['session_id']}/next")
    item = nxt.get("item")
    if item is None:
        return finalize(api, st)
    cur = {
        "seq": item["seq"], "variant_code": item["variant_code"], "answer_mode": item["answer_mode"],
        "option_ids": [o["id"] for o in item.get("options") or []], "time_limit_s": item["time_limit_s"],
        "shown_mono": time.monotonic(), "shown_at": datetime.now(UTC).isoformat(),
        "clip": item.get("clip"), "copied": False, "shown_text": compose_shown(item),
    }
    prev = st.get("current")
    if prev and prev["seq"] == cur["seq"]:  # повторный показ того же задания — время идёт с первого показа
        cur["shown_at"], cur["copied"] = prev["shown_at"], prev.get("copied", False)
        cur["shown_wall"] = prev.get("shown_wall", time.time())
    else:
        cur["shown_wall"] = time.time()
    cur.pop("shown_mono")
    st["current"] = cur
    save_state(st)
    return {"status": "in_progress", "persona": st["persona"], "attempt": st["attempt"], "item": shown_item(item, nxt["session"])}


def finalize(api: Api, st: dict[str, Any]) -> dict[str, Any]:
    res = api.call("GET", f"/tests/sessions/{st['session_id']}/result", ok=(200, 409))
    ui = result_view(res) if isinstance(res, dict) and "session" in res else {"message": "Результат ещё не готов"}
    run = {
        "persona": st["persona"], "attempt": st["attempt"], "email": st["email"], "spec": st["spec"],
        "claimed_grade": st["grade"], "intended": st["intended"], "kind": st["kind"], "session_id": st["session_id"],
        "started_at": st["started_at"], "finished_at": datetime.now(UTC).isoformat(),
        "items": st.get("log", []), "result": ui, "audit": audit(st["session_id"]),
    }
    _write(run_path(st["persona"], st["attempt"]), run)
    st["finished"] = True
    st["current"] = None
    save_state(st)
    return {"status": "finished", "persona": st["persona"], "attempt": st["attempt"],
            "message": "Тест завершён. Результат — командой result.", "n_answered": len(st.get("log", []))}


def result_view(res: dict[str, Any]) -> dict[str, Any]:
    """Экран К5 как его видит кандидат (без слепой карточки и согласий)."""
    keys = ("visible", "message", "headline", "decision", "stop_text", "confirmed_grade", "category", "score", "interval80",
            "confidence_pct", "certainty", "by_level_text", "n_shown", "offers", "review_available")
    v = {k: res.get(k) for k in keys}
    v["status"] = res["session"]["status_ru"]
    v["ruler"] = [{"grade": c["grade"], "name": c["name"], "score": c["score"]} for c in (res.get("ruler") or {}).get("cuts", [])]
    v["modules"] = [{"name": m["name"], "symbol": m["symbol"], "tag_ru": m["tag_ru"]} for m in res.get("modules") or []]
    return v


# --------------------------------------------------------------------------- команды


def get_persona(pid: str):  # type: ignore[no-untyped-def]
    from eval.agent_pilot.personas import PERSONAS

    p = PERSONAS.get(pid.upper())
    if p is None:
        fail(f"Неизвестная персона {pid}", known=sorted(PERSONAS))
    return p


def cmd_persona(a: argparse.Namespace) -> None:
    from dataclasses import asdict

    p = get_persona(a.persona)
    d = asdict(p)
    for k in ("actions", "techs"):
        d.pop(k)
    d["правила"] = [
        "Отвечай так, как ответил бы этот человек: в уме, без запуска кода, без поиска в интернете, без калькулятора.",
        "На задание — не больше одной-двух минут «размышления»; не знаешь — выбери вариант, как выбрал бы он, или пропусти (--skip).",
        "Не ищи верный ответ ради верного ответа: играешь человека с этим профилем, а не максимизируешь результат.",
        "Не читай исходники проекта, не ищи ключи и не обращайся к базе данных; пользуйся только командами pilot.py.",
    ]
    out(d)


def cmd_start(a: argparse.Namespace) -> None:
    pid = a.persona.upper()
    p = get_persona(pid)
    spec, grade = a.spec or p.spec, a.grade or p.grade
    attempt = int(a.attempt or 1)
    prior = _read(state_path(pid, attempt), None)
    if prior and not prior.get("finished"):
        fail("Попытка уже идёт: продолжайте командой answer", persona=pid, attempt=attempt)
    if prior and prior.get("finished"):
        fail("Эта попытка уже завершена; для повторного прогона оркестратор вызывает reset", persona=pid, attempt=attempt)
    api = Api()
    email = email_of(pid, attempt)
    ensure_account(api, email)
    token = api.token

    sv = api.call("GET", "/survey/current")
    if not sv["completed"]:
        api.call("PUT", "/survey/steps/industries", json={"industries": []})
        api.call("PUT", "/survey/steps/actions", json={"actions": p.actions})
        api.call("PUT", "/survey/steps/stack", json={"technologies": p.techs, "experience_months": p.exp_months, "roles": [], "self_assessment": {}})
        api.call("PUT", "/survey/steps/specialization", json={"chosen_spec": spec})
    brief = api.call("GET", "/tests/briefing", params={"specialization": spec})
    if not brief["can_start"]["ok"]:
        fail("Сейчас тест начать нельзя", reason=brief["can_start"])
    sess = api.call("POST", "/tests/sessions", ok=(201,), json={"specialization": spec, "grade": grade, "consent_test_logging": True})
    tagged = tag_session(sess["id"])
    st = {
        "persona": pid, "attempt": attempt, "email": email, "spec": spec, "grade": grade, "intended": p.intended, "kind": p.kind,
        "session_id": sess["id"], "started_at": datetime.now(UTC).isoformat(), "log": [], "current": None, "finished": False,
        "tagged_demo_assisted": tagged, "token": token,
    }
    save_state(st)
    first = fetch_next(api, st)
    first["note"] = f"Грейд выбран на экране К3: {grade}. Это сессия пилота (demo_assisted), в метрики не входит."
    out(first)


def parse_answer(raw: str, cur: dict[str, Any]) -> Any:
    mode = cur["answer_mode"]
    if mode == "text":
        return raw
    if mode == "choice":
        for oid in cur["option_ids"]:
            if str(oid) == raw.strip():
                return oid
        raise PilotError(f"Нет такого варианта; допустимые id: {cur['option_ids']}")
    try:
        val = json.loads(raw)
    except ValueError as e:
        fmt = '{"line": 3, "fix": "b"}' if mode == "line_choice" else "[...]"
        raise PilotError(f"Для режима {mode} ответ — JSON, например {fmt}") from e
    if mode == "line_choice" and not (isinstance(val, dict) and {"line", "fix"} <= set(val)):
        raise PilotError('Формат: {"line": номер строки с 1, "fix": id варианта}')
    if mode == "order" and not isinstance(val, list):
        raise PilotError("Формат: список строк или индексов с 0 в нужном порядке")
    return val


def cmd_answer(a: argparse.Namespace) -> None:
    pid = a.persona.upper()
    st = load_state(pid, a.attempt)
    if st.get("finished"):
        out({"status": "finished", "message": "Тест уже завершён. Результат — командой result."})
        return
    cur = st.get("current")
    if not cur:
        fail("Нет текущего задания: попробуйте start")
    if a.skip:
        value = None
    elif a.answer is None:
        fail("Нужен --answer (или --skip)")
    else:
        try:
            value = parse_answer(a.answer, cur)
        except PilotError as e:
            fail(str(e))
    api = Api()
    relogin(api, st)
    res = api.call("POST", f"/tests/sessions/{st['session_id']}/answer", json={"seq": cur["seq"], "answer": value})
    st["log"].append({
        "seq": cur["seq"], "variant_code": cur["variant_code"], "answer_mode": cur["answer_mode"],
        "time_limit_s": cur["time_limit_s"], "wall_s": round(time.time() - cur["shown_wall"], 1),
        "timed_out": res["timed_out"], "skipped": value is None, "used_clip": bool(cur.get("copied")),
        "clip_present": bool(cur.get("clip")), "confidence": a.conf,
    })
    st["current"] = None
    st["token"] = api.token
    save_state(st)
    if res["finished"]:
        out(finalize(api, st))
        return
    out(fetch_next(api, st))


def cmd_copy(a: argparse.Namespace) -> None:
    pid = a.persona.upper()
    st = load_state(pid, a.attempt)
    if st["kind"] != "cheater":
        fail("Копировать задание могут только «списывающие» персоны (C01, C02): честная персона решает сама")
    cur = st.get("current")
    if not cur:
        fail("Нет текущего задания")
    api = Api()
    relogin(api, st)
    api.call("POST", f"/tests/sessions/{st['session_id']}/events", json={"events": [{"type": "copy", "payload": {}}]})
    cur["copied"] = True
    save_state(st)
    clip = cur.get("clip")
    if clip:
        out({"status": "ok", "clipboard_text": clip["clipboard_text"],
             "note": "Это текст из буфера обмена. «Спроси ассистента»: реши именно его и ответь на экране тем же по смыслу."})
    else:
        item_text = (cur.get("shown_text") or "")
        out({"status": "ok", "clipboard_text": item_text or None,
             "note": "Защита от подсказчика для этого задания не включена: в буфер попал обычный текст экрана, решай то, что на экране."})


def cmd_result(a: argparse.Namespace) -> None:
    pid = a.persona.upper()
    st = load_state(pid, a.attempt)
    run = _read(run_path(pid, st["attempt"]), None)
    if run is None:
        out({"status": "in_progress", "message": "Тест ещё идёт: результат появится после последнего задания"})
        return
    out({"status": "ok", "persona": pid, "attempt": st["attempt"], "result": run["result"]})


def cmd_status(a: argparse.Namespace) -> None:
    rows = []
    for f in sorted(STATE_DIR.glob("*.json")):
        st = _read(f, {})
        rows.append({"persona": st.get("persona"), "attempt": st.get("attempt"), "answered": len(st.get("log", [])),
                     "finished": st.get("finished")})
    out({"status": "ok", "runs": len(list(RUNS_DIR.glob("*.json"))) if RUNS_DIR.exists() else 0, "state": rows})


def cmd_reset(a: argparse.Namespace) -> None:
    """Для оркестратора: убрать пилотные аккаунты (каскадом сессии), состояние и (без --keep-runs) прогоны."""
    accounts = _read(ACCOUNTS, {})
    if a.persona:
        pid = a.persona.upper()
        emails = [e for e in accounts if re.fullmatch(rf"pilot-{pid.lower()}(-a\d+)?@{re.escape(EMAIL_DOMAIN)}", e)]
        pattern = f"{pid}_a*.json"
    else:
        emails = [e for e in accounts if e.startswith("pilot-")]
        pattern = "*_a*.json"
    removed = drop_accounts(emails) if emails else 0
    with _accounts_lock():
        accounts = _read(ACCOUNTS, {})
        for e in emails:
            accounts.pop(e, None)
        _write(ACCOUNTS, accounts)
    n_state = 0
    for f in STATE_DIR.glob(pattern):
        f.unlink()
        n_state += 1
    n_runs = 0
    if not a.keep_runs and RUNS_DIR.exists():
        for f in RUNS_DIR.glob(pattern):
            f.unlink()
            n_runs += 1
    out({"status": "ok", "accounts_deleted": removed, "state_files": n_state, "run_files": n_runs})


def cmd_prepare_config(a: argparse.Namespace) -> None:
    """Копия config/ для бэкенда пилота: запас таймера задания 600 с вместо 5 (агент думает дольше человека-секунды)
    и достройка двойников на месте (lazy_budget_s 20 с).
    Лимит задания (60–90 с) агенту показывается как есть; просрочка не штрафуется, пока агент укладывается в запас."""
    src = BACKEND.parent / "config"
    if PILOT_CONFIG.exists():
        shutil.rmtree(PILOT_CONFIG)
    shutil.copytree(src, PILOT_CONFIG)
    f = PILOT_CONFIG / "testing.yaml"
    text = f.read_text(encoding="utf-8")
    new, n = re.subn(r"(timer_grace_s:\s*)\d+", r"\g<1>600", text, count=1)
    if n != 1:
        fail("timer_grace_s не найден в config/testing.yaml")
    f.write_text(new, encoding="utf-8")
    # Двойники достраиваются на месте (до 20 с на задание): предрасчёт пула занимает часы, а ловушка нужна всем сессиям пилота
    g = PILOT_CONFIG / "anticheat_runtime.yaml"
    new_g, n = re.subn(r"(lazy_budget_s:\s*)[\d.]+", r"\g<1>20.0", g.read_text(encoding="utf-8"), count=1)
    if n != 1:
        fail("lazy_budget_s не найден в config/anticheat_runtime.yaml")
    g.write_text(new_g, encoding="utf-8")
    out({"status": "ok", "config_dir": str(PILOT_CONFIG), "env": {"VZ_CONFIG_DIR": str(PILOT_CONFIG)},
         "changes": ["testing.yaml: timer_grace_s 5 -> 600", "anticheat_runtime.yaml: lazy_budget_s 0 -> 20"]})


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    ap = argparse.ArgumentParser(prog="pilot.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name: str, fn, persona: bool = True) -> argparse.ArgumentParser:  # type: ignore[no-untyped-def]
        sp = sub.add_parser(name)
        if persona:
            sp.add_argument("--persona", required=True)
        sp.set_defaults(fn=fn)
        return sp

    add("persona", cmd_persona)
    sp = add("start", cmd_start)
    sp.add_argument("--spec")
    sp.add_argument("--grade")
    sp.add_argument("--attempt", type=int, choices=(1, 2), default=1)
    sp = add("answer", cmd_answer)
    sp.add_argument("--attempt", type=int, choices=(1, 2))
    sp.add_argument("--answer")
    sp.add_argument("--skip", action="store_true")
    sp.add_argument("--conf", choices=("guess", "unsure", "sure"), help="насколько персона уверена в ответе (необязательно)")
    for name, fn in (("copy", cmd_copy), ("result", cmd_result)):
        sp = add(name, fn)
        sp.add_argument("--attempt", type=int, choices=(1, 2))
    add("status", cmd_status, persona=False)
    sp = add("reset", cmd_reset, persona=False)
    sp.add_argument("--persona")
    sp.add_argument("--keep-runs", action="store_true")
    add("prepare-config", cmd_prepare_config, persona=False)

    args = ap.parse_args()
    try:
        args.fn(args)
    except PilotError as e:
        fail(str(e))


if __name__ == "__main__":
    main()
