import dataclasses
from datetime import date
from pathlib import Path

import pytest

from dieta import plan_service as svc
from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import NewPlanMeal, NewPlanSession, Profile, RecipeMeta, Store

WEEK = date(2026, 11, 23)  # poniedziałek
PREV_WEEK = date(2026, 11, 16)


def _recipe(sid: str, title: str, kcal: float = 400.0, names: tuple[str, ...] = ("ryż", "kurczak")) -> Recipe:
    return Recipe(
        source="respo",
        source_id=sid,
        title=title,
        url=f"https://example.org/{sid}",
        image=None,
        servings=2,
        kcal=kcal,
        protein=25,
        carbs=40,
        fat=12,
        minutes=30,
        tags=(),
        category_hint=None,
        ingredients=tuple(RawIngredient(f"1 {n}", 1, None, n) for n in names),
    )


def _profile(person_id: int) -> Profile:
    return Profile(person_id, "f", 30, 165.0, 2, "maintain", 0.0, None, None, 0.3)


def _setup(tmp_path: Path, people: tuple[int, ...] = (1, 2)) -> tuple[Store, Settings, list[int]]:
    settings = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(settings.db_path)
    for pid in people:
        store.save_profile(_profile(pid))
        store.add_weight(pid, date(2026, 11, 1), 70.0)
    ids: list[int] = []
    layout = (
        ("lunch", 4, 380.0),
        ("dinner", 4, 420.0),
        ("breakfast", 2, 300.0),
    )
    for category, count, kcal in layout:
        for i in range(count):
            rid = store.upsert_recipe(
                _recipe(f"{category}{i}", f"{category} {i}", kcal + 10 * i),
                RecipeMeta(category, 3, True, "rule"),
            )
            ids.append(rid)
    return store, settings, ids


def test_monday_of_normalizes_any_day_and_bad_input() -> None:
    assert svc.monday_of("2026-11-25", date(2026, 11, 1)) == WEEK
    assert svc.monday_of("2026-11-23", date(2026, 11, 1)) == WEEK
    assert svc.monday_of("", WEEK) == WEEK
    assert svc.monday_of("nie-data", WEEK) == WEEK


def test_pool_skips_dismissed_and_uncategorized(tmp_path: Path) -> None:
    store, settings, ids = _setup(tmp_path, people=())
    store.set_flag(ids[0], "dismissed", True)
    store.upsert_recipe(_recipe("bez", "Bez kategorii", 300.0), RecipeMeta(None, None, None, "pending"))
    pool = svc.build_pool(store, settings)
    got = {c.id for c in pool.items}
    assert ids[0] not in got
    assert ids[1] in got
    assert all(c.title != "Bez kategorii" for c in pool.items)
    assert all(not c.dismissed for c in pool.items)


def test_pool_keeps_dismissed_plan_recipe_for_swaps(tmp_path: Path) -> None:
    store, settings, ids = _setup(tmp_path, people=())
    store.set_flag(ids[0], "dismissed", True)
    pool = svc.build_pool(store, settings, extra_ids=[ids[0]])
    dismissed = [c for c in pool.items if c.id == ids[0]]
    assert len(dismissed) == 1 and dismissed[0].dismissed


def test_generate_save_read_roundtrip(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    out = svc.generate(store, settings, WEEK, seed=1)
    assert out.notice == svc.NOTICE_GENERATED
    plan = store.plan(WEEK)
    assert plan is not None and plan.status == "draft"
    view = svc.view(store, settings, WEEK)
    meals = [m for d in view.days for m in d.meals]
    assert meals, "plan z danych demo nie ma posiłków"
    assert all(m.p1 is not None and 0.6 <= m.p1 <= 1.8 for m in meals)
    assert view.has_target == (True, True)
    assert all(m.session_days >= 1 for m in meals)


def _meal_keys(store: Store) -> list[tuple[int, str, int, float | None]]:
    plan = store.plan(WEEK)
    assert plan is not None
    sessions = {s.id: s for s in store.plan_sessions(plan.id)}
    return [
        (m.day, m.slot, sessions[m.session_id].recipe_id, m.p1_portions) for m in store.plan_meals(plan.id)
    ]


def test_generation_is_deterministic_for_same_seed(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=7)
    first = _meal_keys(store)
    svc.generate(store, settings, WEEK, seed=7)
    assert first == _meal_keys(store)


def test_no_profiles_means_no_plan(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path, people=())
    assert svc.generate(store, settings, WEEK).notice == svc.NOTICE_NO_PROFILES
    assert store.plan(WEEK) is None


def test_one_person_only_plans_for_that_person(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path, people=(2,))
    assert svc.generate(store, settings, WEEK, seed=3).notice == svc.NOTICE_GENERATED
    view = svc.view(store, settings, WEEK)
    assert view.has_target == (False, True)
    assert all(m.p1 is None and m.p2 is not None for d in view.days for m in d.meals)


def test_undo_restores_previous_contents(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=1)
    plan = store.plan(WEEK)
    assert plan is not None
    before = (store.plan_sessions(plan.id), store.plan_meals(plan.id))
    session = store.plan_sessions(plan.id)[0]
    svc.swap_session(store, settings, WEEK, session.id, "random")
    assert svc.undo(store, WEEK).notice == svc.NOTICE_UNDONE
    assert (store.plan_sessions(plan.id), store.plan_meals(plan.id)) == before
    assert svc.undo(store, WEEK).notice == svc.NOTICE_NOTHING_TO_UNDO


def test_swap_changes_contents_or_reports_unchanged(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=1)
    plan = store.plan(WEEK)
    assert plan is not None
    before = store.plan_sessions(plan.id)
    out = svc.swap_day(store, settings, WEEK, 0, "lunch", "random")
    assert out.notice in (svc.NOTICE_SWAPPED, svc.NOTICE_UNCHANGED)
    if out.notice == svc.NOTICE_UNCHANGED:
        assert store.plan_sessions(plan.id) == before


def test_swap_session_unknown_id_changes_nothing(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=1)
    plan = store.plan(WEEK)
    assert plan is not None
    before = store.plan_meals(plan.id)
    assert svc.swap_session(store, settings, WEEK, 999_999, "random").notice == svc.NOTICE_NOT_FOUND
    assert store.plan_meals(plan.id) == before


def test_approved_week_needs_confirm_to_regenerate(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=1)
    assert svc.approve(store, WEEK).notice == svc.NOTICE_APPROVED
    plan = store.plan(WEEK)
    assert plan is not None and plan.status == "approved"
    before = store.plan_meals(plan.id)
    assert svc.generate(store, settings, WEEK, seed=2).notice == svc.NOTICE_NEEDS_CONFIRM
    assert store.plan_meals(plan.id) == before
    assert store.plan(WEEK).status == "approved"  # type: ignore[union-attr]
    assert svc.generate(store, settings, WEEK, confirm=True, seed=2).notice == svc.NOTICE_GENERATED
    assert store.plan(WEEK).status == "draft"  # type: ignore[union-attr]
    assert store.plan(WEEK).seed == 2  # type: ignore[union-attr]


def test_approve_without_plan(tmp_path: Path) -> None:
    store, _, _ = _setup(tmp_path)
    assert svc.approve(store, WEEK).notice == svc.NOTICE_NO_PLAN


def test_swap_allowed_after_approval(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=1)
    svc.approve(store, WEEK)
    session = store.plan_sessions(store.plan(WEEK).id)[0]  # type: ignore[union-attr]
    out = svc.swap_session(store, settings, WEEK, session.id, "random")
    assert out.notice in (svc.NOTICE_SWAPPED, svc.NOTICE_UNCHANGED)
    assert store.plan(WEEK).status == "approved"  # type: ignore[union-attr]


def test_history_blocks_recent_approved_recipes_only_outside_own_week(tmp_path: Path) -> None:
    store, _, ids = _setup(tmp_path, people=())
    sessions = [NewPlanSession("lunch", ids[0], 0, 1)]
    meals = [NewPlanMeal(0, "lunch", 0, 1.0, 1.0)]
    store.save_plan(PREV_WEEK, "approved", 1, "{}", sessions, meals)
    hist = svc.history(store, WEEK)
    assert hist == {ids[0]: PREV_WEEK}
    own = [NewPlanSession("lunch", ids[1], 0, 1)]
    own_meals = [NewPlanMeal(0, "lunch", 0, 1.0, 1.0)]
    store.save_plan(WEEK, "approved", 2, "{}", own, own_meals)
    assert ids[1] not in svc.history(store, WEEK)


def test_snapshot_records_status(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    svc.generate(store, settings, WEEK, seed=1)
    plan = store.plan(WEEK)
    assert plan is not None
    snap = svc._snapshot(store, plan)
    assert '"status": "draft"' in snap


def _link_with_home(store: Store, monkeypatch: pytest.MonkeyPatch, home: set[str]) -> None:
    from dieta.profile import IngredientStat

    for name in ("ryż", "kurczak"):
        store.link_recipe_ingredient(name, name, "dry")  # type: ignore[arg-type]
        store.upsert_ingredient(name, None)
    stats = {
        n: IngredientStat(n, 1, 1.0, date(2026, 11, 20), 1.0, n in home, None) for n in ("ryż", "kurczak")
    }
    monkeypatch.setattr(svc, "_stat_map", lambda store, settings: stats)


def _ratio(store: Store, settings: Settings, rid: int) -> float:
    cheaper = dataclasses.replace(settings, cheaper_mode=True)  # home_ratio liczony tylko w trybie „taniej”
    return next(c for c in svc.build_pool(store, cheaper).items if c.id == rid).home_ratio


def test_home_ratio_by_mass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, settings, _ = _setup(tmp_path)
    rid = store.upsert_recipe(
        Recipe(
            source="respo", source_id="mass", title="Masa", url="https://example.org/mass", image=None,
            servings=2, kcal=400.0, protein=25, carbs=40, fat=12, minutes=30, tags=(), category_hint=None,
            ingredients=(
                RawIngredient("300 g ryż", 300, "g", "ryż"),
                RawIngredient("100 g kurczak", 100, "g", "kurczak"),
            ),
        ),
        RecipeMeta("lunch", 3, True, "rule"),
    )  # fmt: skip
    _link_with_home(store, monkeypatch, {"ryż"})
    assert _ratio(store, settings, rid) == pytest.approx(0.75)


def test_home_ratio_falls_back_to_line_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, settings, _ = _setup(tmp_path)
    rid = store.upsert_recipe(
        Recipe(
            source="respo", source_id="cnt", title="Liczba", url="https://example.org/cnt", image=None,
            servings=2, kcal=400.0, protein=25, carbs=40, fat=12, minutes=30, tags=(), category_hint=None,
            ingredients=(
                RawIngredient("ryż", None, None, "ryż"),
                RawIngredient("kurczak", None, None, "kurczak"),
            ),
        ),
        RecipeMeta("lunch", 3, True, "rule"),
    )  # fmt: skip
    _link_with_home(store, monkeypatch, {"ryż"})
    assert _ratio(store, settings, rid) == pytest.approx(0.5)


def test_home_ratio_zero_without_stats(tmp_path: Path) -> None:
    store, settings, ids = _setup(tmp_path)
    assert _ratio(store, settings, ids[0]) == 0.0


def test_plan_config_passes_cheaper_mode(tmp_path: Path) -> None:
    on = Settings(data_dir=tmp_path, log_level="info", dev=True, cheaper_mode=True)
    off = Settings(data_dir=tmp_path, log_level="info", dev=True)
    assert svc.plan_config(on).cheaper is True
    assert svc.plan_config(off).cheaper is False
