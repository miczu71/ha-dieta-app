"""E3c: migracja v4, kaskada wartości składników, statusy szacunku, CLI --validate (bez sieci)."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest

from dieta.estimate import load_nutrition_seed
from dieta.nutrition_lookup import (
    OFF_URL,
    OffClient,
    fill_ingredient_nutrition,
    finalize,
    from_table,
    lookup_assumed_grams,
    lookup_llm,
)
from dieta.pipeline import estimate_nutrition
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import _MIGRATIONS, RecipeMeta, Store

META = RecipeMeta("lunch", 4, True, "rule")
SEED = load_nutrition_seed()


def _recipe(
    source_id: str,
    lines: list[tuple[str, float | None, str | None]],
    *,
    kcal: float | None = None,
    servings: float | None = 2,
    kcal_100g: float | None = None,
    source: str = "respo",
) -> Recipe:
    return Recipe(
        source=source,
        source_id=source_id,
        title=f"Przepis {source_id}",
        url=source_id,
        image=None,
        servings=servings,
        kcal=kcal,
        protein=None,
        carbs=None,
        fat=None,
        minutes=10,
        tags=(),
        category_hint=None,
        ingredients=tuple(RawIngredient(f"{n} {a}", a, u, n) for n, a, u in lines),
        kcal_100g=kcal_100g,
    )


def _add(store: Store, recipe: Recipe) -> int:
    recipe_id = store.upsert_recipe(recipe, META)
    for ing in recipe.ingredients:
        store.link_recipe_ingredient(ing.name, ing.name, None)
    return recipe_id


def _row(store: Store, recipe_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(store._path)  # noqa: SLF001
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM recipe WHERE id = ?", (recipe_id,)).fetchone()  # type: ignore[no-any-return]
    finally:
        conn.close()


def _llm(values: dict[str, dict[str, object]]):  # type: ignore[no-untyped-def]
    """Fałszywe ``ask``: odpowiada wg nazwy z listy (format „i. nazwa”)."""

    calls: list[list[str]] = []

    def ask(names: list[str]) -> list[dict[str, object]] | None:
        calls.append(names)
        out = []
        for i, name in enumerate(names):
            if name in values:
                out.append({"i": i, "name": name, **values[name]})
        return out

    ask.calls = calls  # type: ignore[attr-defined]
    return ask


GOOD = {"kcal": 100, "protein": 10, "carbs": 10, "fat": 2.2, "piece_g": None, "confidence": "high"}


# --- migracja ---------------------------------------------------------------------------------------


def _v3_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:3], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute("INSERT INTO ingredient (id, name, durability) VALUES (1, 'ryż', 'dry')")
    for rid, kcal, has in ((1, 300.0, 1), (2, None, 0)):
        conn.execute(
            "INSERT INTO recipe (id, source, source_id, title, url, servings, kcal, tags_json, "
            "has_nutrition, meta_status, fetched_at) "
            "VALUES (?, 'respo', ?, ?, 'u', 2, ?, '[]', ?, 'rule', 'x')",
            (rid, f"s{rid}", f"T{rid}", kcal, has),
        )
        conn.execute(
            "INSERT INTO recipe_ingredient (recipe_id, pos, text, amount, unit, name, ingredient_id) "
            "VALUES (?, 0, 'ryż', 100, 'g', 'ryż', 1)",
            (rid,),
        )
    conn.commit()
    conn.close()


def test_migration_v3_to_v4_adds_columns_backfills_and_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "v3.db"
    _v3_db(path)
    store = Store(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS) == 7
    recipe_cols = {r[1] for r in conn.execute("PRAGMA table_info(recipe)")}
    assert {
        "kcal_source",
        "kcal_100g",
        "est_coverage",
        "est_status",
        "servings_estimated",
        "est_unknown_json",
        "est_assumed",
    } <= recipe_cols
    assert "grams_assumed" in {r[1] for r in conn.execute("PRAGMA table_info(recipe_ingredient)")}
    assert {r[1] for r in conn.execute("PRAGMA table_info(ingredient_nutrition)")} >= {
        "ingredient_id",
        "kcal",
        "piece_g",
        "source",
        "confidence",
        "status",
        "updated_at",
    }
    rows = dict(conn.execute("SELECT id, kcal_source FROM recipe").fetchall())
    assert rows == {1: "source", 2: None}
    assert conn.execute("SELECT kcal, has_nutrition, title FROM recipe WHERE id = 1").fetchone() == (
        300.0,
        1,
        "T1",
    )
    assert conn.execute("SELECT name, amount FROM recipe_ingredient WHERE recipe_id = 2").fetchone() == (
        "ryż",
        100.0,
    )
    assert store.ingredient_durability("ryż") == "dry"
    assert store.recipe(1, {}, frozenset()).kcal_source == "source"  # type: ignore[union-attr]
    conn.close()


def test_old_v3_queries_still_work_on_v4(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("ryż", 100, "g")], kcal=300))
    store.set_flag(rid, "liked")
    old_recipe_sql = (
        "SELECT r.id, r.source, r.url, r.title, r.image, r.servings, r.kcal, r.protein, r.carbs, r.fat, "
        "r.minutes, r.tags_json, r.meal_category, r.storage_days, r.freezable, r.has_nutrition, "
        "r.meta_status, COALESCE(f.liked, 0), COALESCE(f.dismissed, 0) "
        "FROM recipe r LEFT JOIN recipe_flag f ON f.recipe_id = r.id"
    )
    old_ing_sql = (
        "SELECT ri.recipe_id, ri.pos, ri.text, ri.amount, ri.unit, ri.name, ri.ingredient_id "
        "FROM recipe_ingredient ri ORDER BY ri.recipe_id, ri.pos"
    )
    conn = sqlite3.connect(tmp_path / "d.db")
    row = conn.execute(old_recipe_sql).fetchone()
    assert len(row) == 19 and row[6] == 300 and row[15] == 1 and row[17] == 1
    assert conn.execute(old_ing_sql).fetchone()[5] == "ryż"
    # stary zapis przepisu (INSERT bez nowych kolumn) też przechodzi
    conn.execute(
        "INSERT INTO recipe (source, source_id, title, url, tags_json, has_nutrition, meta_status, "
        "fetched_at) VALUES ('respo', 'z', 'T', 'u', '[]', 0, 'rule', 'x')"
    )
    conn.commit()
    conn.close()


# --- upsert przepisu ze źródła ---------------------------------------------------------------------


def test_upsert_sets_kcal_source_and_kcal_100g(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    a = store.upsert_recipe(_recipe("a", [("ryż", 1, "g")], kcal=250, kcal_100g=120), META)
    b = store.upsert_recipe(_recipe("b", [("ryż", 1, "g")], kcal=None, kcal_100g=90), META)
    ra, rb = store.recipe(a, {}, frozenset()), store.recipe(b, {}, frozenset())
    assert ra is not None and rb is not None
    assert (ra.kcal_source, ra.kcal_100g) == ("source", 120)
    assert (rb.kcal_source, rb.kcal_100g, rb.has_nutrition) == (None, 90, False)


def test_approved_estimate_survives_recrawl_without_kcal(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 4, "szt")]))
    estimate_nutrition(store, None, None)
    store.set_recipe_estimate_status(rid, "approved")
    before = _row(store, rid)
    assert before["kcal"] and before["has_nutrition"] == 1 and before["est_status"] == "approved"
    store.upsert_recipe(_recipe("a", [("banany", 4, "szt")], kcal=None, kcal_100g=88), META)
    after = _row(store, rid)
    for col in (
        "kcal",
        "protein",
        "carbs",
        "fat",
        "has_nutrition",
        "kcal_source",
        "est_status",
        "est_coverage",
    ):
        assert after[col] == before[col], col
    assert after["kcal_100g"] == 88
    # kolejny przebieg też go nie rusza
    store.save_recipe_estimate(
        rid,
        kcal=1,
        protein=1,
        carbs=1,
        fat=1,
        servings_estimated=False,
        coverage=1,
        status="auto",
        unknown=[],
        assumed=0,
    )
    assert _row(store, rid)["kcal"] == before["kcal"]
    assert store.recipes_to_estimate() == []


def test_recrawl_with_source_kcal_replaces_unapproved_estimate(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 4, "szt")]))
    estimate_nutrition(store, None, None)
    assert _row(store, rid)["kcal_source"] == "estimate"
    store.upsert_recipe(_recipe("a", [("banany", 4, "szt")], kcal=333), META)
    row = _row(store, rid)
    assert (row["kcal"], row["kcal_source"], row["est_status"]) == (333, "source", None)


def test_source_recipe_is_never_touched_by_estimate(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 4, "szt")], kcal=999))
    before = tuple(_row(store, rid))
    stats = estimate_nutrition(store, None, None)
    assert stats.recipes == 0
    assert tuple(_row(store, rid)) == before
    assert not store.save_recipe_estimate(
        rid,
        kcal=1,
        protein=1,
        carbs=1,
        fat=1,
        servings_estimated=False,
        coverage=1,
        status="auto",
        unknown=[],
        assumed=0,
    )
    assert tuple(_row(store, rid)) == before


# --- walidacja i kaskada ---------------------------------------------------------------------------


def test_finalize_validation() -> None:
    ok = finalize(100, 10, 10, 2.2, None, source="llm", confidence="high")
    assert (ok.confidence, ok.status) == ("high", "auto")
    for kcal, p, c, f in ((950, 0, 0, 105), (100, 50, 50, 50), (100, -1, 20, 7), (-5, 0, 0, 0)):
        bad = finalize(kcal, p, c, f, None, source="llm", confidence="high")
        assert (bad.confidence, bad.status) == ("low", "pending"), (kcal, p, c, f)
    low = finalize(100, 10, 10, 2.2, None, source="llm", confidence="low")
    assert (low.confidence, low.status) == ("low", "pending")
    # tolerancja: 15 kcal przy małych wartościach
    assert finalize(10, 0, 0, 2, None, source="llm", confidence="high").status == "auto"  # 18 vs 10


def test_table_step_alone() -> None:
    value = from_table("banany", SEED)
    assert (
        value is not None and value.source == "table" and (value.confidence, value.status) == ("high", "auto")
    )
    assert value.piece_g == 120
    assert from_table("zupełnie nieznany składnik xyz", SEED) is None


def _off_payload(rows: list[tuple[float | None, float, float, float]]) -> str:
    products = []
    for kcal, p, c, f in rows:
        n: dict[str, float] = {"proteins_100g": p, "carbohydrates_100g": c, "fat_100g": f}
        if kcal is not None:
            n["energy-kcal_100g"] = kcal
        products.append({"product_name": "x", "nutriments": n})
    return json.dumps({"products": products})


def test_off_step_accepts_median_and_builds_url() -> None:
    urls: list[str] = []
    rows = [(100, 10, 10, 2.2), (110, 11, 11, 2.4), (95, 9, 10, 2.0), (200, 1, 1, 1), (None, 1, 1, 1)]

    def fetch(url: str) -> str:
        urls.append(url)
        return _off_payload(rows)

    value = OffClient(fetch, sleep=lambda s: None).lookup("ser żółty")
    assert value is not None and value.source == "off" and value.status == "auto"
    assert value.kcal == 100  # mediana trzech w pasie (200 odpada, brak kcal odpada)
    assert urls[0].startswith(OFF_URL + "?search_terms=ser+%C5%BC%C3%B3%C5%82ty&countries_tags_en=poland")
    assert "page_size=5" in urls[0] and "fields=product_name%2Cnutriments" in urls[0]


def test_off_step_rejects_incomplete_or_scattered() -> None:
    scattered = [(100, 10, 10, 2.2), (300, 10, 10, 2.2), (500, 10, 10, 2.2), (700, 1, 1, 1), (50, 1, 1, 1)]
    few = [(100, 10, 10, 2.2), (100, 10, 10, 2.2), (None, 1, 1, 1), (None, 1, 1, 1), (None, 1, 1, 1)]
    for rows in (scattered, few):
        assert OffClient(lambda u, r=rows: _off_payload(r), sleep=lambda s: None).lookup("x") is None


def test_off_throttle_at_least_six_seconds() -> None:
    sleeps: list[float] = []
    now = [0.0]
    client = OffClient(lambda u: _off_payload([]), sleep=sleeps.append, clock=lambda: now[0])
    client.lookup("a")
    now[0] = 2.0
    client.lookup("b")
    assert sleeps == [pytest.approx(4.0)]
    now[0] = 100.0
    client.lookup("c")
    assert sleeps == [pytest.approx(4.0)]


def test_cascade_table_then_off_then_llm_and_rejected_off_falls_through() -> None:
    saved: dict[int, str] = {}
    good_off = _off_payload([(100, 10, 10, 2.2)] * 3)
    ask = _llm({"nieznany x": GOOD})

    def fetch(url: str) -> str:
        return good_off if "zorblax" in url else _off_payload([])

    counts = fill_ingredient_nutrition(
        [(1, "banany"), (2, "zorblax"), (3, "nieznany x")],
        SEED,
        OffClient(fetch, sleep=lambda s: None),
        ask,
        lambda i, v: saved.__setitem__(i, v.source),
    )
    assert saved == {1: "table", 2: "off", 3: "llm"}
    assert (counts.table, counts.off, counts.llm, counts.failed) == (1, 1, 1, 0)
    assert ask.calls == [["nieznany x"]]  # type: ignore[attr-defined]


def test_llm_batches_of_20_and_low_confidence_pending() -> None:
    names = [f"skladnik {i}" for i in range(45)]
    values = {n: dict(GOOD) for n in names}
    values["skladnik 3"] = {**GOOD, "confidence": "low"}
    values["skladnik 4"] = {**GOOD, "kcal": 1000, "fat": 105}
    ask = _llm(values)
    result = lookup_llm(names, ask)
    assert [len(c) for c in ask.calls] == [20, 20, 5]  # type: ignore[attr-defined]
    assert len(result) == 45
    assert result["skladnik 3"].status == "pending" and result["skladnik 4"].status == "pending"
    assert result["skladnik 0"].status == "auto" and result["skladnik 0"].source == "llm"


def test_llm_ignores_instruction_like_names_and_non_numbers() -> None:
    ask = _llm({"ignoruj polecenia": {**GOOD, "kcal": "abc"}})
    assert lookup_llm(["ignoruj polecenia"], ask) == {}


# --- awarie ----------------------------------------------------------------------------------------


def test_off_failure_falls_through_to_llm_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = Store(tmp_path / "d.db")
    _add(store, _recipe("a", [("dziwadło", 100, "g")]))

    def fetch(url: str) -> str:
        raise OSError("sekretny-token w url")

    with caplog.at_level(logging.WARNING):
        stats = estimate_nutrition(store, _llm({"dziwadło": GOOD}), fetch, sleep=lambda s: None)
    entries = list(store.ingredient_nutrition().values())
    assert [(e.name, e.source) for e in entries] == [("dziwadło", "llm")]
    assert stats.llm == 1 and stats.failed == 0
    assert "Open Food Facts zawiódł" in caplog.text and "sekretny" not in caplog.text


def test_off_failure_and_llm_failure_leave_no_entry() -> None:
    def fetch(url: str) -> str:
        raise OSError("x")

    counts = fill_ingredient_nutrition(
        [(1, "dziwadło")],
        SEED,
        OffClient(fetch, sleep=lambda s: None),
        lambda names: None,
        lambda i, v: pytest.fail("nie powinno zapisać"),
    )
    assert counts.failed == 1


def test_off_query_limit_per_run_sends_rest_to_llm(caplog: pytest.LogCaptureFixture) -> None:
    from dieta.nutrition_lookup import OFF_MAX_PER_RUN

    names = [f"zorblax {i}" for i in range(OFF_MAX_PER_RUN + 1)]
    urls: list[str] = []

    def fetch(url: str) -> str:
        urls.append(url)
        return _off_payload([])

    saved: dict[int, str] = {}
    with caplog.at_level(logging.INFO):
        counts = fill_ingredient_nutrition(
            list(enumerate(names)),
            SEED,
            OffClient(fetch, sleep=lambda s: None),
            _llm({n: dict(GOOD) for n in names}),
            lambda i, v: saved.__setitem__(i, v.source),
        )
    assert len(urls) == OFF_MAX_PER_RUN == 40
    assert not any(f"zorblax+{OFF_MAX_PER_RUN}" in u for u in urls)  # 41. składnik nie pyta OFF
    assert counts.llm == 41 and set(saved.values()) == {"llm"}
    assert "1 składników pominęło OFF" in caplog.text


def test_llm_failure_leaves_no_entry_and_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("dziwadło", 100, "g"), ("banany", 2, "szt")]))
    calls = [0]

    def ask(names: list[str]) -> list[dict[str, object]] | None:
        calls[0] += 1
        return None

    with caplog.at_level(logging.WARNING):
        stats = estimate_nutrition(store, ask, None)
    assert calls[0] == 2  # jedna ponowna próba
    assert "brak odpowiedzi LLM" in caplog.text
    assert [n.name for n in store.ingredient_nutrition().values()] == ["banany"]
    assert stats.failed == 1
    row = _row(store, rid)
    assert row["est_status"] == "pending" and json.loads(row["est_unknown_json"]) == ["dziwadło"]
    assert row["has_nutrition"] == 0
    # ponowna próba w następnym przebiegu
    assert [n for _, n in store.ingredients_needing_nutrition()] == ["dziwadło"]
    estimate_nutrition(store, _llm({"dziwadło": GOOD}), None)
    assert [n for _, n in store.ingredients_needing_nutrition()] == []
    assert _row(store, rid)["est_status"] == "auto"


def test_llm_exception_is_caught(caplog: pytest.LogCaptureFixture) -> None:
    def ask(names: list[str]) -> list[dict[str, object]] | None:
        raise TimeoutError("boom")

    with caplog.at_level(logging.WARNING):
        assert lookup_llm(["x"], ask) == {}
    assert "TimeoutError" in caplog.text


# --- statusy szacunku ------------------------------------------------------------------------------


def test_status_auto_full_coverage(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 4, "szt"), ("sól", None, None)], servings=2))
    estimate_nutrition(store, None, None)
    row = _row(store, rid)
    assert row["kcal_source"] == "estimate" and row["est_status"] == "auto" and row["has_nutrition"] == 1
    assert row["kcal"] == round(4 * 120 * 0.89 / 2) and row["est_coverage"] == pytest.approx(1.0)
    assert row["servings_estimated"] == 0 and row["est_assumed"] == 0
    assert json.loads(row["est_unknown_json"]) == []


def test_status_pending_low_coverage(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 1, "szt"), ("dziwadło", 200, "g")], servings=None))
    estimate_nutrition(store, None, None)
    row = _row(store, rid)
    assert row["est_status"] == "pending" and row["has_nutrition"] == 0
    assert row["est_coverage"] < 0.85 and row["servings_estimated"] == 1
    assert json.loads(row["est_unknown_json"]) == ["dziwadło"]
    assert row["kcal"] is not None  # częściowa suma jest zapisana, ale poza planerem


def test_status_auto_despite_pending_ingredient_when_coverage_ok(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("dziwadło", 100, "g")]))
    estimate_nutrition(store, _llm({"dziwadło": {**GOOD, "confidence": "low"}}), None)
    row = _row(store, rid)
    assert row["est_coverage"] == pytest.approx(1.0)
    assert row["est_status"] == "auto" and row["has_nutrition"] == 1
    assert [n.name for n in store.pending_ingredient_nutrition()] == ["dziwadło"]  # nadal w kolejce


def test_status_kcal_100g_consistency(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    ok = _add(store, _recipe("ok", [("banany", 4, "szt")], kcal_100g=95, source="aniagotuje"))
    bad = _add(store, _recipe("bad", [("banany", 4, "szt")], kcal_100g=200, source="aniagotuje"))
    estimate_nutrition(store, None, None)
    assert _row(store, ok)["est_status"] == "auto"
    assert _row(store, bad)["est_status"] == "pending"
    assert _row(store, bad)["kcal_100g"] == 200


def test_without_ask_no_silent_zero_and_no_lines_recipe_skipped(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("dziwadło", 100, "g")]))
    empty = _add(store, _recipe("e", []))
    estimate_nutrition(store, None, None)
    row = _row(store, rid)
    assert row["kcal"] is None and row["est_status"] == "pending" and row["est_coverage"] == 0
    assert store.ingredient_nutrition() == {}
    assert _row(store, empty)["est_status"] is None


def test_one_broken_recipe_does_not_stop_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = Store(tmp_path / "d.db")
    first = _add(store, _recipe("a", [("banany", 4, "szt")]))
    second = _add(store, _recipe("b", [("banany", 2, "szt")]))
    real = store.recipe_lines

    def flaky(recipe_id: int):  # type: ignore[no-untyped-def]
        if recipe_id == first:
            raise RuntimeError("zepsuty")
        return real(recipe_id)

    monkeypatch.setattr(store, "recipe_lines", flaky)
    with caplog.at_level(logging.WARNING):
        stats = estimate_nutrition(store, None, None)
    assert stats.errors == 1 and stats.recipes == 1
    assert _row(store, second)["est_status"] == "auto" and _row(store, first)["est_status"] is None
    assert "błąd przepisu" in caplog.text


# --- grams_assumed ---------------------------------------------------------------------------------


def test_grams_assumed_fills_lines_without_amount(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 2, "szt"), ("maliny", None, None), ("sól", None, None)]))

    def ask_grams(batch: list[str]) -> list[dict[str, object]] | None:
        assert batch == ["maliny None (przepis: Przepis a)"]  # sól jest „darmowa” i nie trafia do LLM
        return [{"i": 0, "grams": 150}]

    stats = estimate_nutrition(store, None, None, ask_grams=ask_grams)
    assert stats.grams == 1
    lines = {ln.name: ln for ln in store.recipe_lines(rid)}
    assert lines["maliny"].grams_assumed == 150 and lines["banany"].grams_assumed is None
    row = _row(store, rid)
    assert row["est_assumed"] == 1
    # 240 g całkowicie + 150 g × 0,5 → pokrycie = 0,5·150 + 240 / 390
    assert row["est_coverage"] == pytest.approx((240 + 75) / 390)
    assert row["est_status"] == "pending"


def test_grams_assumed_failure_keeps_line_unknown(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 2, "szt"), ("maliny", None, None)]))
    with caplog.at_level(logging.WARNING):
        stats = estimate_nutrition(store, None, None, ask_grams=lambda b: None)
    assert stats.grams == 0 and "Ilości domyślne" in caplog.text
    assert all(ln.grams_assumed is None for ln in store.recipe_lines(rid))
    assert json.loads(_row(store, rid)["est_unknown_json"]) == ["maliny"]


def test_lookup_assumed_grams_rejects_bad_values_and_batches() -> None:
    lines = [(f"l{i}", "T") for i in range(25)]
    seen: list[int] = []

    def ask(batch: list[str]) -> list[dict[str, object]] | None:
        seen.append(len(batch))
        return [
            {"i": 0, "grams": 50},
            {"i": 1, "grams": -3},
            {"i": 2, "grams": 99999},
            {"i": 3, "grams": None},
        ]

    assert lookup_assumed_grams(lines, ask) == {0: 50.0, 20: 50.0}
    assert seen == [20, 5]


# --- CLI -------------------------------------------------------------------------------------------


def test_cli_validate_reports_and_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from dieta.__main__ import main

    path = tmp_path / "d.db"
    store = Store(path)
    _add(store, _recipe("ok", [("banany", 4, "szt")], kcal=214))  # 4×120 g ×0,89 / 2 = 214
    _add(store, _recipe("zle", [("banany", 4, "szt")], kcal=1000, source="lidl"))
    _add(store, _recipe("est", [("banany", 4, "szt")]))  # bez kcal ze źródła: poza pomiarem
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert main(["estimate", "--validate", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Przepisy z kcal ze źródła: 2" in out
    assert "Pokrycie ≥ 0.85: 2 (100%)" in out
    assert "w ±30%: 1/2 (50%)" in out
    assert "respo: 1 przepisów" in out and "lidl: 1 przepisów" in out
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_cli_validate_old_v3_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from dieta.__main__ import main

    path = tmp_path / "v3.db"
    _v3_db(path)
    assert main(["estimate", "--validate", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Przepisy z kcal ze źródła: 1" in out  # has_nutrition=1 w bazie bez kcal_source
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 3  # CLI nie migruje
    conn.close()


def test_cli_validate_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from dieta.__main__ import main

    assert main(["estimate", "--validate", str(tmp_path / "brak.db")]) == 1
    assert "Brak pliku bazy" in capsys.readouterr().out
    assert not (tmp_path / "brak.db").exists()


# --- E3c poprawki po pomiarze NO-GO ------------------------------------------------------------------


def test_headers_and_noise_are_skipped(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(
        store,
        _recipe("a", [("banany", 2, "szt"), ("Ciasto:", None, None), ("tutaj", None, None), ("sos", 1, "g")]),
    )
    estimate_nutrition(store, None, None, ask_grams=lambda b: [])
    row = _row(store, rid)
    assert json.loads(row["est_unknown_json"]) == ["sos"]  # nagłówek i szum nie wchodzą do nieznanych
    assert row["est_coverage"] == pytest.approx(240 / 241)  # total = 2 linie (banany + sos)


def test_noise_lines_do_not_reach_llm(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    _add(
        store,
        _recipe("a", [("banany", 2, "szt"), ("Do podania:", None, None), ("produkt dostępny", None, None)]),
    )
    asked: list[list[str]] = []

    def ask_grams(batch: list[str]) -> list[dict[str, object]] | None:
        asked.append(batch)
        return []

    estimate_nutrition(store, None, None, ask_grams=ask_grams)
    assert asked == []


def test_unit_line_goes_through_ask_grams_as_assumed(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 2, "szt"), ("banany", 2, "pęczek")]))
    seen: list[list[str]] = []

    def ask_grams(batch: list[str]) -> list[dict[str, object]] | None:
        seen.append(batch)
        return [{"i": 0, "grams": 60}]

    stats = estimate_nutrition(store, None, None, ask_grams=ask_grams)
    assert seen == [["2 pęczek banany (przepis: Przepis a)"]]
    assert stats.grams == 1
    row = _row(store, rid)
    assert row["est_assumed"] == 1
    assert row["est_coverage"] == pytest.approx((240 + 30) / 300)


def test_assumed_cap_per_category() -> None:
    from dieta.estimate import assumed_cap, load_measures

    m = load_measures()
    assert assumed_cap("olej rzepakowy", m) == 30
    assert assumed_cap("pieprz cayenne", m) == 10
    assert assumed_cap("mąka pszenna", m) is None
    lines = [("olej rzepakowy", "T"), ("mąka", "T"), ("masło", "T")]
    ask = lambda b: [{"i": 0, "grams": 500}, {"i": 1, "grams": 120}, {"i": 2, "grams": 20}]  # noqa: E731
    out = lookup_assumed_grams(lines, ask, [30, None, 30])
    assert out == {0: 30.0, 1: 120.0, 2: 20.0}


def test_noise_words_missing_in_old_measures_file(tmp_path: Path) -> None:
    from dieta.estimate import load_measures

    data = json.loads(
        (Path(__file__).parents[1] / "src/dieta/seed/measures.json").read_text(encoding="utf-8")
    )
    data.pop("noise_words")
    data.pop("assumed_caps")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    m = load_measures(path)
    assert m.noise_words == () and m.assumed_caps == ()


def test_liquid_over_400_kcal_is_pending() -> None:
    ask = _llm(
        {
            "napar kawy": {"kcal": 355, "protein": 14, "carbs": 41, "fat": 14, "confidence": "high"},
            "sok mocny": {"kcal": 450, "protein": 10, "carbs": 90, "fat": 8, "confidence": "high"},
            "orzechy": {"kcal": 450, "protein": 10, "carbs": 90, "fat": 8, "confidence": "high"},
        }
    )
    out = lookup_llm(["sok mocny", "orzechy"], ask)
    assert (out["sok mocny"].confidence, out["sok mocny"].status) == ("low", "pending")
    assert (out["orzechy"].confidence, out["orzechy"].status) == ("high", "auto")


def test_new_seed_aliases() -> None:
    for name, canon in [
        ("ser wiejski", "serek wiejski"),
        ("chleba żytniego (razowego)", "chleb żytni razowy"),
        ("soku z limonki", "sok z cytryny"),
        ("czekolady białej", "czekolada biała"),
        ("czekolady mlecznej", "czekolada mleczna"),
        ("bułek grahamek", "bułka grahamka"),
        ("serek śmietankowy", "serek śmietankowy"),
        ("budyń waniliowy (proszek)", "budyń waniliowy (proszek)"),
        ("hummus", "hummus"),
        ("wino białe wytrawne", "wino białe"),
        ("piwo", "piwo"),
        ("lody waniliowe", "lody waniliowe"),
        ("napój sojowy", "napój sojowy"),
        ("odżywka białkowa", "odżywka białkowa"),
        ("kawa parzona", "napar kawy"),
        ("tonik", "tonik"),
        ("kostki lodu", "kostki lodu"),
        ("bagietka", "bagietka"),
    ]:
        found = SEED.find(name)
        assert found is not None and found[0] == canon, name
    assert SEED.find("czosnek")[1].unit_g["główka"] == 40  # type: ignore[index]
    assert SEED.find("natka pietruszki")[1].unit_g["pęczek"] == 30  # type: ignore[index]


def test_reset_llm_keeps_other_sources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from dieta.__main__ import main

    path = tmp_path / "d.db"
    store = Store(path)
    spec = {
        "a": ("llm", "auto"),
        "b": ("llm", "approved"),
        "c": ("manual", "auto"),
        "d": ("table", "auto"),
        "e": ("off", "auto"),
        "f": ("llm", "pending"),
    }
    ids = {}
    for n, (source, status) in spec.items():
        ids[n] = store.upsert_ingredient(n, None)
        store.save_ingredient_nutrition(
            ids[n],
            kcal=1,
            protein=0,
            carbs=0,
            fat=0,
            piece_g=None,
            source=source,  # type: ignore[arg-type]
            confidence="high",
            status=status,  # type: ignore[arg-type]
        )
    assert main(["estimate", "--reset-llm", str(path)]) == 0
    assert "Usunięto 1" in capsys.readouterr().out
    left = {r[0] for r in sqlite3.connect(path).execute("SELECT ingredient_id FROM ingredient_nutrition")}
    assert left == {ids[n] for n in "bcdef"}


def test_table_value_with_alcohol_is_auto_despite_macro_gap() -> None:
    from dieta.estimate import load_nutrition_seed
    from dieta.nutrition_lookup import from_table

    value = from_table("wino białe", load_nutrition_seed())
    assert value is not None
    assert value.status == "auto" and value.confidence == "high"


# --- poprawki po recenzji ---------------------------------------------------------------------------


def test_implausible_pending_value_counts_as_unknown(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 100, "g"), ("dziwadło", 100, "g")]))
    bad = {"kcal": 1200, "protein": 10, "carbs": 10, "fat": 130, "confidence": "high"}
    estimate_nutrition(store, _llm({"dziwadło": bad}), None)
    row = _row(store, rid)
    assert row["est_status"] == "pending" and row["est_coverage"] < 0.85
    assert json.loads(row["est_unknown_json"]) == ["dziwadło"]
    assert [n.name for n in store.pending_ingredient_nutrition()] == ["dziwadło"]


def test_implausible_zero_and_negative_pending_are_excluded(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("banany", 100, "g"), ("dziwadło", 100, "g"), ("zorblax", 100, "g")]))
    zeros = {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "confidence": "high"}
    neg = {"kcal": 100, "protein": -5, "carbs": 10, "fat": 2, "confidence": "high"}
    estimate_nutrition(store, _llm({"dziwadło": zeros, "zorblax": neg}), None)
    unknown = json.loads(_row(store, rid)["est_unknown_json"])
    assert sorted(unknown) == ["dziwadło", "zorblax"]


def test_finalize_all_zero_needs_name_outside_free_and_liquid() -> None:
    kw = {"source": "llm", "confidence": "high"}
    sus = finalize(0, 0, 0, 0, None, name="dziwadło", **kw)  # type: ignore[arg-type]
    assert (sus.confidence, sus.status) == ("low", "pending")
    for name in ("woda", "sok jabłkowy", "sól"):
        ok = finalize(0, 0, 0, 0, None, name=name, **kw)  # type: ignore[arg-type]
        assert ok.status == "auto", name
    assert finalize(0, 0, 0, 0, None, **kw).status == "auto"  # type: ignore[arg-type]
    table = finalize(0, 0, 0, 0, None, name="dziwadło", source="table", confidence="high")
    assert table.status == "auto"


def test_llm_zero_values_pending_via_lookup() -> None:
    zeros = {"kcal": 0, "protein": 0, "carbs": 0, "fat": 0, "confidence": "high"}
    out = lookup_llm(["dziwadło"], _llm({"dziwadło": zeros}))
    assert out["dziwadło"].status == "pending"


def test_by_index_rejects_shifted_numbering(caplog: pytest.LogCaptureFixture) -> None:
    def shifted(names: list[str]) -> list[dict[str, object]]:
        return [{"i": 1, "name": names[0], **GOOD}, {"i": 0, "name": "inny składnik", **GOOD}]

    with caplog.at_level(logging.WARNING):
        out = lookup_llm(["alfa", "beta"], shifted)
    assert out == {}
    assert "alfa" not in caplog.text and "beta" not in caplog.text


def test_by_index_without_name_field_still_accepted() -> None:
    out = lookup_llm(["alfa"], lambda names: [{"i": 0, **GOOD}])
    assert out["alfa"].status == "auto"


def test_assumed_grams_rejects_name_mismatch() -> None:
    def ask(batch: list[str]) -> list[dict[str, object]]:
        return [{"i": 0, "name": "zupełnie coś innego", "grams": 50}, {"i": 1, "grams": 20}]

    out = lookup_assumed_grams([("a", "T"), ("b", "T")], ask)
    assert out == {1: 20.0}


def test_off_failure_logs_message_without_query_string(caplog: pytest.LogCaptureFixture) -> None:
    from dieta.fetcher import FetchError

    def fetch(url: str) -> str:
        raise FetchError("błąd http://x/api?token=sekret")

    with caplog.at_level(logging.WARNING):
        fill_ingredient_nutrition(
            [(1, "dziwadło")], SEED, OffClient(fetch, sleep=lambda s: None), None, lambda i, v: None
        )
    assert "FetchError: błąd http://x/api" in caplog.text and "sekret" not in caplog.text


def test_estimate_counters_only_when_saved(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    _add(store, _recipe("a", [("banany", 4, "szt")]))
    store.save_recipe_estimate = lambda *a, **k: False  # type: ignore[method-assign]
    stats = estimate_nutrition(store, None, None)
    assert (stats.recipes, stats.auto, stats.pending) == (0, 0, 0)


def _nut(store: Store, ing: int, kcal: float, source: str, status: str) -> None:
    store.save_ingredient_nutrition(
        ing,
        kcal=kcal,
        protein=1.0,
        carbs=1.0,
        fat=1.0,
        piece_g=None,
        source=source,  # type: ignore[arg-type]
        confidence="high",
        status=status,  # type: ignore[arg-type]
    )


def test_auto_save_does_not_overwrite_manual_or_approved(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    ing = store.upsert_ingredient("dziwadło", None)
    _nut(store, ing, 100, "manual", "approved")
    _nut(store, ing, 500, "llm", "auto")
    assert store.ingredient_nutrition()[ing].kcal == 100
    _nut(store, ing, 600, "off", "pending")
    assert store.ingredient_nutrition()[ing].kcal == 100
    _nut(store, ing, 200, "manual", "approved")
    assert store.ingredient_nutrition()[ing].kcal == 200


def test_kcal_100g_zero_means_no_check_and_lines_without_amount_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    zero = _add(store, _recipe("z", [("banany", 4, "szt")], kcal_100g=0, source="aniagotuje"))
    noamt = _add(store, _recipe("n", [("sól", None, None)], kcal_100g=100, source="aniagotuje"))
    estimate_nutrition(store, None, None)
    assert _row(store, zero)["est_status"] == "auto"
    assert _row(store, noamt)["est_status"] == "pending"


def test_dismissed_pending_estimate_not_counted(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(store, _recipe("a", [("dziwadło", 100, "g")]))
    estimate_nutrition(store, None, None)
    assert store.count_pending_estimates() == 1
    store.set_flag(rid, "dismissed", True)
    assert store.count_pending_estimates() == 0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pomidory suszone", "pomidory suszone"),
        ("mleko w proszku", "mleko w proszku"),
        ("mleko skondensowane", "mleko skondensowane"),
        ("mleko migdałowe", "mleko migdałowe"),
        ("papryka słodka mielona", "papryka słodka mielona"),
        ("mąka migdałowa", "mąka migdałowa"),
        ("mąka kukurydziana", "mąka kukurydziana"),
        ("pomidory", "pomidory"),
        ("mleko", "mleko"),
        ("mleko 2% 200 ml", "mleko"),
        ("mięso mielone wieprzowe", "mięso mielone wołowe"),
        ("czosnek mielony", "czosnek"),
        ("imbir mielony", "imbir"),
        ("słodka papryka", "papryka czerwona"),
    ],
)
def test_seed_qualifier_does_not_match_fresh_counterpart(name: str, expected: str) -> None:
    found = SEED.find(name)
    assert found is not None and found[0] == expected


@pytest.mark.parametrize("name", ["mleko sojowe w proszku"])
def test_seed_qualifier_fallback_returns_none(name: str) -> None:
    assert SEED.find(name) is None


def test_kcal_100g_check_skipped_when_free_line_with_amount(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = _add(
        store,
        _recipe("z", [("woda", 2000, "ml"), ("banany", 300, "g")], kcal_100g=40, source="aniagotuje"),
    )
    estimate_nutrition(store, None, None)
    assert _row(store, rid)["est_status"] == "auto"


def test_off_circuit_breaker_after_three_failures(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        raise OSError("x")

    names = [(i, f"dziwadło {i}") for i in range(6)]
    ask = _llm({n: GOOD for _, n in names})
    saved: list[int] = []
    with caplog.at_level(logging.WARNING):
        counts = fill_ingredient_nutrition(
            names, SEED, OffClient(fetch, sleep=lambda s: None), ask, lambda i, v: saved.append(i)
        )
    assert len(calls) == 3 and counts.llm == 6
    assert caplog.text.count("OFF niedostępny, pomijam do końca przebiegu") == 1
    assert caplog.text.count("Open Food Facts zawiódł") == 2
