"""Демо-мир целиком: seed в пустую тестовую БД → вызовы настоящего API → сброс возвращает исходное состояние.

Мир малый (≈14 синтетических кандидатов Backend, пул экземпляров 14 клонов на семейство), но сборка — та же, что у
`make seed`: опрос, настоящие сессии адаптивного теста, персоны (Анна, Илья), ступенчатые аккаунты, шесть компаний,
брифы с версиями, приглашения во всех достижимых статусах, вакансии, отклики, задачи. Нагрузочные цифры (≈300
кандидатов) проверяются реальным запуском `scripts/seed_demo.py`, а не тестом.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.clock import clock
from app.core.settings import Settings
from app.models.company import Company
from app.models.contact import Application, Invitation, Vacancy
from app.models.fsp import FspLink
from app.models.matching import Need
from app.models.testing import ItemInstance, TestResponse
from app.models.testing import TestSession as SessionRow
from app.models.users import User
from app.services.demo import accounts, population, sink, world
from app.services.demo import models as _demo_models  # noqa: F401
from app.services.demo.cfg import section
from app.services.demo.models import DemoPersona
from app.services.testing import qualification as qual
from app.services.testing import sessions
from app.services.testing.keystore import decrypt_key
from app.services.testing.pool import families_for, generate_rows, pool_seeds, seed_family_params
from app.testing.cat import load_blueprint

N_SYNTHETIC = 14
POOL_K = 14


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None, env="test", demo_mode=True, argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1,
        mail_backend="console",
    )


@pytest.fixture
def app(test_settings, mailer, db_session):
    from app.db.session import get_db
    from app.main import create_app

    application = create_app(test_settings, mailer)
    application.state.demo_loop = False

    def _override() -> Iterator:
        yield db_session

    application.dependency_overrides[get_db] = _override
    return application


@pytest.fixture
def sink_transport():
    previous = sink.install()
    yield
    sink.uninstall(previous)


@pytest.fixture
def pool(db_session):
    rows = [row for f in families_for("backend_python") for row in generate_rows(f, pool_seeds(f.id, range(POOL_K)))]
    db_session.execute(pg_insert(ItemInstance).values(rows).on_conflict_do_nothing(index_elements=["family_id", "seed"]))
    seed_family_params(db_session, spec="backend_python")
    db_session.flush()


def backend_indices(n: int) -> list[int]:
    cfg = population.population_config(60)
    out = []
    for i in range(60):
        c = population.generate_candidate(i, cfg)
        if c.claimed_spec == "backend_python" and c.truth.spec == "backend_python":
            out.append(i)
        if len(out) == n:
            break
    return out


@pytest.fixture
def seeded(db_session, test_settings, pool, sink_transport, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(world, "BACKEND_DIR", tmp_path)  # файл с паролями — во временный каталог
    cfg = population.population_config(60)
    for i in backend_indices(N_SYNTHETIC):
        assert population.seed_candidate(db_session, test_settings, i, population.generate_candidate(i, cfg)) is not None
    out = world.build_personas_and_scenario(db_session, test_settings)
    accounts.REUSE.clear()
    world.write_credentials_file(test_settings)
    return out


def login(client, email: str, password: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, (email, r.text)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def user_of(db, email: str) -> User:
    u = db.scalar(select(User).where(User.email == email))
    assert u is not None, email
    return u


def snapshot(db) -> dict[str, int]:
    c = world.counts(db)
    return {k: c[k] for k in ("companies_demo", "needs", "invitations", "vacancies", "applications", "employer_tasks",
                              "candidates_synthetic", "test_sessions")}


def test_seed_builds_personas_staged_accounts_and_scenario(seeded, client, db_session, test_settings, tmp_path):
    cfg = section("personas")
    # --- синтетические кандидаты: настоящие сессии с трассами; повтор seed идемпотентен
    assert db_session.scalar(select(func.count()).select_from(DemoPersona).where(DemoPersona.kind == "synthetic")) == N_SYNTHETIC
    first = backend_indices(1)[0]
    assert population.seed_candidate(db_session, test_settings, first, population.generate_candidate(first, population.population_config(60))) is None
    n_resp = db_session.scalar(select(func.count()).select_from(TestResponse))
    assert n_resp > N_SYNTHETIC * 6  # у демо-кандидатов есть ответы с трассой (лаборатория Л1)
    assert db_session.scalar(select(func.count()).select_from(SessionRow).where(SessionRow.demo_assisted.is_(True))) == 0

    # --- Анна: ФСП-призёр, сессия IN_PROGRESS на 11 ответах, без висящего задания
    anna = user_of(db_session, cfg["anna"]["email"])
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == anna.id))
    assert link is not None and link.fsp_id == cfg["anna"]["fsp_sub"] and link.sync_status == "ok"
    s = db_session.scalar(select(SessionRow).where(SessionRow.candidate_id == anna.id))
    assert s.status == "IN_PROGRESS" and s.n_items == int(cfg["anna"]["session_answered"])
    assert qual.get_row(db_session, anna.id, "backend_python").status == "TESTING"

    # --- Илья: Middle без ФСП, SQL не подтверждён для Middle
    ilya = user_of(db_session, cfg["ilya"]["email"])
    assert db_session.scalar(select(FspLink).where(FspLink.user_id == ilya.id)) is None
    q = qual.get_row(db_session, ilya.id, "backend_python")
    assert q.status == "CONFIRMED" and q.grade == "middle"
    assert db_session.get(DemoPersona, ilya.id).profile["sql_theta"] is not None

    # --- ступенчатые аккаунты
    stage = {st["key"]: user_of(db_session, st["email"]) for st in section("staged")}
    assert qual.get_row(db_session, stage["registered"].id, "backend_python") is None
    assert qual.get_row(db_session, stage["survey"].id, "backend_python").status == "SURVEY_DONE"
    mid = qual.get_row(db_session, stage["midtest"].id, "backend_python")
    assert mid.status == "TESTING"
    assert db_session.scalar(select(SessionRow).where(SessionRow.candidate_id == stage["midtest"].id)).status == "IN_PROGRESS"
    cat = qual.get_row(db_session, stage["category"].id, "backend_python")
    assert cat.status == "CONFIRMED" and cat.grade == "middle"

    # «Мария» заранее не создаётся — по сценарию регистрируется вживую
    assert db_session.scalar(select(User).where(User.email == section("accounts")["fresh_registration_email"])) is None

    # --- компании, брифы, приглашения, вакансии, задачи
    assert db_session.scalar(select(func.count()).select_from(Company).where(Company.is_demo.is_(True))) == 6
    sc = seeded["scenario"]
    assert sc["needs"] == len(section("needs")) and sc["versions"] == sc["needs"] and sc["pins"] > 0
    plan = section("invitation_plan")
    skipped_inv = [m for m in sc["skipped"] if m.startswith("приглашение")]
    assert sc["invitations"] + len(skipped_inv) == len(plan)
    statuses = set(db_session.scalars(select(Invitation.status)))
    # Мир малый (14 кандидатов на 8 брифов), итог сессий CAT зависит от случайных id сессий: из 12 запланированных приглашений
    # создаётся 5–6 (по 20 прогонам 6 — в 75%, 5 — в 25%); порог — с запасом, смысл проверки в статусах ниже.
    assert sc["invitations"] >= 4 and statuses <= {
        "sent", "viewed", "countered", "accepted", "declined", "counter_declined", "expired", "withdrawn", "closed",
        "consent_revoked",
    }
    assert {"sent", "viewed", "countered"} & statuses  # есть «живые» приглашения, ждущие ответа
    # Анну и Илью заготовленная история не трогает: их приглашает и открывает вживую «Северный код»
    persona_ids = {anna.id, ilya.id}
    assert not set(db_session.scalars(select(Invitation.candidate_id))) & persona_ids
    assert not set(db_session.scalars(select(Application.candidate_id))) & persona_ids
    assert db_session.scalar(select(func.count()).select_from(Vacancy)) == sc["vacancies"] > 0
    assert sc["employer_tasks"] >= 1

    # --- файл с учётными данными: пароли выводятся из секрета, в stdout не печатаются
    text = (tmp_path / "var" / "demo_accounts.md").read_text(encoding="utf-8")
    assert accounts.password_for(test_settings, "persona:anna") in text and cfg["anna"]["email"] in text
    assert "maria@demo.example.com" in text and "Mailpit" in text

    # --- вход под выданными паролями и вызовы настоящего API
    anna_h = login(client, cfg["anna"]["email"], accounts.password_for(test_settings, "persona:anna"))
    assert client.get("/api/invitations/inbox", headers=anna_h).status_code == 200
    quals = client.get("/api/tests/qualification", headers=anna_h).json()
    assert quals[0]["status"] == "TESTING"
    hr_h = login(client, "hr@severny-kod.example", accounts.password_for(test_settings, "employer:severny_kod"))
    needs = client.get("/api/needs", headers=hr_h).json()["items"]
    assert len(needs) == 2 and all(n["current_version"] == 1 for n in needs)
    kanban = client.get("/api/invitations", headers=hr_h)
    assert kanban.status_code == 200
    jury_h = login(client, "jury@demo.example.com", accounts.password_for(test_settings, "staff:jury"))
    labs = client.get("/api/lab/sessions", headers=jury_h).json()
    assert len(labs) >= N_SYNTHETIC
    done = next(x for x in labs if x["status"] in ("FINAL",))
    trace = client.get(f"/api/lab/sessions/{done['id']}/trace", headers=jury_h)
    assert trace.status_code == 200 and trace.json()["steps"]
    admin_h = login(client, "admin@demo.example.com", accounts.password_for(test_settings, "staff:admin"))
    assert client.get("/api/admin/import", headers=admin_h).status_code == 200
    state = client.get("/api/demo/state").json()
    assert state["synthetic_candidates"] == N_SYNTHETIC and state["scenario_built_at"] is not None

    # --- сессии «на середине» не обрываются за 30 минут тишины: демо-цикл освежает отметку активности персон
    from app.services.demo import personas

    clock.advance(40 * 60)
    assert personas.keep_alive(db_session) >= 2  # Анна и «середина теста»
    sessions.refresh_user_sessions(db_session, anna.id)
    sessions.refresh_user_sessions(db_session, stage["midtest"].id)
    for uid in (anna.id, stage["midtest"].id):
        assert db_session.scalar(select(SessionRow.status).where(SessionRow.candidate_id == uid)) == "IN_PROGRESS"


def test_reset_restores_seed_state(seeded, client, db_session, test_settings, make_account):
    cfg = section("personas")
    before = snapshot(db_session)
    anna = user_of(db_session, cfg["anna"]["email"])
    anna_id = anna.id
    anna_h = login(client, cfg["anna"]["email"], accounts.password_for(test_settings, "persona:anna"))

    # Операторский день: часы +3 дня, «Мария» зарегистрировалась, Анна дозавершила тест
    assert client.post("/api/demo/clock", json={"preset": "plus_3d"}, headers=anna_h).json()["offset_seconds"] == 3 * 86400
    maria = make_account(section("accounts")["fresh_registration_email"], role="candidate")
    assert user_of(db_session, "maria@demo.example.com").id == maria.user_id
    sid = db_session.scalar(select(SessionRow.id).where(SessionRow.candidate_id == anna_id))
    status = "IN_PROGRESS"
    for _ in range(3):  # сценарий: «ответить на 12-е, на 13-м стоп» — не больше двух-трёх верных ответов
        ld, item = sessions.next_item(db_session, anna, sid)
        assert item is not None
        res = sessions.submit_answer(db_session, anna, sid, item.response.seq, decrypt_key(item.instance.key_enc))
        status = str(res.ld.sess.status)
        if status != "IN_PROGRESS":
            break
    assert status in ("FINAL", "DECIDED", "EXTENSION_OFFERED")
    assert qual.get_row(db_session, anna_id, "backend_python").status == "CONFIRMED"

    r = client.post("/api/demo/reset", headers=anna_h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["companies_demo"] == 6 and body["scenario"]["needs"] == len(section("needs"))

    assert clock.offset_seconds == 0 and client.get("/api/demo/state").json()["clock"]["offset_seconds"] == 0
    assert db_session.scalar(select(User).where(User.email == "maria@demo.example.com")) is None  # зарегистрируется заново
    after = snapshot(db_session)
    assert {k: after[k] for k in before if k not in ("invitations", "applications")} == {
        k: v for k, v in before.items() if k not in ("invitations", "applications")
    }
    assert abs(after["invitations"] - before["invitations"]) <= 1  # история приглашений та же (до одного: состав шортлиста)
    # Анна: тот же аккаунт и токен, сессия снова на 11 ответах, категории нет
    anna2 = user_of(db_session, cfg["anna"]["email"])
    assert anna2.id == anna_id
    s = db_session.scalar(select(SessionRow).where(SessionRow.candidate_id == anna_id))
    assert s.status == "IN_PROGRESS" and s.n_items == int(cfg["anna"]["session_answered"])
    assert qual.get_row(db_session, anna_id, "backend_python").status == "TESTING"
    assert client.get("/api/invitations/inbox", headers=anna_h).status_code == 200  # тот же токен всё ещё действует
    assert db_session.scalar(select(func.count()).select_from(Need)) == len(section("needs"))


# --------------------------------------------------------------------------- добавочные кандидаты QA и DevOps


@pytest.fixture
def pool_qa_devops(db_session):
    """Пул 14 клонов на семейство для тестов QA и DevOps (общие семейства — по одному разу)."""
    fams = {f.id: f for spec in ("qa", "devops") for f in families_for(spec)}
    rows = [row for f in fams.values() for row in generate_rows(f, pool_seeds(f.id, range(POOL_K)))]
    db_session.execute(pg_insert(ItemInstance).values(rows).on_conflict_do_nothing(index_elements=["family_id", "seed"]))
    for spec in ("qa", "devops"):
        seed_family_params(db_session, spec=spec)
    db_session.flush()


def test_extra_ranges_are_disjoint_from_the_eval_population():
    qa, ops = population.extra_indices("qa"), population.extra_indices("devops")
    assert len(qa) == len(ops) == 40 and not set(qa) & set(ops)
    assert min(qa + ops) >= 2000  # префикс популяции eval — не больше 2000 человек
    assert population.extra_indices("qa", 5) == qa[:5]
    with pytest.raises(KeyError):
        population.extra_indices("mobile")
    cfg = population.extra_config("devops")
    assert cfg.spec_mix == (("devops", 1.0),) and cfg.mismatch_rate == 0.0 and not cfg.run_tests


@pytest.mark.parametrize("spec", ["qa", "devops"])
def test_extra_candidates_take_their_own_test_and_get_a_category(db_session, test_settings, pool_qa_devops, spec):
    """seed_candidate для добавочных кандидатов: опрос со своей специализацией, настоящая сессия QA/DevOps, категория;
    повторный seed идемпотентен; кандидаты видны как синтетические (ключ cNNNN в диапазоне добавочных номеров)."""
    cfg = population.extra_config(spec)
    indices = population.extra_indices(spec, 4)
    made = []
    for i in indices:
        cand = population.generate_candidate(i, cfg)
        assert cand.truth.spec == spec and cand.claimed_spec == spec
        uid = population.seed_candidate(db_session, test_settings, i, cand)
        assert uid is not None
        made.append(uid)
    assert population.seed_candidate(db_session, test_settings, indices[0], population.generate_candidate(indices[0], cfg)) is None
    assert set(population.synthetic_ids(db_session)) == set(indices)
    sessions_ = list(db_session.scalars(select(SessionRow).where(SessionRow.candidate_id.in_(made))))
    assert sessions_ and all(s.specialization == spec for s in sessions_)
    assert all(s.status in ("FINAL", "DECIDED", "EXTENSION_OFFERED") for s in sessions_)
    quals = [qual.get_row(db_session, uid, spec) for uid in made]
    assert all(q is not None and q.status in ("CONFIRMED", "NOT_CONFIRMED") for q in quals)
    assert any(q.status == "CONFIRMED" for q in quals)
    modules = set(db_session.scalars(
        select(TestResponse.module).where(TestResponse.session_id.in_([s.id for s in sessions_])).distinct()
    ))
    own = "qa_testing" if spec == "qa" else "devops_core"
    assert own in modules and modules <= set(load_blueprint(spec))  # задания только из blueprint своей специализации
