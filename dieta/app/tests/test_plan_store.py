import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from dieta.sources.base import RawIngredient, Recipe
from dieta.store import _MIGRATIONS, NewPlanMeal, NewPlanSession, RecipeMeta, Store

MON = date(2026, 11, 23)
META = RecipeMeta("lunch", 4, True, "rule")


def _recipe(source_id: str) -> Recipe:
    return Recipe(
        source="respo", source_id=source_id, title=f"T{source_id}", url=source_id, image=None,
        servings=2, kcal=500.0, protein=20, carbs=50, fat=15, minutes=30, tags=(), category_hint=None,
        ingredients=(RawIngredient("ryż", None, None, "ryż"),),
    )  # fmt: skip


def _store(tmp_path: Path) -> tuple[Store, int, int]:
    store = Store(tmp_path / "d.db")
    return store, store.upsert_recipe(_recipe("a"), META), store.upsert_recipe(_recipe("b"), META)


def _content(r1: int, r2: int) -> tuple[list[NewPlanSession], list[NewPlanMeal]]:
    sessions = [NewPlanSession("lunch", r1, 0, 2), NewPlanSession("dinner", r2, 1, 1)]
    meals = [
        NewPlanMeal(0, "lunch", 0, 1.0, 1.4),
        NewPlanMeal(1, "lunch", 0, 1.0, None),
        NewPlanMeal(1, "dinner", 1, 0.8, 1.2),
    ]
    return sessions, meals


def _counts(path: Path) -> tuple[int, int, int, int]:
    conn = sqlite3.connect(path)
    try:
        return tuple(  # type: ignore[return-value]
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("plan", "plan_session", "plan_meal", "plan_undo")
        )
    finally:
        conn.close()


def test_migration_v4_to_v5_keeps_data_and_adds_plan_tables(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:4], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute(
        "INSERT INTO profile (person_id, sex, age, height_cm, pal_level, goal, pace_kg_week, fat_pct) "
        "VALUES (1, 'm', 40, 180, 2, 'lose', 0.5, 0.3)"
    )
    conn.execute("INSERT INTO weight (person_id, day, kg) VALUES (1, '2026-11-01', 80)")
    conn.execute(
        "INSERT INTO recipe (id, source, source_id, title, url, servings, kcal, tags_json, "
        "has_nutrition, meta_status, fetched_at, kcal_source) "
        "VALUES (7, 'respo', 's7', 'Stary', 'u', 2, 300, '[]', 1, 'rule', 'x', 'source')"
    )
    conn.commit()
    conn.close()
    assert len(_MIGRATIONS) == 8

    store = Store(path)

    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 8
    tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"plan", "plan_session", "plan_meal", "plan_undo"} <= tables
    assert check.execute("SELECT title FROM recipe WHERE id = 7").fetchone() == ("Stary",)
    check.close()
    assert store.profile(1) is not None and store.latest_weight(1) == 80
    assert store.plan(MON) is None
    # plan może odwoływać się do przepisu sprzed migracji
    store.save_plan(
        MON, "draft", 1, "{}", [NewPlanSession("lunch", 7, 0, 1)], [NewPlanMeal(0, "lunch", 0, 1.0, 1.0)]
    )
    assert _counts(path) == (1, 1, 1, 0)


def test_save_and_read_plan(tmp_path: Path) -> None:
    store, a, b = _store(tmp_path)
    params = json.dumps({"discovery": 1})
    plan_id = store.save_plan(MON, "draft", 42, params, *_content(a, b))
    plan = store.plan(MON)
    assert plan is not None
    assert (plan.id, plan.week_start, plan.status, plan.seed, plan.params_json) == (
        plan_id,
        MON,
        "draft",
        42,
        params,
    )
    assert plan.approved_at is None
    sessions = store.plan_sessions(plan_id)
    assert [(s.slot, s.recipe_id, s.start_day, s.days) for s in sessions] == [
        ("lunch", a, 0, 2),
        ("dinner", b, 1, 1),
    ]
    meals = store.plan_meals(plan_id)
    assert [(m.day, m.slot, m.session_id, m.p1_portions, m.p2_portions) for m in meals] == [
        (0, "lunch", sessions[0].id, 1.0, 1.4),
        (1, "dinner", sessions[1].id, 0.8, 1.2),
        (1, "lunch", sessions[0].id, 1.0, None),
    ]
    assert store.plan(date(2026, 11, 30)) is None


def test_second_plan_for_same_week_is_rejected(tmp_path: Path) -> None:
    store, a, b = _store(tmp_path)
    store.save_plan(MON, "draft", 1, "{}", *_content(a, b))
    with pytest.raises(sqlite3.IntegrityError):
        store.save_plan(MON, "draft", 2, "{}", [], [])
    assert _counts(tmp_path / "d.db")[:1] == (1,)


def test_failed_save_leaves_nothing_behind(tmp_path: Path) -> None:
    store, a, _ = _store(tmp_path)
    bad = [NewPlanSession("lunch", a, 0, 9)]  # days > 7 łamie CHECK
    with pytest.raises(sqlite3.IntegrityError):
        store.save_plan(MON, "draft", 1, "{}", bad, [])
    assert _counts(tmp_path / "d.db") == (0, 0, 0, 0)


def test_replace_contents_and_status(tmp_path: Path) -> None:
    store, a, b = _store(tmp_path)
    plan_id = store.save_plan(MON, "draft", 1, "{}", *_content(a, b))
    store.replace_plan_contents(
        plan_id, [NewPlanSession("breakfast", b, 2, 3)], [NewPlanMeal(2, "breakfast", 0, 1.1, 0.9)]
    )
    assert [s.recipe_id for s in store.plan_sessions(plan_id)] == [b]
    assert [(m.day, m.slot) for m in store.plan_meals(plan_id)] == [(2, "breakfast")]
    assert _counts(tmp_path / "d.db")[1:3] == (1, 1)
    assert store.set_plan_status(plan_id, "approved") is True
    plan = store.plan(MON)
    assert plan is not None and plan.status == "approved" and plan.approved_at is not None
    assert store.set_plan_status(plan_id, "draft") is True
    plan = store.plan(MON)
    assert plan is not None and plan.status == "draft" and plan.approved_at is None
    assert store.set_plan_status(999, "approved") is False


def test_deleting_plan_cascades(tmp_path: Path) -> None:
    store, a, b = _store(tmp_path)
    plan_id = store.save_plan(MON, "draft", 1, "{}", *_content(a, b))
    other = store.save_plan(date(2026, 11, 30), "draft", 1, "{}", *_content(a, b))
    store.save_undo(plan_id, "{}")
    assert _counts(tmp_path / "d.db") == (2, 4, 6, 1)
    store.delete_plan(plan_id)
    assert _counts(tmp_path / "d.db") == (1, 2, 3, 0)
    assert store.plan_meals(plan_id) == [] and store.plan_sessions(other) != []


def test_undo_is_single_step_and_consumed(tmp_path: Path) -> None:
    store, a, b = _store(tmp_path)
    plan_id = store.save_plan(MON, "draft", 1, "{}", *_content(a, b))
    assert store.pop_undo(plan_id) is None
    store.save_undo(plan_id, '{"v": 1}')
    store.save_undo(plan_id, '{"v": 2}')
    assert store.pop_undo(plan_id) == '{"v": 2}'
    assert store.pop_undo(plan_id) is None


def test_approved_recipe_history(tmp_path: Path) -> None:
    store, a, b = _store(tmp_path)
    store.save_plan(MON, "approved", 1, "{}", *_content(a, b))  # a: dni 0-1, b: dzień 1
    prev = date(2026, 11, 16)
    store.save_plan(prev, "approved", 1, "{}", [NewPlanSession("lunch", a, 5, 2)],
                    [NewPlanMeal(5, "lunch", 0, 1.0, 1.0), NewPlanMeal(6, "lunch", 0, 1.0, 1.0)])  # fmt: skip
    store.save_plan(date(2026, 11, 30), "draft", 1, "{}", *_content(a, b))  # szkic się nie liczy
    hist = store.approved_recipe_history(date(2026, 11, 1))
    assert hist == {a: date(2026, 11, 24), b: date(2026, 11, 24)}
    # `since` odcina starsze posiłki (włącznie)
    assert store.approved_recipe_history(date(2026, 11, 24)) == {a: date(2026, 11, 24), b: date(2026, 11, 24)}
    assert store.approved_recipe_history(date(2026, 11, 25)) == {}
