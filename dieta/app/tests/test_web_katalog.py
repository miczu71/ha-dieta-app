import threading
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from dieta.catalog import CrawlResult
from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import RecipeMeta, Store
from dieta.web.app import create_app
from dieta.zakupy_client import Purchase

TODAY = date.today()


def _recipe(
    sid: str, title: str, names: tuple[str, ...], kcal: float | None = 400.0, source: str = "respo"
) -> Recipe:
    return Recipe(
        source=source,
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


def _client(tmp_path: Path, runner=None) -> tuple[TestClient, Store, Settings]:  # type: ignore[no-untyped-def]
    s = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(s.db_path)
    app = create_app(s, sync_runner=lambda: 0, crawl_runner=runner or (lambda src: {}))
    return TestClient(app, follow_redirects=False), store, s


def _demo(store: Store) -> dict[str, int]:
    day = TODAY - timedelta(days=1)
    store.replace_purchases(
        [
            Purchase(f"n:{i}", "lidl", n, (n,), 3, 10.0, day, day, (day,), "food")
            for i, n in enumerate(("ryż", "kurczak"))
        ]
    )
    store.set_mapping("n:0", "rule", "ryż", "dry", "ryż")
    store.set_mapping("n:1", "rule", "filet z kurczaka", "fresh", "kurczak")
    ids = {
        "dobry": store.upsert_recipe(
            _recipe("a", "Ryż z kurczakiem", ("ryż", "filet z kurczaka")),
            RecipeMeta("lunch", 3, True, "rule"),
        ),
        "slaby": store.upsert_recipe(
            _recipe("b", "Tahini bowl", ("ryż", "tahini", "ciecierzyca"), kcal=None, source="lidl"),
            RecipeMeta("dinner", 2, False, "rule"),
        ),
    }
    for raw in ("ryż", "filet z kurczaka"):
        store.link_recipe_ingredient(raw, raw, None)
    for raw in ("tahini", "ciecierzyca"):
        store.link_recipe_ingredient(raw, raw, None)
    return ids


def test_catalog_lists_sorted_with_familiarity_and_badges(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    _demo(store)
    r = client.get("/katalog?prog=0")
    assert r.status_code == 200
    assert r.text.index("Ryż z kurczakiem") < r.text.index("Tahini bowl")
    assert "2/2" in r.text and "1/3" in r.text
    assert "brak kcal" in r.text
    assert "brakuje: tahini, ciecierzyca" in r.text
    assert (
        'href="katalog/' in r.text
        and 'href="/' not in r.text.split("<main")[1].split("https://example.org")[0]
    )


def test_default_threshold_hides_weak_recipe(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    _demo(store)
    text = client.get("/katalog").text
    assert "Ryż z kurczakiem" in text and "Tahini bowl" not in text


def test_filters_category_source_liked(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _demo(store)
    assert "Tahini bowl" not in client.get("/katalog?prog=0&kat=lunch").text
    assert "Ryż z kurczakiem" not in client.get("/katalog?prog=0&zrodlo=lidl").text
    assert "Ryż z kurczakiem" not in client.get("/katalog?prog=0&polubione=1").text
    store.set_flag(ids["dobry"], "liked")
    liked = client.get("/katalog?prog=0&polubione=1").text
    assert "Ryż z kurczakiem" in liked and "Tahini bowl" not in liked
    assert "Ryż z kurczakiem" in client.get("/katalog?prog=abc&kat=zle").text  # śmieci → domyślne


def test_heart_and_dismiss_toggle_and_keep_filters(tmp_path: Path) -> None:
    client, store, s = _client(tmp_path)
    ids = _demo(store)
    r = client.post(f"/katalog/{ids['dobry']}/serce", data={"qs": "prog=0&kat=lunch&evil=1"})
    assert r.status_code == 303
    assert r.headers["location"] == "../../katalog?kat=lunch&prog=0"
    assert store.recipe(ids["dobry"], {}, frozenset()).liked  # type: ignore[union-attr]
    client.post(f"/katalog/{ids['dobry']}/serce", data={})
    assert not store.recipe(ids["dobry"], {}, frozenset()).liked  # type: ignore[union-attr]
    client.post(f"/katalog/{ids['dobry']}/odrzuc", data={})
    assert "Ryż z kurczakiem" not in client.get("/katalog?prog=0").text
    assert "Ryż z kurczakiem" in client.get("/katalog?prog=0&odrzucone=1").text
    r = client.post(f"/katalog/{ids['dobry']}/odrzuc", data={"detail": "1"})
    assert r.headers["location"] == f"../{ids['dobry']}"
    assert client.post("/katalog/999/serce", data={}).status_code == 404


def test_detail_marks_ingredients(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    ids = _demo(store)
    text = client.get(f"/katalog/{ids['slaby']}").text
    assert "Tahini bowl" in text and "brak kcal" in text
    assert text.count('dz-chip">brak<') == 2
    assert 'dz-chip dz-chip--home">w domu<' in text
    assert 'href="../static/app.css' in text
    assert client.get("/katalog/999").status_code == 404


def test_refresh_runs_in_thread_and_rejects_unknown_source(tmp_path: Path) -> None:
    done = threading.Event()
    calls: list[str | None] = []

    def runner(src: str | None) -> dict[str, CrawlResult]:
        calls.append(src)
        done.set()
        return {}

    client, _, _ = _client(tmp_path, runner)
    r = client.post("/katalog/odswiez?zrodlo=policzona")
    assert r.status_code == 303 and r.headers["location"] == "../katalog?notice=start"
    assert done.wait(3)
    assert calls == ["policzona"]
    assert client.post("/katalog/odswiez?zrodlo=nieznane").headers["location"].endswith("notice=zrodlo")


def test_refresh_refuses_parallel_run(tmp_path: Path) -> None:
    gate = threading.Event()

    def runner(src: str | None) -> dict[str, CrawlResult]:
        gate.wait(3)
        return {}

    client, _, _ = _client(tmp_path, runner)
    client.post("/katalog/odswiez")
    assert client.post("/katalog/odswiez").headers["location"].endswith("notice=trwa")
    gate.set()


def test_queue_includes_recipe_ingredients_and_meta(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    rid = store.upsert_recipe(
        _recipe("z", "Dziwne danie", ("zagadka",)), RecipeMeta(None, None, None, "pending")
    )
    store.set_mapping("ing:zagadka", "pending", None, None, "zagadka")
    text = client.get("/kupujemy/zatwierdz").text
    assert "Dziwne danie" in text and "zagadka" in text
    assert client.get("/").text.count("dz-chip dz-num") == 0  # profil bez licznika
    assert ">2<" in client.get("/katalog?prog=0").text  # licznik w nawigacji

    r = client.post(
        "/kupujemy/zatwierdz-przepis",
        data={"recipe_id": rid, "category": "dinner", "storage_days": 3, "freezable": "1"},
    )
    assert r.status_code == 303 and r.headers["location"] == "zatwierdz"
    assert store.pending_recipe_meta() == []
    bad = client.post(
        "/kupujemy/zatwierdz-przepis", data={"recipe_id": rid, "category": "dinner", "storage_days": 3}
    )
    assert bad.headers["location"].endswith("error=nieznany")

    client.post(
        "/kupujemy/zatwierdz",
        data={"code": "ing:zagadka", "action": "approve", "ingredient": "zagadka", "durability": "dry"},
    )
    assert store.unlinked_ingredient_names() == []  # zatwierdzony składnik powiązany z przepisem
