import sqlite3
from datetime import date
from pathlib import Path

from dieta.profile import IngredientStat
from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import _MIGRATIONS, Profile, RecipeMeta, Store
from dieta.sync import map_recipe_ingredients

TODAY = date(2026, 11, 22)
META = RecipeMeta("lunch", 4, True, "rule")


def _recipe(
    source_id: str = "u1", title: str = "Hummus", kcal: float | None = 300.0, names=("ryż",)
) -> Recipe:  # type: ignore[no-untyped-def]
    return Recipe(
        source="respo",
        source_id=source_id,
        title=title,
        url=source_id,
        image=None,
        servings=2,
        kcal=kcal,
        protein=10,
        carbs=30,
        fat=5,
        minutes=20,
        tags=("a", "b"),
        category_hint=None,
        ingredients=tuple(RawIngredient(n, None, None, n) for n in names),
    )


def _stat(name: str) -> IngredientStat:
    return IngredientStat(name, 1, 1.0, TODAY, 1.0, True, "dry")


def test_migration_v2_to_v3_keeps_data(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:2], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute("INSERT INTO profile VALUES (1,'m',30,180,2,'lose',0.5,NULL,NULL,0.3)")
    conn.execute("INSERT INTO ingredient (name, durability) VALUES ('ryż','dry')")
    conn.execute("INSERT INTO meta VALUES ('last_sync_at','2026-11-21')")
    conn.commit()
    conn.close()

    store = Store(path)
    profile = store.profile(1)
    assert isinstance(profile, Profile) and profile.age == 30
    assert store.ingredient_durability("ryż") == "dry"
    assert store.get_meta("last_sync_at") == "2026-11-21"
    assert sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS) == 4
    assert store.recipes({}, frozenset()) == []


def test_upsert_and_has_nutrition(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    a = store.upsert_recipe(_recipe("a", kcal=None), META)
    b = store.upsert_recipe(_recipe("b"), META)
    rows = {r.id: r for r in store.recipes({}, frozenset())}
    assert not rows[a].has_nutrition and rows[b].has_nutrition
    assert rows[b].tags == ("a", "b") and rows[b].freezable is True
    assert store.upsert_recipe(_recipe("a", kcal=250), META) == a
    assert store.recipe(a, {}, frozenset()).has_nutrition  # type: ignore[union-attr]


def test_flags_filters_and_sort_by_familiarity(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    low = store.upsert_recipe(_recipe("l", "A niska", names=("ryż", "tahini")), META)
    high = store.upsert_recipe(_recipe("h", "Z wysoka", names=("ryż",)), META)
    inna = store.upsert_recipe(_recipe("x", "Inna", names=("ryż",)), RecipeMeta("dinner", 2, False, "rule"))
    for name in ("ryż", "tahini"):
        for raw in ("ryż", "tahini"):
            if raw == name:
                store.link_recipe_ingredient(raw, name, None)
    stats = {"ryż": _stat("ryż")}
    rows = store.recipes(stats, frozenset())
    assert [r.id for r in rows][-1] == low
    assert [r.title for r in rows[:2]] == ["Inna", "Z wysoka"]
    assert [r.id for r in store.recipes(stats, frozenset(), min_familiarity=0.7)] == [r.id for r in rows[:2]]
    assert low not in [r.id for r in store.recipes(stats, frozenset(), min_familiarity=0.7)]
    assert {r.id for r in store.recipes(stats, frozenset(), category="dinner")} == {inna}
    store.set_flag(high, "liked")
    assert [r.id for r in store.recipes(stats, frozenset(), liked_only=True)] == [high]
    store.set_flag(high, "dismissed")
    assert high not in [r.id for r in store.recipes(stats, frozenset())]
    assert high in [r.id for r in store.recipes(stats, frozenset(), show_dismissed=True)]
    store.set_flag(high, "dismissed", False)
    assert store.recipe(high, stats, frozenset()).liked  # type: ignore[union-attr]


def test_approve_meta_and_pending_queue(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = store.upsert_recipe(_recipe(), RecipeMeta(None, None, None, "pending"))
    assert [p.id for p in store.pending_recipe_meta()] == [rid]
    store.approve_recipe_meta(rid, "dinner", 3, False)
    assert store.pending_recipe_meta() == []
    row = store.recipe(rid, {}, frozenset())
    assert row is not None and (row.meal_category, row.storage_days, row.freezable) == ("dinner", 3, False)


def test_recipe_ingredients_mapped_via_rules_ai_and_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = store.upsert_recipe(_recipe(names=("Ryż basmati", "tahini", "zagadka")), META)
    asked: list[list[str]] = []

    def ask(names: list[str]) -> list[dict[str, object]] | None:
        asked.append(names)
        return [
            {"i": 0, "klasa": "skladnik", "skladnik": "tahini", "trwalosc": "dry"},
            {"i": 1, "klasa": "bzdura", "skladnik": "", "trwalosc": ""},
        ]

    assert map_recipe_ingredients(store, ask) == 2
    assert asked == [["tahini", "zagadka"]]
    assert store.recipe_ingredient_names(rid) == [
        ("Ryż basmati", "ryż basmati"),
        ("tahini", "tahini"),
        ("zagadka", None),
    ]
    assert store.ingredient_durability("tahini") == "dry"
    # ponowne wywołanie nie pyta AI o to samo (zagadka = pending, nieznany)
    assert map_recipe_ingredients(store, ask) == 0
    assert len(asked) == 1
    assert store.recipe(rid, {"ryż": _stat("ryż")}, frozenset()).familiarity.total == 3  # type: ignore[union-attr]


def test_ingredients_without_ask_are_not_marked_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_recipe(_recipe(names=("zagadka",)), META)
    assert map_recipe_ingredients(store, None) == 0
    assert "ing:zagadka" not in store.mapped_codes()
    assert Settings(data_dir=tmp_path, log_level="info", dev=True).sources
