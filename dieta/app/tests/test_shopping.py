from datetime import date, timedelta
from pathlib import Path

import pytest

from dieta.estimate import SERVING_G
from dieta.settings import Settings
from dieta.shopping import plan_cost, shopping_list
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import NewPlanMeal, NewPlanSession, RecipeMeta, Store
from dieta.zakupy_client import Purchase

WEEK = date(2026, 11, 23)  # poniedziałek
TODAY = date(2026, 11, 23)


def _recipe(sid: str, servings: float | None, items: tuple[tuple[float, str | None, str], ...]) -> Recipe:
    return Recipe(
        source="respo",
        source_id=sid,
        title=f"Przepis {sid}",
        url=f"https://example.org/{sid}",
        image=None,
        servings=servings,
        kcal=400.0,
        protein=25,
        carbs=40,
        fat=12,
        minutes=30,
        tags=(),
        category_hint=None,
        ingredients=tuple(RawIngredient(f"{a:g} {u or ''} {n}".strip(), a, u, n) for a, u, n in items),
    )


def _buy(
    code: str, names: tuple[str, ...], unit_price: float | None, is_weight: bool | None, last: date
) -> Purchase:
    return Purchase(
        code=code,
        chain="lidl",
        name=names[0],
        names=names,
        purchases=1,
        spend=10.0,
        first=last,
        last=last,
        dates=(last,),
        category="food",
        unit_price=unit_price,
        is_weight=is_weight,
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, log_level="info", dev=True)


def _setup(
    tmp_path: Path, status: str = "approved", prices: bool = True
) -> tuple[Store, Settings, dict[str, int]]:
    """Dwie sesje: R1 (2 porcje; 3 dni po 1+1 = skala 3) i R2 (4 porcje; 1 dzień 1+1 = skala 0,5).

    Kurczak 20 zł/kg, ryż 3,20 zł / 400 g (kupiony 5 dni temu → „w domu"), cebula bez ceny.
    """
    settings = _settings(tmp_path)
    store = Store(settings.db_path)
    meta = RecipeMeta("lunch", 3, True, "rule")
    r1 = store.upsert_recipe(
        _recipe(
            "r1", 2, ((400, "g", "kurczak"), (200, "g", "ryż"), (2, "szt", "cebula"), (1, "łyżeczka", "sól"))
        ),
        meta,
    )
    r2 = store.upsert_recipe(_recipe("r2", 4, ((300, "g", "kurczak"), (1, "szt", "cebula"))), meta)
    ids = {}
    for raw, durability in (("kurczak", "fresh"), ("ryż", "dry"), ("cebula", "fresh"), ("sól", "dry")):
        store.link_recipe_ingredient(raw, raw, durability)  # type: ignore[arg-type]
        ids[raw] = store.upsert_ingredient(raw, None)
    store.save_ingredient_nutrition(
        ids["cebula"], kcal=40, protein=1, carbs=9, fat=0.1, piece_g=100.0,
        source="table", confidence="high", status="auto",
    )  # fmt: skip
    k_price, r_price = (20.0, 3.2) if prices else (None, None)
    store.replace_purchases(
        [
            _buy("k1", ("Pierś z kurczaka",), k_price, True if prices else None, TODAY - timedelta(days=20)),
            _buy("r1", ("Ryż biały 400g",), r_price, False if prices else None, TODAY - timedelta(days=5)),
        ]
    )
    store.set_mapping("k1", "approved", "kurczak", "fresh", "x")
    store.set_mapping("r1", "approved", "ryż", "dry", "x")
    sessions = [NewPlanSession("lunch", r1, 0, 3), NewPlanSession("lunch", r2, 3, 1)]
    meals = [NewPlanMeal(d, "lunch", 0, 1.0, 1.0) for d in range(3)] + [NewPlanMeal(3, "lunch", 1, 1.0, 1.0)]
    store.save_plan(WEEK, status, 1, "{}", sessions, meals)  # type: ignore[arg-type]
    return store, settings, ids


def _by_name(lines: tuple) -> dict:  # type: ignore[type-arg]
    return {ln.name: ln for ln in lines}


def test_list_merges_sessions_scales_and_splits_sections(tmp_path: Path) -> None:
    store, settings, ids = _setup(tmp_path)
    result = shopping_list(store, settings, WEEK, TODAY)
    assert result.notice == "ok" and result.week_start == WEEK
    [kurczak] = result.to_buy
    # 400 g × 3 (R1: 6 porcji / 2) + 300 g × 0,5 (R2: 2 porcje / 4)
    assert kurczak.name == "kurczak" and kurczak.ingredient_id == ids["kurczak"]
    assert kurczak.grams == pytest.approx(1350.0)
    assert kurczak.priced and kurczak.cost == pytest.approx(27.0)
    assert kurczak.pieces is None and kurczak.pack_g is None and not kurczak.at_home
    [ryz] = result.at_home
    assert ryz.name == "ryż" and ryz.grams == pytest.approx(600.0) and ryz.at_home
    assert ryz.pack_g == 400.0 and ryz.packs == 2  # ≈ 2 op. po 400 g
    [cebula] = result.unpriced
    assert cebula.grams == pytest.approx(650.0) and cebula.pieces == pytest.approx(6.5)
    assert cebula.cost is None and not cebula.priced
    assert all(ln.name != "sól" for ln in (*result.to_buy, *result.at_home, *result.unpriced))


def test_list_cost_and_coverage_exclude_at_home_and_count_unpriced(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    result = shopping_list(store, settings, WEEK, TODAY)
    assert result.cost_total == pytest.approx(27.0)
    assert result.coverage == pytest.approx(1350 / (1350 + 650))


def test_force_moves_at_home_item_to_buy(tmp_path: Path) -> None:
    store, settings, ids = _setup(tmp_path)
    store.set_shopping_state(WEEK.isoformat(), ids["ryż"], "force")
    result = shopping_list(store, settings, WEEK, TODAY)
    assert result.at_home == ()
    assert [ln.name for ln in result.to_buy] == ["kurczak", "ryż"]
    assert result.cost_total == pytest.approx(27.0 + 4.8)
    assert result.coverage == pytest.approx(1950 / 2600)


def test_checked_stays_on_list(tmp_path: Path) -> None:
    store, settings, ids = _setup(tmp_path)
    store.set_shopping_state(WEEK.isoformat(), ids["kurczak"], "checked")
    result = shopping_list(store, settings, WEEK, TODAY)
    assert [ln.name for ln in result.to_buy] == ["kurczak"]


def test_missing_servings_use_estimated_servings(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    r3 = store.upsert_recipe(
        _recipe("r3", None, ((300, "g", "kurczak"),)), RecipeMeta("lunch", 3, True, "rule")
    )
    store.link_recipe_ingredient("kurczak", "kurczak", "fresh")
    store.delete_plan(store.plan(WEEK).id)  # type: ignore[union-attr]
    store.save_plan(
        WEEK, "approved", 1, "{}", [NewPlanSession("lunch", r3, 0, 2)],
        [NewPlanMeal(0, "lunch", 0, 1.0, 1.0), NewPlanMeal(1, "lunch", 0, 1.0, 1.0)],
    )  # fmt: skip
    servings = max(1.0, 300 / SERVING_G)
    [kurczak] = shopping_list(store, settings, WEEK, TODAY).to_buy
    assert kurczak.grams == pytest.approx(300 * 4 / servings)


def test_notices_for_missing_draft_and_past_week(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path, status="draft")
    assert shopping_list(store, settings, WEEK + timedelta(days=7), TODAY).notice == "no_plan"
    draft = shopping_list(store, settings, WEEK, TODAY)
    assert draft.notice == "not_approved" and draft.to_buy == ()
    store.set_plan_status(store.plan(WEEK).id, "approved")  # type: ignore[union-attr]
    past = shopping_list(store, settings, WEEK, WEEK + timedelta(days=9))
    assert past.notice == "past_week" and past.to_buy == () and past.unpriced == ()


def test_current_week_skips_already_cooked_sessions(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    result = shopping_list(store, settings, WEEK, WEEK + timedelta(days=3))  # czwartek
    assert result.notice == "ok"
    [kurczak] = result.to_buy
    assert kurczak.grams == pytest.approx(150.0)  # tylko R2


def test_future_week_includes_all_sessions(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    result = shopping_list(store, settings, WEEK, WEEK - timedelta(days=10))
    assert _by_name(result.to_buy)["kurczak"].grams == pytest.approx(1350.0)


def test_no_prices_notice_when_all_prices_none(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path, prices=False)
    result = shopping_list(store, settings, WEEK, TODAY)
    assert result.notice == "no_prices"
    assert result.to_buy == () and result.cost_total == 0.0 and result.coverage == 0.0
    assert {ln.name for ln in result.unpriced} == {"kurczak", "cebula"}  # ryż jest „w domu"
    assert [ln.name for ln in result.at_home] == ["ryż"]


def test_plan_cost_per_session_total_and_coverage(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path)
    result = plan_cost(store, settings, WEEK)
    s1, s2 = result.sessions
    assert s1.cost == pytest.approx(24.0 + 4.8) and s1.per_portion == pytest.approx(28.8 / 6)
    assert s1.coverage == pytest.approx(1800 / 2400) and s1.title == "Przepis r1"
    assert s2.cost == pytest.approx(3.0) and s2.per_portion == pytest.approx(1.5)
    assert result.total == pytest.approx(31.8) and result.coverage == pytest.approx(0.75)


def test_plan_cost_works_for_draft_and_missing_plan(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path, status="draft")
    assert plan_cost(store, settings, WEEK).total == pytest.approx(31.8)
    empty = plan_cost(store, settings, WEEK + timedelta(days=7))
    assert (empty.total, empty.coverage, empty.sessions) == (0.0, 0.0, ())


def test_plan_cost_without_prices_has_zero_coverage(tmp_path: Path) -> None:
    store, settings, _ = _setup(tmp_path, prices=False)
    result = plan_cost(store, settings, WEEK)
    assert result.total == 0.0 and result.coverage == 0.0
    assert all(s.cost == 0.0 and s.coverage == 0.0 for s in result.sessions)
