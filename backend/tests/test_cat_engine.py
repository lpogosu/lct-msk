"""CAT-движок: выбор задания, исключения, pretest, стоп, решение, доборы, подбаллы (spec-testing §3.4–3.6)."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.testing.cat import (
    BORDERLINE_FAIL,
    BORDERLINE_PASS,
    FAILED,
    OPERATIONAL,
    PASSED,
    PRETEST,
    QUARANTINE,
    TAG_CONFIRMED,
    TAG_INSUFFICIENT,
    TAG_NONE,
    TAG_STRONG,
    CatState,
    FamilyMeta,
    ResponseRecord,
    ScaleParam,
    compute_offers,
    decide,
    level_breakdown,
    load_cat_config,
    meta_from_family,
    module_subscores,
    module_tag,
    new_state,
    record_answer,
    select_next,
    start_extension,
    stop_check,
)
from app.testing.irt import info_at, load_scale
from app.testing.simulate import SimulatedExaminee, synthetic_families

SCALE = load_scale()
CFG = load_cat_config()
SPEC = "backend_python"
BANK = synthetic_families((SPEC,), seed=3)
BY_ID = {f.id: f for f in BANK}
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def fam(fid, module="python_core", grade="middle", pos=0.0, **kw):
    return FamilyMeta(id=fid, module=module, specs=(SPEC,), target_grade=grade, position=pos, **kw)


def run_main(theta, claimed, seed, families=BANK, max_steps=40):
    """Основная часть сессии через чистые функции движка."""
    rng = random.Random(seed)
    st = new_state(SPEC, claimed, rng)
    ex = SimulatedExaminee(theta=theta)
    by_id = {f.id: f for f in families}
    for _ in range(max_steps):
        sel = select_next(st, families, rng, now=NOW)
        if sel is None:
            return st, decide(st, "bank_exhausted")
        record_answer(st, sel, ex.answer(by_id[sel.family_id], SPEC, SCALE, rng))
        reason = stop_check(st)
        if reason:
            return st, decide(st, reason)
    raise AssertionError("сессия не остановилась")


def make_record(seq, b, correct, module="python_core", grade="middle", pretest=False, a=1.35):
    return ResponseRecord(
        seq=seq, family_id=f"f{seq}", module=module, family_grade=grade, is_pretest=pretest,
        correct=correct, b=b, c=0.0, a=a,
    )


# --- общий приор ------------------------------------------------------------------------------


def test_same_prior_for_all_claimed_grades():
    """Заявленный грейд выбирает только порог: при одинаковых ответах θ̂ и SE одинаковы (§3.3)."""
    recs = [make_record(i + 1, b, i % 3 != 0) for i, b in enumerate((-0.5, 0.0, 0.3, 0.6, -0.2, 0.1))]
    states = []
    for claimed in ("intern", "junior", "middle", "senior"):
        st = new_state(SPEC, claimed, random.Random(0))
        st.responses = [ResponseRecord(**r.__dict__) for r in recs]
        states.append(st)
    posts = [s.posterior(SCALE) for s in states]
    assert len({round(p.mean, 10) for p in posts}) == 1
    assert len({round(p.sd, 10) for p in posts}) == 1
    empty = [new_state(SPEC, g, random.Random(1)).posterior(SCALE).mean for g in ("junior", "senior")]
    assert empty[0] == empty[1] == pytest.approx(-0.25, abs=0.01)
    # отличается только P_pass — относительно своего порога
    assert states[0].p_pass(SCALE) > states[3].p_pass(SCALE)


# --- выбор задания -----------------------------------------------------------------------------


def test_module_quota_deficit_order():
    """k* = argmax(q_k·(n+1) − n_k): python, sql, http_api, algo, python, git_linux."""
    rng = random.Random(5)
    st = new_state(SPEC, "middle", rng)
    modules = []
    for _ in range(6):
        sel = select_next(st, BANK, rng, now=NOW)
        modules.append(sel.module)
        record_answer(st, sel, True)
    assert modules == ["python_core", "sql", "http_api", "algo", "python_core", "git_linux"]


def test_topk_by_information_at_target_cut_and_reason():
    rng = random.Random(11)
    st = new_state(SPEC, "senior", rng)
    ks = []
    for i in range(7):
        sel = select_next(st, BANK, rng, now=NOW)
        r = sel.reason
        ks.append(r["k"])
        assert r["target_cut"] == 1.0
        assert sel.family_id in [t["family_id"] for t in r["top_k"]]
        infos = [t["info"] for t in r["top_k"]]
        assert infos == sorted(infos, reverse=True)
        # top-K — действительно самые информативные у порога Senior среди допустимых семейств модуля
        # (при достигнутой доле 30% задания с угадыванием недопустимы)
        rest = [
            info_at(1.0, f.item_params(SPEC, SCALE))
            for f in BANK
            if f.module == sel.module and f.id not in st.seen_family_ids and f.id not in {t["family_id"] for t in r["top_k"]}
            and not (r["mcq_capped"] and f.c > 0)
        ]
        assert min(infos) >= max(rest) - 1e-4
        for key in ("module", "deficit", "deficits", "chosen", "text_ru"):
            assert key in r
        assert "θ" not in r["text_ru"]
        record_answer(st, sel, i % 2 == 0)
    assert ks == [5, 5, 5, 5, 3, 3, 3]


def test_enemies_are_mutually_exclusive():
    a = fam("A", enemies=("B",))
    b = fam("B")
    c = fam("C", pos=1.0)
    d = fam("D", pos=-1.0)
    bp = {"python_core": 1.0}
    for seed in range(30):
        rng = random.Random(seed)
        st = new_state(SPEC, "middle", rng, blueprint=bp)
        st.pretest_positions = ()
        record_answer(st, select_next(st, [a], rng), True)
        sel = select_next(st, [a, b, c, d], rng)
        assert sel.family_id in {"C", "D"}
        # и в обратную сторону: показан B → A недоступен
        st2 = new_state(SPEC, "middle", rng, blueprint=bp)
        st2.pretest_positions = ()
        record_answer(st2, select_next(st2, [b], rng), True)
        assert select_next(st2, [a, b, c, d], rng).family_id in {"C", "D"}


def test_history_exclusion_12_months_and_unavailable_pools():
    bp = {"python_core": 1.0}
    fams = [fam("old"), fam("recent"), fam("empty_pool", free_instances=0), fam("quar", status=QUARANTINE),
            fam("pre", status=PRETEST)]
    history = {"recent": NOW - timedelta(days=100), "old": NOW - timedelta(days=400)}
    for seed in range(20):
        rng = random.Random(seed)
        st = new_state(SPEC, "middle", rng, blueprint=bp)
        sel = select_next(st, fams, rng, now=NOW, seen_history=history)
        assert sel.family_id == "old"


def test_exposure_cap_and_relaxation():
    bp = {"python_core": 1.0}
    hot = fam("hot", exposure_30d=0.5)
    cool = [fam(f"c{i}", pos=p) for i, p in enumerate((0.0, 0.5, -0.5))]
    for seed in range(20):
        rng = random.Random(seed)
        st = new_state(SPEC, "middle", rng, blueprint=bp)
        sel = select_next(st, [hot, *cool], rng)
        assert sel.family_id != "hot"
        assert sel.reason["exposure_relaxed"] is False
    # допустимых меньше 3 → лимит снимается с записью в журнал
    rng = random.Random(1)
    st = new_state(SPEC, "middle", rng, blueprint=bp)
    sel = select_next(st, [hot, cool[0]], rng)
    assert sel.reason["exposure_relaxed"] is True
    record_answer(st, sel, True)
    assert any(e["event"] == "exposure_cap_relaxed" for e in st.log)


def test_pretest_slots_not_scored():
    bank = synthetic_families((SPEC,), pretest_per_module=4, seed=3)
    rng = random.Random(21)
    st = new_state(SPEC, "middle", rng)
    assert len(st.pretest_positions) == 2
    assert all(3 <= p <= 14 for p in st.pretest_positions)
    ex = SimulatedExaminee(theta=0.4)
    by_id = {f.id: f for f in bank}
    for _ in range(16):
        before = st.posterior(SCALE).mean
        sel = select_next(st, bank, rng, now=NOW)
        rec = record_answer(st, sel, ex.answer(by_id[sel.family_id], SPEC, SCALE, rng))
        if sel.seq in st.pretest_positions:
            assert sel.is_pretest and by_id[sel.family_id].status == PRETEST
            assert abs(sel.item.b - 0.0) <= 1.0  # |b_prior − t| ≤ 1
            assert rec.theta_after == pytest.approx(before, abs=1e-3)  # в оценку не входит
        else:
            assert not sel.is_pretest and by_id[sel.family_id].status == OPERATIONAL
    assert st.n_pretest == 2 and st.n_scored == 14


def test_bank_exhausted_returns_none():
    bp = {"python_core": 1.0}
    rng = random.Random(0)
    st = new_state(SPEC, "middle", rng, blueprint=bp)
    fams = [fam("only")]
    record_answer(st, select_next(st, fams, rng), True)
    assert select_next(st, fams, rng) is None


# --- стоп и решение ------------------------------------------------------------------------------


def test_stop_between_8_and_20_items():
    lengths = []
    for seed in range(40):
        theta = random.Random(seed).uniform(-2.5, 2.0)
        st, d = run_main(theta, "middle", seed)
        assert CFG.n_min <= d.n_items <= CFG.n_max
        lengths.append(d.n_items)
    assert min(lengths) == 8 and max(lengths) <= 20


def test_clear_cases_stop_at_8_with_confident_decision():
    st, d = run_main(2.5, "junior", 1)
    assert d.code == PASSED and d.n_items == 8 and d.p_pass >= 0.9 and d.reason == "confidence"
    st, d = run_main(-3.0, "senior", 2)
    assert d.code == FAILED and d.n_items == 8 and d.p_pass <= 0.1


def test_coverage_required_before_stop():
    """Нельзя остановиться, пока модуль с квотой ≥ 15% не встречен (algo 15% — да, git 10% — нет)."""
    st = new_state(SPEC, "junior", random.Random(0))
    st.pretest_positions = ()
    # 10 верных ответов только по python/sql/http — уверенность есть, но algo не встречен
    for i in range(10):
        mod = ("python_core", "sql", "http_api")[i % 3]
        st.responses.append(make_record(i + 1, -1.0, True, module=mod, grade="junior"))
    assert st.p_pass(SCALE) >= 0.9
    assert stop_check(st) is None
    st.responses.append(make_record(11, -1.0, True, module="algo", grade="junior"))
    assert stop_check(st) == "confidence"  # git_linux (10%) не обязателен


def _borderline_state(n_right):
    st = new_state(SPEC, "middle", random.Random(0))
    for i in range(20):
        st.responses.append(make_record(i + 1, 0.0, i < n_right, module=list(st.blueprint)[i % 5]))
    return st


def test_borderline_decisions_at_20():
    st = _borderline_state(11)
    p = st.p_pass(SCALE)
    assert 0.5 <= p < 0.9
    assert stop_check(st) == "max_items"
    d = decide(st, "max_items")
    assert d.code == BORDERLINE_PASS and d.passed and d.borderline
    st = _borderline_state(9)
    p = st.p_pass(SCALE)
    assert 0.1 < p < 0.5
    d = decide(st, "max_items")
    assert d.code == BORDERLINE_FAIL and not d.passed  # «как FAILED»
    offers = compute_offers(st, d)
    assert [o.direction for o in offers] == ["down"]


# --- доборы -------------------------------------------------------------------------------------


def test_extension_down_reuses_answers():
    for seed in range(60):
        st, d = run_main(-0.7, "middle", seed)
        if not d.passed:
            break
    else:
        pytest.fail("не нашли провал Middle у кандидата уровня Junior")
    offers = compute_offers(st, d)
    down = next(o for o in offers if o.direction == "down")
    assert down.target_grade == "junior"
    parent_post = st.posterior(SCALE)
    assert down.p_already == pytest.approx(parent_post.p_at_least(SCALE.cut("junior")), abs=1e-4)
    child = start_extension(st, down)
    # тот же апостериор: прошлые ответы засчитываются
    assert child.n_scored == st.n_scored and child.n_phase_scored == 0
    assert child.posterior(SCALE).mean == pytest.approx(parent_post.mean)
    assert child.target_grade == "junior" and child.kind == "ext_down"
    assert child.p_pass(SCALE) == pytest.approx(down.p_already, abs=1e-4)
    if down.instant:
        assert stop_check(child) == "confidence"
        assert decide(child, "confidence").code == PASSED
        assert decide(child, "confidence").n_items == 0


def test_extension_is_limited_to_8_items_and_skips_seen_families():
    rng = random.Random(4)
    ex = SimulatedExaminee(theta=-1.0)  # ровно на пороге Junior — добор не сойдётся быстро
    for seed in range(80):
        st, d = run_main(-1.0, "middle", seed)
        if d.passed:
            continue
        down = compute_offers(st, d)[0]
        if down.instant:
            continue
        child = start_extension(st, down)
        seen_before = set(child.seen_family_ids)
        while True:
            sel = select_next(child, BANK, rng, now=NOW)
            assert sel is not None and sel.family_id not in seen_before
            assert not sel.is_pretest
            record_answer(child, sel, ex.answer(BY_ID[sel.family_id], SPEC, SCALE, rng))
            reason = stop_check(child)
            if reason:
                break
        assert 1 <= child.n_phase_scored <= CFG.ext_max_items
        assert child.n_scored == st.n_scored + child.n_phase_scored
        return
    pytest.fail("нет сценария с неочевидным добором вниз")


def test_offer_up_when_clearly_above():
    st, d = run_main(1.8, "junior", 3)
    assert d.code == PASSED
    offers = compute_offers(st, d)
    assert len(offers) == 1 and offers[0].direction == "up" and offers[0].target_grade == "middle"
    assert offers[0].p_already >= CFG.offer_up_min_p
    # с Senior вверх некуда
    st, d = run_main(2.5, "senior", 3)
    assert d.code == PASSED and compute_offers(st, d) == ()


def test_down_offer_skips_grades_already_decided_below():
    """P(θ ≥ cut(g−1)) ≤ 0.10 → добор на g−1 закончился бы провалом на 0 заданиях: сразу ниже (§3.5)."""
    hit = 0
    for seed in range(40):
        st, d = run_main(-2.3, "senior", seed)
        if d.passed or st.posterior(SCALE).p_at_least(SCALE.cut("middle")) > CFG.p_stop_low:
            continue
        offers = compute_offers(st, d)
        if not offers:
            continue
        down = offers[0]
        assert down.direction == "down" and down.target_grade != "middle"
        assert down.p_already > CFG.p_stop_low
        child = start_extension(st, down)
        if not down.instant:
            assert stop_check(child) is None  # добор действительно задаёт вопросы
        hit += 1
    assert hit >= 5


def test_up_offer_is_instant_when_answers_already_suffice():
    st = new_state(SPEC, "junior", random.Random(0))
    for i in range(14):
        st.responses.append(make_record(i + 1, 0.3, i % 7 != 0, module=list(st.blueprint)[i % 5], grade="middle"))
    d = decide(st, "confidence")
    assert d.code == PASSED
    p_mid = st.posterior(SCALE).p_at_least(SCALE.cut("middle"))
    assert p_mid >= CFG.ext_pass_p
    (up,) = compute_offers(st, d)
    assert up.direction == "up" and up.instant and up.max_items == 0
    assert up.text_ru.startswith("Ваших ответов достаточно, чтобы подтвердить Middle")
    child = start_extension(st, up)
    assert stop_check(child) == "confidence" and decide(child, "confidence").code == PASSED


def test_guessing_items_share_capped_at_30_percent():
    """Доля заданий с c > 0 ≤ 30% в любой момент теста (§2.5), даже если они информативнее у порога."""
    bp = {"python_core": 1.0}
    mcq = [fam(f"mcq{i}", pos=p, c=0.25) for i, p in enumerate((0.0, 0.0, 0.1, -0.1, 0.2, -0.2) * 3)]
    plain = [fam(f"pl{i}", grade=g, pos=p) for i, (g, p) in enumerate(
        [(g, p) for g in ("junior", "middle", "senior") for p in (-1.0, 1.0)] * 3)]
    bank = mcq + plain
    for seed in range(10):
        rng = random.Random(seed)
        st = new_state(SPEC, "middle", rng, blueprint=bp)
        st.pretest_positions = ()
        for _ in range(20):
            sel = select_next(st, bank, rng)
            record_answer(st, sel, rng.random() < 0.5)
            n_guess = sum(1 for r in st.scored if r.c > 0)
            assert n_guess <= CFG.mcq_max_share * st.n_scored + 1e-9
        assert any(r.c > 0 for r in st.scored)  # правило — доля, а не запрет
        capped = [r.selection_reason for r in st.responses if r.selection_reason.get("mcq_capped")]
        assert capped and "угадыванием" in capped[0]["text_ru"]
    # мягкость: в модуле только задания с угадыванием → выбор не блокируется
    rng = random.Random(1)
    st = new_state(SPEC, "middle", rng, blueprint=bp)
    st.pretest_positions = ()
    assert select_next(st, mcq, rng).item.c == 0.25


def test_intern_fail_has_no_down_offer():
    st, d = run_main(-3.5, "intern", 5)
    assert not d.passed
    assert compute_offers(st, d) == ()


# --- подбаллы и теги -----------------------------------------------------------------------------


def test_module_tag_thresholds():
    assert module_tag(0, 0.99, 0.99) == TAG_INSUFFICIENT
    assert module_tag(1, 0.99, 0.99) == TAG_INSUFFICIENT  # n_k < 2 → «~»
    assert module_tag(2, 0.80, 0.79) == TAG_CONFIRMED  # ✓: P(θ_k ≥ c_J) ≥ 0.8
    assert module_tag(2, 0.80, 0.80) == TAG_STRONG  # ✓✓: P(θ_k ≥ c_g) ≥ 0.8
    assert module_tag(5, 0.79, 0.95) == TAG_NONE


def test_module_subscores_shrinkage_and_tags():
    st = new_state(SPEC, "middle", random.Random(0))
    seq = 0

    def add(module, b, ok, n):
        nonlocal seq
        for _ in range(n):
            seq += 1
            st.responses.append(make_record(seq, b, ok, module=module))

    add("python_core", 0.5, True, 4)  # сильный модуль
    add("sql", -1.5, False, 4)  # слабый
    add("http_api", 0.0, True, 1)  # одно задание
    add("algo", 0.0, True, 2)
    subs = module_subscores(st, "middle")
    assert subs["python_core"]["tag"] == TAG_STRONG and subs["python_core"]["symbol"] == "✓✓"
    assert subs["sql"]["tag"] == TAG_NONE
    assert subs["http_api"]["tag"] == TAG_INSUFFICIENT and subs["http_api"]["symbol"] == "~"
    assert subs["git_linux"]["n"] == 0 and subs["git_linux"]["tag"] == TAG_INSUFFICIENT
    theta_hat = st.posterior(SCALE).mean
    # без данных подбалл = общий уровень (сжатие)
    assert subs["git_linux"]["theta"] == pytest.approx(theta_hat, abs=0.02)
    assert subs["python_core"]["theta"] > theta_hat > subs["sql"]["theta"]
    assert subs["python_core"]["level"] in ("at", "above") and subs["sql"]["level"] == "below"


def test_level_breakdown_text():
    st = new_state(SPEC, "middle", random.Random(0))
    seq = 0
    for grade, right, total in (("junior", 3, 3), ("middle", 6, 8), ("senior", 1, 2)):
        for i in range(total):
            seq += 1
            st.responses.append(make_record(seq, 0.0, i < right, grade=grade))
    st.responses.append(make_record(99, 0.0, True, grade="intern", pretest=True))  # pretest не считается
    counts, text = level_breakdown(st)
    assert counts == {"junior": [3, 3], "middle": [6, 8], "senior": [1, 2]}
    assert text == "Junior 3/3, Middle 6/8, Senior 1/2"


# --- служебное ----------------------------------------------------------------------------------


def test_state_roundtrip_and_adapter():
    st, _ = run_main(0.2, "middle", 9)
    clone = CatState.from_dict(st.to_dict())
    assert clone.posterior(SCALE).mean == pytest.approx(st.posterior(SCALE).mean)
    assert clone.pretest_positions == st.pretest_positions

    class FakeBankFamily:  # утиная типизация по контракту bank/base.py, без реального банка
        id = "py.fake"
        title = "Тестовое семейство"
        specs = ("backend_python",)
        module = "python_core"
        format = "predict_output"
        target_grade = "junior"
        position = 1.0
        c = 0.0
        time_limit_s = 60
        enemies = ("py.other",)
        is_anchor = True

    meta = meta_from_family(FakeBankFamily(), params={"backend_python": ScaleParam(b=-0.5, sigma_b=0.3)})
    assert meta.enemies == ("py.other",) and meta.is_anchor and meta.time_limit_s == 60
    assert meta.scale_param("backend_python", SCALE).b == -0.5
    # для другой шкалы — экспертный приор cut(g) + 0.35·pos
    assert meta.scale_param("frontend_js", SCALE).b == pytest.approx(-0.65)
