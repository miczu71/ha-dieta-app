import sqlite3
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from dieta.pipeline import estimate_nutrition
from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import RecipeMeta, Store
from dieta.web.app import create_app

TODAY = date.today()


def _rec(sid: str, title: str, kcal: float | None, kcal_100g: float | None = None) -> Recipe:
    return Recipe(
        source="respo",
        source_id=sid,
        title=title,
        url=f"https://example.org/{sid}",
        image=None,
        servings=2,
        kcal=kcal,
        protein=25 if kcal is not None else None,
        carbs=40 if kcal is not None else None,
        fat=12 if kcal is not None else None,
        minutes=30,
        tags=(),
        category_hint=None,
        ingredients=(RawIngredient("100 g tahini", 100, "g", "tahini"),),
        kcal_100g=kcal_100g,
    )


def _client(tmp_path: Path) -> tuple[TestClient, Store, Settings]:
    s = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(s.db_path)
    app = create_app(s, sync_runner=lambda: 0, crawl_runner=lambda src: {})
    return TestClient(app, follow_redirects=False), store, s


def _estimate(
    store: Store, rid: int, *, kcal: float | None, status: str, coverage: float, **kw: object
) -> None:
    store.save_recipe_estimate(
        rid,
        kcal=kcal,
        protein=None if kcal is None else 20.0,
        carbs=None if kcal is None else 30.0,
        fat=None if kcal is None else 10.0,
        servings_estimated=bool(kw.get("servings_estimated", False)),
        coverage=coverage,
        status=status,  # type: ignore[arg-type]
        unknown=kw.get("unknown", ()),  # type: ignore[arg-type]
        assumed=int(kw.get("assumed", 0)),  # type: ignore[call-overload]
    )


def _scene(store: Store) -> dict[str, int]:
    meta = RecipeMeta("lunch", 3, True, "rule")
    ids = {
        "zrodlo": store.upsert_recipe(_rec("z", "Zupa Alfa", 400.0), meta),
        "auto": store.upsert_recipe(_rec("a", "Gulasz Beta", None), meta),
        "approved": store.upsert_recipe(_rec("p", "Pierogi Gamma", None), meta),
        "pending": store.upsert_recipe(_rec("n", "Salatka Delta", None), meta),
        "pending0": store.upsert_recipe(_rec("n0", "Kasza Epsilon", None), meta),
        "brak": store.upsert_recipe(_rec("b", "Omlet Zeta", None), meta),
    }
    _estimate(
        store,
        ids["auto"],
        kcal=420.0,
        status="auto",
        coverage=0.9,
        servings_estimated=True,
        unknown=["tahini"],
        assumed=2,
    )
    _estimate(store, ids["approved"], kcal=310.0, status="approved", coverage=0.95)
    _estimate(store, ids["pending"], kcal=300.0, status="pending", coverage=0.6, unknown=["ser"])
    _estimate(store, ids["pending0"], kcal=None, status="pending", coverage=0.0, unknown=["ser"])
    return ids


def test_kcal_badges_per_state(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    _scene(store)
    text = client.get("/katalog?prog=0").text
    assert "~420 kcal (szacunek)" in text
    assert "~310 kcal (szacunek)" in text
    assert "~300 kcal (niepełny szacunek, pokrycie 60%)" in text
    assert "~300 kcal (szacunek)" not in text
    assert "brak szacunku" in text
    assert "brak kcal" in text
    assert "porcje szacowane" in text
    assert "niepełny szacunek" in text
    assert "ilości zgadywane (2)" in text


def test_card_shows_coverage_and_unknown(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _scene(store)
    auto = client.get(f"/katalog/{ids['auto']}").text
    assert "~420 kcal (szacunek)" in auto
    assert "pokrycie 90%" in auto
    assert "nieznane składniki: tahini" in auto
    assert "porcje szacowane" in auto and "ilości zgadywane (2)" in auto
    pend = client.get(f"/katalog/{ids['pending']}").text
    assert "~300 kcal (niepełny szacunek, pokrycie 60%)" in pend
    assert "~300 kcal (szacunek)" not in pend
    assert "nieznane składniki: ser" in pend
    assert "brak szacunku" in client.get(f"/katalog/{ids['pending0']}").text


def test_source_recipe_unchanged(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _scene(store)
    text = client.get(f"/katalog/{ids['zrodlo']}").text
    assert "(szacunek)" not in text and "niepełny" not in text
    assert "<b>400</b> kcal" in text


def test_kcal_filter_modes(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    _scene(store)

    def shown(mode: str) -> set[str]:
        text = client.get(f"/katalog?prog=0&kcal={mode}").text
        names = ("Zupa Alfa", "Gulasz Beta", "Pierogi Gamma", "Salatka Delta", "Kasza Epsilon", "Omlet Zeta")
        return {n for n in names if n in text}

    everything = {"Zupa Alfa", "Gulasz Beta", "Pierogi Gamma", "Salatka Delta", "Kasza Epsilon", "Omlet Zeta"}
    assert shown("wszystkie") == everything
    assert shown("") == everything
    assert shown("smieci") == everything
    assert shown("zrodlo") == {"Zupa Alfa"}
    assert shown("szacunek") == {"Gulasz Beta", "Pierogi Gamma"}
    assert shown("niepelny") == {"Salatka Delta", "Kasza Epsilon"}
    text = client.get("/katalog?prog=0&kcal=szacunek&kat=lunch").text
    assert 'value="szacunek" selected' in text


def test_filter_state_kept_in_flag_posts(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _scene(store)
    r = client.post(f"/katalog/{ids['zrodlo']}/serce", data={"qs": "prog=0&kcal=zrodlo"})
    assert r.headers["location"] == "../../katalog?kcal=zrodlo&prog=0"


def test_queue_shows_pending_sections(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    _scene(store)
    ing = store.upsert_ingredient("tahini", None)
    store.save_ingredient_nutrition(
        ing,
        kcal=950.0,
        protein=10.0,
        carbs=10.0,
        fat=10.0,
        piece_g=None,
        source="llm",
        confidence="low",
        status="pending",
    )
    text = client.get("/kupujemy/zatwierdz").text
    assert "zatwierdz-skladnik" in text and "zatwierdz-szacunek" in text
    assert "Salatka Delta" in text and "Kasza Epsilon" in text
    assert "Zatwierdź szacunek" in text  # Salatka ma kcal
    assert text.count("Zatwierdź szacunek") == 1  # Kasza bez kcal nie ma przycisku


def test_ingredient_validation_rejects_kcal_over_900(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ing = store.upsert_ingredient("tahini", None)
    store.save_ingredient_nutrition(
        ing,
        kcal=950.0,
        protein=10.0,
        carbs=10.0,
        fat=10.0,
        piece_g=None,
        source="llm",
        confidence="low",
        status="pending",
    )
    bad = client.post(
        "/kupujemy/zatwierdz-skladnik",
        data={"ingredient_id": ing, "kcal": "950", "protein": "1", "carbs": "1", "fat": "1"},
    )
    assert bad.status_code == 303 and bad.headers["location"].endswith("error=odzywcze")
    neg = client.post(
        "/kupujemy/zatwierdz-skladnik",
        data={"ingredient_id": ing, "kcal": "-1", "protein": "1", "carbs": "1", "fat": "1"},
    )
    assert neg.headers["location"].endswith("error=odzywcze")
    assert store.ingredient_nutrition()[ing].status == "pending"


def test_ingredient_approval_recalculates_recipe(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    rid = store.upsert_recipe(_rec("t", "Hummus Eta", None), RecipeMeta("lunch", 3, True, "rule"))
    store.link_recipe_ingredient("tahini", "tahini", None)
    ing_id = store.upsert_ingredient("tahini", None)
    store.save_ingredient_nutrition(
        ing_id,
        kcal=600.0,
        protein=17.0,
        carbs=21.0,
        fat=54.0,
        piece_g=None,
        source="llm",
        confidence="low",
        status="pending",
    )
    estimate_nutrition(store, None, None)
    # pokrycie pełne → auto mimo składnika w kolejce; zatwierdzenie składnika nadal przelicza przepis
    assert store.recipe(rid, {}, frozenset()).est_status == "auto"  # type: ignore[union-attr]
    assert store.ingredient_nutrition()[ing_id].status == "pending"

    r = client.post(
        "/kupujemy/zatwierdz-skladnik",
        data={"ingredient_id": ing_id, "kcal": "600", "protein": "17", "carbs": "21", "fat": "54"},
    )
    assert r.status_code == 303 and r.headers["location"] == "zatwierdz"
    assert store.ingredient_nutrition()[ing_id].status == "approved"
    assert store.ingredient_nutrition()[ing_id].source == "manual"
    recipe = store.recipe(rid, {}, frozenset())
    assert recipe is not None and recipe.kcal_source == "estimate"
    assert recipe.est_status == "auto"  # 100 g tahini, masa pełna, składnik zatwierdzony
    assert recipe.has_nutrition


def test_estimate_approve_and_dismiss(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _scene(store)
    r = client.post("/kupujemy/zatwierdz-szacunek", data={"recipe_id": ids["pending"], "action": "approve"})
    assert r.headers["location"] == "zatwierdz"
    row = store.recipe(ids["pending"], {}, frozenset())
    assert row is not None and row.est_status == "approved" and row.has_nutrition

    client.post("/kupujemy/zatwierdz-szacunek", data={"recipe_id": ids["brak"], "action": "dismiss"})
    assert store.recipe(ids["brak"], {}, frozenset()) is not None
    flags = store.recipe_flags(ids["brak"])
    assert flags is not None and flags[1] is False  # brak szacunku → nie ma w kolejce, bez zmian

    client.post("/kupujemy/zatwierdz-szacunek", data={"recipe_id": ids["pending0"], "action": "dismiss"})
    flags = store.recipe_flags(ids["pending0"])
    assert flags is not None and flags[1] is True
    assert "Kasza Epsilon" not in client.get("/kupujemy/zatwierdz").text

    unknown = client.post(
        "/kupujemy/zatwierdz-szacunek", data={"recipe_id": ids["zrodlo"], "action": "approve"}
    )
    assert unknown.headers["location"].endswith("error=nieznany")
    assert store.recipe(ids["zrodlo"], {}, frozenset()).kcal_source == "source"  # type: ignore[union-attr]


def test_estimate_approve_without_kcal_is_noop(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _scene(store)
    client.post("/kupujemy/zatwierdz-szacunek", data={"recipe_id": ids["pending0"], "action": "approve"})
    row = store.recipe(ids["pending0"], {}, frozenset())
    assert row is not None and row.est_status == "pending"  # store nie zatwierdza przepisu bez kcal


def test_kcal_100g_from_source_on_pending_card(tmp_path: Path) -> None:
    client, store, s = _client(tmp_path)
    rid = store.upsert_recipe(
        _rec("x", "Ania Iota", None, kcal_100g=180.0), RecipeMeta("lunch", 3, True, "rule")
    )
    _estimate(store, rid, kcal=250.0, status="pending", coverage=0.7)
    text = client.get("/kupujemy/zatwierdz").text
    assert "Ze strony: 180 kcal/100 g" in text
    with sqlite3.connect(s.db_path) as conn:
        assert conn.execute("SELECT kcal_100g FROM recipe WHERE id=?", (rid,)).fetchone()[0] == 180.0


def test_ingredient_approval_does_not_recalculate_approved_or_source_recipe(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    meta = RecipeMeta("lunch", 3, True, "rule")
    appr = store.upsert_recipe(_rec("ap", "Hummus Approved", None), meta)
    src = store.upsert_recipe(_rec("sr", "Hummus Source", 400.0), meta)
    store.link_recipe_ingredient("tahini", "tahini", None)
    _estimate(store, appr, kcal=310.0, status="approved", coverage=0.95)
    ing_id = store.upsert_ingredient("tahini", None)
    store.save_ingredient_nutrition(
        ing_id,
        kcal=600.0,
        protein=17.0,
        carbs=21.0,
        fat=54.0,
        piece_g=None,
        source="llm",
        confidence="low",
        status="pending",
    )
    r = client.post(
        "/kupujemy/zatwierdz-skladnik",
        data={"ingredient_id": ing_id, "kcal": "600", "protein": "17", "carbs": "21", "fat": "54"},
    )
    assert r.status_code == 303
    approved = store.recipe(appr, {}, frozenset())
    assert approved is not None and approved.kcal == 310.0 and approved.est_status == "approved"
    source = store.recipe(src, {}, frozenset())
    assert source is not None and source.kcal == 400.0 and source.kcal_source == "source"


def test_estimate_approve_failure_redirects_with_error(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _scene(store)
    # pending bez kcal: store nie zatwierdza (False) → błąd formularza
    r = client.post("/kupujemy/zatwierdz-szacunek", data={"recipe_id": ids["pending0"], "action": "approve"})
    assert r.headers["location"].endswith("error=dane")
