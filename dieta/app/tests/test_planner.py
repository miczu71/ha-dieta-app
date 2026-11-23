import json
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from dieta.planner import (
    Candidate,
    PersonTarget,
    PlanConfig,
    PlanResult,
    Targets,
    evaluate,
    generate,
    swap_day,
    swap_session,
)

WEEK = date(2026, 11, 23)  # poniedziałek
FIXTURE = Path(__file__).parent / "fixtures" / "plan_pool.json"


def _pool() -> list[Candidate]:
    return [Candidate(**d) for d in json.loads(FIXTURE.read_text(encoding="utf-8"))]


POOL = _pool()
BY_ID = {c.id: c for c in POOL}
T1 = PersonTarget(1900, 100, 63, 238)
T2 = PersonTarget(2600, 140, 87, 325)
TARGETS = Targets(T1, T2)
CFG = PlanConfig()


def _gen(
    pool: list[Candidate] | None = None,
    targets: Targets = TARGETS,
    config: PlanConfig = CFG,
    history: dict[int, date] | None = None,
    seed: int = 1,
) -> PlanResult:
    return generate(POOL if pool is None else pool, targets, config, history or {}, seed, WEEK)


def _key(plan: PlanResult) -> tuple[tuple[object, ...], ...]:
    return tuple((s.slot, s.recipe_id, s.start_day, s.days) for s in plan.sessions)


def _swap_s(plan: PlanResult, slot: str, start: int, mode: str = "random", seed: int = 1) -> PlanResult:
    return swap_session(plan, POOL, TARGETS, CFG, {}, seed, WEEK, slot, start, mode)


def test_a_day_kcal_within_10pct_two_targets() -> None:
    for t1, t2 in ((1900, 2600), (2600, 1900)):
        targets = Targets(PersonTarget(t1, 100, 60, 240), PersonTarget(t2, 140, 85, 320))
        plan = _gen(targets=targets)
        assert not plan.warnings
        for bal in evaluate(plan, POOL, targets, CFG):
            assert bal.p1 and bal.p2
            assert bal.p1.within_tolerance and bal.p2.within_tolerance
            assert bal.p1.kcal > 0 and bal.p2.kcal > 0


def test_b_week_average_within_5pct() -> None:
    plan = _gen()
    bal = evaluate(plan, POOL, TARGETS, CFG)
    for i, t in ((1, T1), (2, T2)):
        got = sum((b.p1 if i == 1 else b.p2).kcal for b in bal if (b.p1 if i == 1 else b.p2)) / 7
        assert abs(got - t.kcal * 0.9) / (t.kcal * 0.9) <= 0.05


def test_c_no_repeat_with_history() -> None:
    first = _gen()
    history = {s.recipe_id: WEEK - timedelta(days=1) for s in first.sessions}
    history[BY_ID[2].id] = WEEK - timedelta(days=20)  # stare: nie blokuje
    plan = _gen(history=history)
    used = {s.recipe_id for s in plan.sessions}
    assert not (used & {s.recipe_id for s in first.sessions}) - {2}
    assert len(used) == len(plan.sessions)  # raz w tygodniu


def test_d_no_dismissed() -> None:
    dismissed = {c.id for c in POOL if c.dismissed}
    assert dismissed
    for seed in range(1, 6):
        assert not {s.recipe_id for s in _gen(seed=seed).sessions} & dismissed


def test_e_determinism() -> None:
    assert _gen(seed=1) == _gen(seed=1)
    assert any(_key(_gen(seed=s)) != _key(_gen(seed=1)) for s in range(2, 8))


def test_f_discovery_counts() -> None:
    thr = CFG.familiar_threshold
    for seed in (1, 2, 3):
        p0 = _gen(config=replace(CFG, discovery=0), seed=seed)
        assert all(BY_ID[s.recipe_id].familiarity >= thr for s in p0.sessions)
        assert not p0.warnings
        p2 = _gen(config=replace(CFG, discovery=2), seed=seed)
        assert sum(BY_ID[s.recipe_id].familiarity < thr for s in p2.sessions) == 2


def test_g_sessions_start_on_cook_days_and_limits() -> None:
    cfg = replace(CFG, cook_days=(5, 6, 2), session_days_max=4)
    for seed in (1, 2, 3):
        plan = _gen(config=cfg, seed=seed)
        for s in plan.sessions:
            c = BY_ID[s.recipe_id]
            assert s.start_day in {0, 2, 5, 6}
            assert s.days <= min(c.storage_days or 1, cfg.session_days_max)
            assert s.start_day + s.days <= 7
        for slot in ("breakfast", "lunch", "dinner"):
            assert sorted(m.day for m in plan.meals if m.slot == slot) == list(range(7))


def test_h_disabled_slot_has_no_meals() -> None:
    plan = _gen()
    assert {m.slot for m in plan.meals} == {"breakfast", "lunch", "dinner"}
    cfg = replace(CFG, slot_pct={"breakfast": 0, "lunch": 40, "dinner": 30, "snack1": 10, "snack2": 0})
    plan = _gen(config=cfg)
    assert {m.slot for m in plan.meals} == {"lunch", "dinner", "snack1"}
    assert all(b.p1 and b.p1.within_tolerance for b in evaluate(plan, POOL, TARGETS, cfg))


def test_i_single_person() -> None:
    targets = Targets(None, T2)
    plan = _gen(targets=targets)
    assert plan.meals and all(m.p1_portions is None and m.p2_portions for m in plan.meals)
    bal = evaluate(plan, POOL, targets, CFG)
    assert all(b.p1 is None and b.p2 and b.p2.within_tolerance for b in bal)
    assert _gen(targets=Targets(None, None)).meals == ()


def test_j_empty_slot_pool_warns() -> None:
    pool = [c for c in POOL if "breakfast" not in c.slot_affinity]
    plan = _gen(pool=pool)
    assert any("śniadanie" in w for w in plan.warnings)
    assert {m.slot for m in plan.meals} == {"lunch", "dinner"}


def test_j_small_pool_lowers_threshold() -> None:
    # tylko 1 znajomy obiad/kolacja (>=50%), reszta poniżej progu
    keep = {c.id for c in POOL if "breakfast" in c.slot_affinity}
    pool = [replace(c, familiarity=0.2) if c.id not in keep else c for c in POOL]
    plan = _gen(pool=pool, config=replace(CFG, discovery=0))
    assert any("próg znajomości obniżony" in w for w in plan.warnings)
    assert {m.slot for m in plan.meals} == {"breakfast", "lunch", "dinner"}


def test_k_swap_session_random() -> None:
    plan = _gen()
    for sess in plan.sessions:
        res = _swap_s(plan, sess.slot, sess.start_day)
        if res.sessions == plan.sessions and res.meals == plan.meals:
            assert res.warnings
            continue
        new = next(s for s in res.sessions if s.slot == sess.slot and s.start_day == sess.start_day)
        assert new.recipe_id != sess.recipe_id
        old_c, new_c = BY_ID[sess.recipe_id], BY_ID[new.recipe_id]
        assert abs(new_c.kcal - old_c.kcal) / old_c.kcal <= 0.15
        ids = [s.recipe_id for s in res.sessions]
        assert len(ids) == len(set(ids))
        for bal in evaluate(res, POOL, TARGETS, CFG):
            assert bal.p1 and bal.p2
            assert abs(bal.p1.kcal - bal.p1.target_kcal) / bal.p1.target_kcal <= 0.15
            assert abs(bal.p2.kcal - bal.p2.target_kcal) / bal.p2.target_kcal <= 0.15


def test_k_swap_session_liked() -> None:
    plan = _gen()
    changed = 0
    for sess in plan.sessions:
        res = _swap_s(plan, sess.slot, sess.start_day, "liked")
        new = next(s for s in res.sessions if s.slot == sess.slot and s.start_day == sess.start_day)
        if new.recipe_id != sess.recipe_id:
            changed += 1
            assert BY_ID[new.recipe_id].liked
    assert changed
    nolike = [replace(c, liked=False) for c in POOL]
    plan2 = generate(nolike, TARGETS, CFG, {}, 1, WEEK)
    s0 = plan2.sessions[0]
    res = swap_session(plan2, nolike, TARGETS, CFG, {}, 1, WEEK, s0.slot, s0.start_day, "liked")
    assert res.sessions == plan2.sessions and res.meals == plan2.meals
    assert res.warnings


def _manual_plan(cand: Candidate, start: int, days: int, slot: str = "lunch") -> PlanResult:
    from dieta.planner import Meal, Session

    cfg = CFG
    from dieta.planner import _ctx, _fit  # noqa: PLC0415

    fit = _fit(_ctx(POOL, TARGETS, cfg, {}, WEEK), cand, slot)
    assert fit
    return PlanResult(
        (Session(slot, cand.id, start, days),),
        tuple(Meal(d, slot, cand.id, fit.p1, fit.p2) for d in range(start, start + days)),
    )


def test_l_swap_day_moves_freed_portion() -> None:
    cand = next(c for c in POOL if "lunch" in c.slot_affinity and (c.storage_days or 0) >= 4)
    plan = _manual_plan(cand, 1, 2)  # dni 1-2, po sesji wolne dni 3..6
    res = swap_day(plan, POOL, TARGETS, CFG, {}, 1, WEEK, "lunch", 2, "random")
    days_old = sorted(m.day for m in res.meals if m.recipe_id == cand.id)
    assert days_old == [1, 3]
    new_meal = next(m for m in res.meals if m.day == 2)
    assert new_meal.recipe_id != cand.id
    sess = next(s for s in res.sessions if s.recipe_id == cand.id)
    assert (sess.start_day, sess.days) == (1, 3)
    assert not res.warnings


def test_l_swap_day_drops_portion() -> None:
    # przekroczenie storage_days
    cand = next(c for c in POOL if "lunch" in c.slot_affinity and c.storage_days == 2)
    plan = _manual_plan(cand, 1, 2)
    res = swap_day(plan, POOL, TARGETS, CFG, {}, 1, WEEK, "lunch", 2, "random")
    assert [m.day for m in res.meals if m.recipe_id == cand.id] == [1]
    assert res.warnings and "porzucona" in res.warnings[0]
    # brak wolnego dnia (sesja do niedzieli)
    cand2 = next(c for c in POOL if "lunch" in c.slot_affinity and (c.storage_days or 0) >= 3)
    plan2 = _manual_plan(cand2, 5, 2)
    res2 = swap_day(plan2, POOL, TARGETS, CFG, {}, 1, WEEK, "lunch", 6, "random")
    assert [m.day for m in res2.meals if m.recipe_id == cand2.id] == [5]
    assert res2.warnings


def test_m_portions_in_range() -> None:
    for targets in (TARGETS, Targets(PersonTarget(1200, 80, 40, 150), PersonTarget(3200, 160, 100, 400))):
        for seed in (1, 2, 3):
            plan = _gen(targets=targets, seed=seed)
            for m in plan.meals:
                for p in (m.p1_portions, m.p2_portions):
                    assert p is not None and 0.6 <= p <= 1.8


def test_n_low_protein_penalised() -> None:
    base = dict(
        slot_affinity={"lunch": 1.0},
        kcal=700.0,
        carbs=70.0,
        fat=31.0,
        minutes=30,
        storage_days=7,
        familiarity=0.8,
        main_ingredient=None,
    )
    low = Candidate(id=1, title="niskie", protein=5.0, **base)  # type: ignore[arg-type]
    high = Candidate(id=2, title="wysokie", protein=50.0, **base)  # type: ignore[arg-type]
    cfg = PlanConfig(
        slot_pct={"breakfast": 0, "lunch": 35, "dinner": 0, "snack1": 0, "snack2": 0},
        cook_days=(0,),
        session_days_max=7,
        discovery=0,
    )
    targets = Targets(PersonTarget(2000, 150, 66, 250), None)
    for pool in ([low, high], [high, low]):
        for seed in range(1, 6):
            plan = generate(pool, targets, cfg, {}, seed, WEEK)
            assert [s.recipe_id for s in plan.sessions] == [2]


@pytest.mark.parametrize("mode", ["random", "liked"])
def test_swap_unknown_session_does_nothing(mode: str) -> None:
    plan = _gen()
    res = _swap_s(plan, "snack1", 0, mode)
    assert res.sessions == plan.sessions and res.warnings
