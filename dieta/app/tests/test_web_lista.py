from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from dieta.settings import Settings
from dieta.shopping import shopping_list
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import NewPlanMeal, NewPlanSession, Profile, RecipeMeta, Store
from dieta.web.app import create_app
from dieta.zakupy_client import Purchase

TODAY = date.today()
THIS_MONDAY = TODAY - timedelta(days=TODAY.weekday())
WEEK = THIS_MONDAY + timedelta(days=7)  # przyszły tydzień: wszystkie sesje w liście
WEEK_ISO = WEEK.isoformat()


def _recipe(sid: str, servings: float, items: tuple[tuple[float, str, str], ...]) -> Recipe:
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
        ingredients=tuple(RawIngredient(f"{a:g} {u} {n}", a, u, n) for a, u, n in items),
    )


def _buy(code: str, names: tuple[str, ...], unit_price: float, is_weight: bool, last: date) -> Purchase:
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


def _world(
    tmp_path: Path,
    *,
    status: str = "approved",
    week: date = WEEK,
    todo_entity: str = "",
    with_plan: bool = True,
) -> tuple[TestClient, Store, dict[str, int]]:
    settings = Settings(data_dir=tmp_path, log_level="info", dev=True, todo_entity=todo_entity)
    store = Store(settings.db_path)
    for pid in (1, 2):
        store.save_profile(Profile(pid, "f", 30, 165.0, 2, "maintain", 0.0, None, None, 0.3))
        store.add_weight(pid, date(2026, 11, 1), 70.0)
    meta = RecipeMeta("lunch", 3, True, "rule")
    r1 = store.upsert_recipe(
        _recipe("r1", 2, ((400, "g", "kurczak"), (200, "g", "ryż"), (2, "szt", "cebula"))), meta
    )
    ids: dict[str, int] = {}
    for raw, durability in (("kurczak", "fresh"), ("ryż", "dry"), ("cebula", "fresh")):
        store.link_recipe_ingredient(raw, raw, durability)  # type: ignore[arg-type]
        ids[raw] = store.upsert_ingredient(raw, None)
    store.replace_purchases(
        [
            _buy("k1", ("Pierś z kurczaka",), 20.0, True, THIS_MONDAY - timedelta(days=20)),
            _buy("r1", ("Ryż biały 400g",), 3.2, False, THIS_MONDAY - timedelta(days=5)),
        ]
    )
    store.set_mapping("k1", "approved", "kurczak", "fresh", "x")
    store.set_mapping("r1", "approved", "ryż", "dry", "x")
    if with_plan:
        sessions = [NewPlanSession("lunch", r1, 0, 3)]
        meals = [NewPlanMeal(d, "lunch", 0, 1.0, 1.0) for d in range(3)]
        store.save_plan(week, status, 1, "{}", sessions, meals)  # type: ignore[arg-type]
    app = create_app(settings, sync_runner=lambda: 0, crawl_runner=lambda src: {})
    return TestClient(app, follow_redirects=False), store, ids


def _location(resp: Any) -> dict[str, list[str]]:
    return parse_qs(urlsplit(resp.headers["location"]).query)


def test_lista_renders_sections_cost_and_coverage(tmp_path: Path) -> None:
    client, _, _ = _world(tmp_path)
    resp = client.get("/lista", params={"tydzien": WEEK_ISO})
    assert resp.status_code == 200
    for text in ("Do kupienia", "Pewnie masz", "Bez ceny", "pokrycie cen", "zł", "Kurczak", "Cebula"):
        assert text in resp.text
    assert " g" in resp.text  # ilości w gramach


def test_lista_without_todo_entity_has_no_send_button(tmp_path: Path) -> None:
    client, _, _ = _world(tmp_path, todo_entity="")
    assert "Wyślij do todo" not in client.get("/lista", params={"tydzien": WEEK_ISO}).text


def test_lista_with_todo_entity_has_send_button(tmp_path: Path) -> None:
    client, _, _ = _world(tmp_path, todo_entity="todo.zakupy")
    assert "Wyślij do todo" in client.get("/lista", params={"tydzien": WEEK_ISO}).text


def test_lista_draft_shows_notice_and_no_actions(tmp_path: Path) -> None:
    client, _, _ = _world(tmp_path, status="draft", todo_entity="todo.zakupy")
    resp = client.get("/lista", params={"tydzien": WEEK_ISO})
    assert resp.status_code == 200
    assert "Plan jest szkicem" in resp.text
    assert "Wyślij do todo" not in resp.text


def test_lista_without_plan_shows_notice(tmp_path: Path) -> None:
    client, _, _ = _world(tmp_path, with_plan=False)
    resp = client.get("/lista", params={"tydzien": WEEK_ISO})
    assert resp.status_code == 200
    assert "Brak zatwierdzonego planu" in resp.text


def test_lista_past_week_shows_notice(tmp_path: Path) -> None:
    past = THIS_MONDAY - timedelta(days=7)
    client, _, _ = _world(tmp_path, week=past)
    resp = client.get("/lista", params={"tydzien": past.isoformat()})
    assert resp.status_code == 200
    assert "Ten tydzień już minął" in resp.text


def test_jednak_toggles_force(tmp_path: Path) -> None:
    client, store, ids = _world(tmp_path)
    data = {"tydzien": WEEK_ISO, "skladnik": str(ids["ryż"])}
    resp = client.post("/lista/jednak", data=data)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("../lista")
    assert store.shopping_states(WEEK_ISO).get("force") == {ids["ryż"]}
    client.post("/lista/jednak", data=data)
    assert "force" not in store.shopping_states(WEEK_ISO)


def test_odhacz_toggles_checked(tmp_path: Path) -> None:
    client, store, ids = _world(tmp_path)
    data = {"tydzien": WEEK_ISO, "skladnik": str(ids["kurczak"])}
    assert client.post("/lista/odhacz", data=data).status_code == 303
    assert store.shopping_states(WEEK_ISO).get("checked") == {ids["kurczak"]}
    client.post("/lista/odhacz", data=data)
    assert "checked" not in store.shopping_states(WEEK_ISO)


def test_bad_ingredient_id_changes_nothing(tmp_path: Path) -> None:
    client, store, _ = _world(tmp_path)
    resp = client.post("/lista/odhacz", data={"tydzien": WEEK_ISO, "skladnik": "abc"})
    assert resp.status_code == 303
    assert _location(resp)["notice"] == ["bad"]
    assert store.shopping_states(WEEK_ISO) == {}


def _capture_todo(monkeypatch: pytest.MonkeyPatch, result: str = "ok") -> list[tuple[str, str]]:
    sent: list[tuple[str, str]] = []

    def fake(settings: Settings, items: Any, opener: Any = None) -> str:
        sent.extend(items)
        return result

    monkeypatch.setattr("dieta.web.lista.send_items", fake)
    return sent


def test_todo_sends_only_unsent_unchecked_and_not_at_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = _capture_todo(monkeypatch)
    client, store, ids = _world(tmp_path, todo_entity="todo.zakupy")
    client.post("/lista/odhacz", data={"tydzien": WEEK_ISO, "skladnik": str(ids["kurczak"])})
    lst = shopping_list(store, client.app.state.settings, WEEK, date.today())  # type: ignore[attr-defined]
    expected = {ln.name: ln.ingredient_id for ln in (*lst.to_buy, *lst.unpriced) if ln.name != "kurczak"}
    at_home = {ln.name for ln in lst.at_home}

    resp = client.post("/lista/todo", data={"tydzien": WEEK_ISO})
    assert resp.status_code == 303
    assert _location(resp)["notice"] == [f"todo_ok-{len(expected)}-{len(expected)}"]
    texts = [item for item, _ in sent]
    assert len(texts) == len(expected)
    for name in expected:
        assert any(t.lower().startswith(name.lower()) for t in texts)
    assert not any(t.lower().startswith(n.lower()) for t in texts for n in at_home)
    assert not any(t.lower().startswith("kurczak") for t in texts)
    assert store.shopping_states(WEEK_ISO)["sent"] == set(expected.values())

    sent.clear()
    client.post("/lista/todo", data={"tydzien": WEEK_ISO})
    assert sent == [], "drugie kliknięcie nie wysyła ponownie"


@pytest.mark.parametrize("result", ["failed", "skipped"])
def test_todo_not_ok_records_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: str) -> None:
    _capture_todo(monkeypatch, result)
    client, store, _ = _world(tmp_path, todo_entity="todo.zakupy")
    resp = client.post("/lista/todo", data={"tydzien": WEEK_ISO})
    assert resp.status_code == 303
    assert _location(resp)["notice"] == [f"todo_{result}"]
    assert "sent" not in store.shopping_states(WEEK_ISO)


def test_lista_shows_source_chips_estimate_and_dash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dieta.pricing import IngredientPrice

    prices = {
        "kurczak": IngredientPrice(2.0, None, ("k1",), source="promo", as_of=TODAY, promo_to="2026-11-30"),
        "ryż": IngredientPrice(0.8, 400.0, ("r1",), source="frisco", as_of=TODAY, estimate=True),
    }
    monkeypatch.setattr("dieta.shopping.cascade_prices", lambda *a, **k: prices)
    client, _, _ = _world(tmp_path)
    text = client.get("/lista", params={"tydzien": WEEK_ISO}).text
    assert "promocja do 30.11" in text
    assert "szacunek" in text
    assert "≈ " in text  # koszt szacunku (ryż, pewnie masz) — w tym widoku ryż jest w „Pewnie masz”
    assert "bez ceny" not in text and " · —" in text  # cebula bez ceny
    assert "w tym: promocja" in text


def test_lista_real_prices_show_receipt_chip_and_split(tmp_path: Path) -> None:
    client, _, _ = _world(tmp_path)
    text = client.get("/lista", params={"tydzien": WEEK_ISO}).text
    assert ">paragon<" in text
    assert "w tym: paragony " in text


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("todo_ok-3-3", "Wysłano 3 z 3 pozycji do listy todo."),
        ("todo_partial-2-3", "Wysłano 2 z 3 pozycji, reszta się nie udała — szczegóły w logu."),
    ],
)
def test_todo_count_notices_are_shown(tmp_path: Path, code: str, expected: str) -> None:
    client, _, _ = _world(tmp_path)
    assert expected in client.get("/lista", params={"tydzien": WEEK_ISO, "notice": code}).text


@pytest.mark.parametrize("code", ["todo_ok-5-3", "todo_ok-x-3", "todo_partial-2-3-9", "todo_ok-03-3x"])
def test_malformed_todo_count_notice_is_ignored(tmp_path: Path, code: str) -> None:
    client, _, _ = _world(tmp_path)
    assert "Wysłano" not in client.get("/lista", params={"tydzien": WEEK_ISO, "notice": code}).text


def test_todo_partial_after_two_of_three_counts_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, store, ids = _world(tmp_path, todo_entity="todo.zakupy")
    # ryż z „Pewnie masz” do listy → trzy pozycje: kurczak, ryż, cebula
    client.post("/lista/jednak", data={"tydzien": WEEK_ISO, "skladnik": str(ids["ryż"])})
    calls: list[int] = []

    def fake(settings: Settings, items: Any, opener: Any = None) -> str:
        calls.append(1)
        return "ok" if len(calls) <= 2 else "failed"

    monkeypatch.setattr("dieta.web.lista.send_items", fake)
    resp = client.post("/lista/todo", data={"tydzien": WEEK_ISO})
    assert _location(resp)["notice"] == ["todo_partial-2-3"]
    assert len(store.shopping_states(WEEK_ISO)["sent"]) == 2
    assert (
        "Wysłano 2 z 3"
        in client.get("/lista", params={"tydzien": WEEK_ISO, "notice": "todo_partial-2-3"}).text
    )
