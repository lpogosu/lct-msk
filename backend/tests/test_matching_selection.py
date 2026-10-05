"""Версии подборки: неизменяемость, неразрушающие фильтры (hidden_by), бейджи diff, шортлист с жёлтой меткой,
откат на последние 10 версий, вкладки «По категориям» / «Общий топ» (концепция §3.2 шаг 6, §4.4 п.5)."""

import dataclasses
import random
from datetime import datetime

import pytest

from app.matching.scoring import BriefSpec, CandidateFeatures, ModuleScore, ScoringConfig, TaskResult
from app.matching.selection import (
    Filter,
    SelectionError,
    SelectionHistory,
    Shortlist,
    build_version,
    derive_version,
    diff_badges,
    page,
    shortlist_view,
)

BRIEF = BriefSpec(
    need_id="need-s",
    spec="backend_python",
    grade="middle",
    must=("python", "fastapi", "postgresql"),
    nice=("kafka", "docker"),
    salary_from=220_000,
    salary_to=280_000,
    formats=frozenset({"hybrid"}),
    city="moscow",
)
CATS = (("backend_python", "middle"), ("backend_python", "senior"), ("fullstack", "middle"))
NOW = datetime(2026, 10, 5, 12, 0)


def make_pool(n: int = 90, seed: int = 5) -> list[CandidateFeatures]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        theta = rng.gauss(0.6, 0.6)
        spec, grade = rng.choice(CATS)
        mods = {
            m: ModuleScore(
                theta + rng.gauss(0, 0.4),
                rng.uniform(0.25, 0.6),
                rng.choice(["confirmed", "strong", "low_data"]),
            )
            for m in ("python_core", "sql", "http_api", "algo", "js_core", "react")
        }
        tasks = tuple(
            TaskResult(
                f"t{i}-{j}",
                rng.choice("AB"),
                rng.random(),
                rng.uniform(0, 120),
                f"Задача {j}",
                skills=(rng.choice(["kafka", "docker", "sql"]),),
            )
            for j in range(rng.randint(0, 2))
        )
        out.append(
            CandidateFeatures(
                candidate_id=f"c{i:03d}",
                spec=spec,
                grade=grade,
                theta=theta,
                se=rng.uniform(0.25, 0.45),
                test_days_ago=rng.uniform(1, 200),
                test_session_id=f"s{i}",
                modules=mods,
                tasks=tasks,
                vss_by_discipline={"product": rng.uniform(20, 95)} if rng.random() < 0.35 else {},
                fsp_ref=f"fsp-{i}",
                salary_min=rng.randrange(170_000, 330_000, 1000),
                formats=frozenset(rng.sample(["office", "hybrid", "remote"], rng.randint(1, 3))),
                city=rng.choice(["moscow", "spb"]),
                industries=frozenset({rng.choice(["fintech", "ecommerce", "gov"])}),
            )
        )
    # Fullstack-кандидат виден в двух категориях.
    fs = dataclasses.replace(
        out[0], candidate_id="fs-1", spec="backend_python", grade="middle", is_fullstack=True, theta=1.5
    )
    out.append(fs)
    out.append(dataclasses.replace(fs, spec="fullstack"))
    return out


POOL = make_pool()


@pytest.fixture(scope="module")
def v1():
    return build_version(brief=BRIEF, pool=POOL, categories=CATS, created_at=NOW)


def test_version_is_immutable(v1):
    with pytest.raises(dataclasses.FrozenInstanceError):
        v1.n = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        v1.items[0].components["T"] = 1.0  # type: ignore[index]
    assert isinstance(v1.items, tuple)
    assert v1.scoring_config_version == ScoringConfig.load().version
    assert v1.created_at == "2026-10-05T12:00:00"
    assert v1.preset == "balance"


def test_version_contains_full_ranked_list(v1):
    assert len(v1.items) == len(POOL)
    ranks = [i.rank for i in v1.visible()]
    assert ranks == list(range(1, len(ranks) + 1))
    scores = [i.score for i in v1.visible()]
    assert all(a >= b - 1e-9 for a, b in zip(scores, scores[1:], strict=False))
    for i in v1.items:
        assert set(i.components) == set("TKFAU")
        assert i.reasons, "у каждой строки есть хотя бы один довод"


def test_fullstack_appears_in_both_tabs_but_once_in_overall(v1):
    tabs = v1.by_category()
    in_backend = [i.candidate_id for i in tabs[("backend_python", "middle")]]
    in_fs = [i.candidate_id for i in tabs[("fullstack", "middle")]]
    assert "fs-1" in in_backend and "fs-1" in in_fs
    overall = [i.candidate_id for i in v1.overall_top()]
    assert overall.count("fs-1") == 1
    assert len(overall) == len(set(overall))


def test_tab_limit_and_paging(v1):
    tabs = v1.by_category(limit=ScoringConfig.load().section("selection")["max_per_category"])
    assert all(len(v) <= 50 for v in tabs.values())
    first = page(v1.overall_top(), 1)
    second = page(v1.overall_top(), 2)
    assert len(first) == 10 and first[-1].rank == 10 and second[0].rank == 11


def test_filters_are_non_destructive(v1):
    v2 = derive_version(v1, POOL, n=2, filters=(Filter("has_fsp"),), created_at=NOW)
    assert len(v2.items) == len(v1.items)
    hidden = v2.hidden()
    assert hidden and all("has_fsp" in i.hidden_by for i in hidden)
    assert all(i.hidden_reasons == ("Нет достижений ФСП",) for i in hidden)
    assert v2.hidden_summary()["has_fsp"] == len(hidden)
    assert all(i.has_fsp for i in v2.visible())
    # Скрытые сохраняют Score и причины — фильтр ничего не пересчитывает.
    s1 = {(i.candidate_id, i.spec): i.score for i in v1.items}
    assert all(abs(s1[(i.candidate_id, i.spec)] - i.score) < 1e-12 for i in v2.items)
    assert v2.parent_n == 1 and v1.filters == ()


def test_brief_edit_creates_new_version_and_diff_badges(v1):
    brief2 = dataclasses.replace(BRIEF, must=BRIEF.must + ("kafka",), nice=("docker",))
    v2 = derive_version(v1, POOL, n=2, brief=brief2, filters=(Filter("must_confirmed"),))
    assert v2.brief.must[-1] == "kafka" and v1.brief.must == BRIEF.must
    badges = diff_badges(v1, v2)
    kinds = {b.kind for b in badges.values()}
    assert "hidden" in kinds
    for b in badges.values():
        if b.kind == "up":
            assert b.label == f"▲{b.delta}" and b.delta > 0
        if b.kind == "down":
            assert b.label == f"▼{b.delta}"
    assert diff_badges(None, v1) == {}
    # Снятие фильтра возвращает скрытых: они «новые» относительно v2.
    v3 = derive_version(v2, POOL, n=3, filters=())
    back = diff_badges(v2, v3)
    assert any(b.kind == "new" and b.label == "новый" for b in back.values())


def test_shortlist_survives_versions_with_yellow_mark(v1):
    no_fsp = next(i.candidate_id for i in v1.visible() if not i.has_fsp)
    with_fsp = next(i.candidate_id for i in v1.visible() if i.has_fsp)
    sl = Shortlist("need-s").pin(no_fsp, NOW).pin(with_fsp, NOW)
    v2 = derive_version(v1, POOL, n=2, filters=(Filter("has_fsp"),))
    view = {e.candidate_id: e for e in shortlist_view(sl, v2)}
    assert view[no_fsp].mark == "yellow" and view[no_fsp].reason == "Нет достижений ФСП"
    assert view[with_fsp].mark == "ok"
    assert all(e.mark == "ok" for e in shortlist_view(sl, v1))
    gone = shortlist_view(Shortlist("need-s").pin("nobody"), v1)
    assert gone[0].mark == "yellow" and gone[0].item is None


def test_shortlist_limit():
    sl = Shortlist("n")
    for i in range(30):
        sl = sl.pin(f"c{i}")
    with pytest.raises(SelectionError):
        sl.pin("c99")
    assert len(sl.unpin("c0").pins) == 29


def test_history_rollback_last_10():
    h = SelectionHistory("need-s").add(build_version(brief=BRIEF, pool=POOL[:30], categories=CATS))
    v1 = h.current
    for k in range(11):
        h = h.derive(POOL[:30], filters=(Filter("min_score", float(k)),))
    assert h.current.n == 12 and len(h.versions) == 12
    assert h.rollback_targets() == tuple(range(3, 13))
    with pytest.raises(SelectionError):
        h.rollback(1)
    h2 = h.rollback(3)
    assert h2.current.n == 3 and h.current.n == 12  # откат — перенос указателя, история не меняется
    assert h2.get(3) is h.get(3)  # без пересчёта — тот же объект версии
    h3 = h2.derive(POOL[:30], preset="stack")
    assert h3.current.n == 13 and h3.current.parent_n == 3 and h3.current.preset == "stack"
    with pytest.raises(SelectionError):
        h3.add(v1)  # версии неизменяемы, повторно v1 не добавить


def test_rebuild_is_deterministic(v1):
    again = build_version(brief=BRIEF, pool=list(reversed(POOL)), categories=CATS, created_at=NOW)
    assert again.to_dict() == v1.to_dict()


def test_neighbors_and_filters_catalog(v1):
    assert len(v1.neighbors) == len(v1.visible()) - 1
    n1 = v1.neighbor(1)
    assert n1.text.startswith("#1 выше #2")
    for f in (
        Filter("salary_fit"),
        Filter("format"),
        Filter("city"),
        Filter("skill", "kafka"),
        Filter("min_score", 50),
        Filter("active"),
        Filter("industry", ("fintech",)),
        Filter("grade", ("middle",)),
        Filter("confirmed_only"),
        Filter("exclude", ["c001"]),
    ):
        v = derive_version(v1, POOL, n=2, filters=(f,))
        assert len(v.items) == len(v1.items)
        assert all(i.hidden_by == (f.code,) for i in v.items if not i.visible)
    with pytest.raises(SelectionError):
        Filter("magic")


def test_to_dict_is_json_ready(v1):
    import json

    d = v1.to_dict()
    json.dumps(d, ensure_ascii=False)
    item = d["items"][0]
    assert {"components", "reasons", "hidden_by", "parts"} <= set(item)
    assert abs(sum(item["parts"].values()) - item["score"]) < 0.051


def test_build_2000_candidates_fast_enough():
    """Цель §7.2: p95 сборки подборки на 2000 кандидатов ≤ 300 мс; здесь — грубый порог против регрессий."""
    import time

    big = make_pool(2000, seed=1)
    build_version(brief=BRIEF, pool=big[:20], categories=CATS)
    t0 = time.perf_counter()
    v = build_version(brief=BRIEF, pool=big, categories=CATS)
    assert time.perf_counter() - t0 < 1.5
    assert len(v.items) == ScoringConfig.load().section("selection")["max_items"]


# --------------------------------------------------------------------------- регрессии ревью


def test_hidden_counter_and_pinned_rows_survive_truncation():
    """Скрытые стоят в конце и первыми попадают под обрезку до 500 строк — счётчик «Скрыто (N)» считается до
    обрезки, а закреплённый в шортлисте кандидат остаётся в версии с жёлтой меткой и причиной."""
    big = make_pool(700, seed=2)
    in_scope_no_fsp = {c.candidate_id for c in big if not c.has_fsp}
    v = build_version(brief=BRIEF, pool=big, categories=CATS, filters=(Filter("has_fsp"),))
    assert len(v.items) == 500 and v.total_n == len(big)
    assert v.hidden_n == len(in_scope_no_fsp) > len(v.hidden())
    assert v.hidden_summary() == {"has_fsp": len(in_scope_no_fsp)}
    assert v.to_dict()["hidden_n"] == v.hidden_n
    present = {i.candidate_id for i in v.items}
    lost = sorted(in_scope_no_fsp - present)[0]
    assert shortlist_view(Shortlist("need-s").pin(lost), v)[0].reason == "Нет в текущей версии подборки"
    v_keep = build_version(brief=BRIEF, pool=big, categories=CATS, filters=(Filter("has_fsp"),), keep=[lost])
    entry = shortlist_view(Shortlist("need-s").pin(lost), v_keep)[0]
    assert entry.mark == "yellow" and entry.reason == "Нет достижений ФСП"
    assert len(v_keep.items) == 501


def test_filter_values_are_validated():
    for code in ("skill", "min_score", "industry", "grade", "exclude"):
        with pytest.raises(SelectionError):
            Filter(code)
    with pytest.raises(SelectionError):
        Filter("min_score", "много")
    assert Filter("industry", {"gov", "fintech"}).value == ("fintech", "gov")


def test_category_tabs_capped_at_50_by_default():
    big = make_pool(400, seed=4)
    v = build_version(brief=BRIEF, pool=big, categories=CATS)
    tabs = v.by_category()
    assert max(len(t) for t in tabs.values()) == 50
    assert max(len(t) for t in v.by_category(limit=0).values()) > 50


def test_exported_components_are_coarse():
    v = build_version(brief=BRIEF, pool=POOL, categories=CATS)
    for item in v.to_dict()["items"]:
        for value in item["components"].values():
            assert round(value, 2) == value
