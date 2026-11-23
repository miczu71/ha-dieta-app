from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient

from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import Profile, RecipeMeta, Store
from dieta.web.app import create_app


def _recipe(sid: str, title: str, kcal: float) -> Recipe:
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
        ingredients=(RawIngredient("1 ryż", 1, None, "ryż"),),
    )


def _client(tmp_path: Path) -> tuple[TestClient, Store]:
    s = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(s.db_path)
    app = create_app(s, sync_runner=lambda: 0, crawl_runner=lambda src: {})
    return TestClient(app, follow_redirects=False), store


def _demo(store: Store, people: tuple[int, ...] = (1, 2)) -> None:
    for pid in people:
        store.save_profile(Profile(pid, "f", 30, 165.0, 2, "maintain", 0.0, None, None, 0.3))
        store.add_weight(pid, date(2026, 11, 1), 70.0)
    for category, base in (("lunch", 380.0), ("dinner", 420.0), ("breakfast", 300.0)):
        for i in range(4):
            store.upsert_recipe(
                _recipe(f"{category}{i}", f"Danie {category} {i}", base + 10 * i),
                RecipeMeta(category, 3, True, "rule"),
            )


def _location(resp: httpx.Response) -> dict[str, list[str]]:
    return parse_qs(urlsplit(resp.headers["location"]).query)


def _week() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def _approve(client: TestClient) -> None:
    client.post("/plan/generuj", data={"tydzien": _week().isoformat()})
    client.post("/plan/zatwierdz", data={"tydzien": _week().isoformat()})


def _today_slot(store: Store) -> str:
    plan = store.plan(_week())
    assert plan is not None
    meals = [
        m for m in store.plan_meals(plan.id) if m.day == date.today().weekday() and m.p1_portions is not None
    ]
    assert meals
    return meals[0].slot


def _mark(client: TestClient, day: date, slot: str, stan: str, **extra: str) -> httpx.Response:
    data = {"dzien": day.isoformat(), "osoba": "1", "slot": slot, "stan": stan, **extra}
    return client.post("/dzis/odhacz", data=data)


def test_dzis_renders_meals_with_actions(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    page = client.get("/dzis")
    assert page.status_code == 200
    assert "dz-meal-log" in page.text
    assert "Danie" in page.text
    assert 'name="stan" value="zjedzone"' in page.text


def test_nav_has_dzis_and_bilans_tabs(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    text = client.get("/plan").text
    assert 'href="dzis"' in text
    assert 'href="bilans"' in text


def test_eaten_then_bilans_and_day_view(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    slot = _today_slot(store)
    resp = _mark(client, today, slot, "zjedzone")
    assert resp.status_code == 303
    assert _location(resp)["notice"] == ["ok"]
    entries = store.meal_log(today, today)
    assert len(entries) == 1
    assert entries[0].status == "eaten" and entries[0].kcal > 0
    page = client.get(f"/dzis?dzien={today.isoformat()}")
    assert "zjedzone · " in page.text
    bilans = client.get(f"/bilans?tydzien={_week().isoformat()}")
    assert bilans.status_code == 200
    assert f"{entries[0].kcal:.0f}" in bilans.text
    assert "odchylenie" in bilans.text
    assert "Bez odpowiedzi" in bilans.text


def test_skipped_state(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    slot = _today_slot(store)
    _mark(client, today, slot, "pominiete")
    entries = store.meal_log(today, today)
    assert [(e.status, e.kcal) for e in entries] == [("skipped", 0.0)]
    assert "pominięte" in client.get(f"/dzis?dzien={today.isoformat()}").text


def test_substitute_with_decimal_comma(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    slot = _today_slot(store)
    resp = _mark(client, today, slot, "zamiennik", nazwa="Kanapka", kcal="350,5", bialko="12")
    assert _location(resp)["notice"] == ["ok"]
    (entry,) = store.meal_log(today, today)
    assert entry.status == "substitute"
    assert entry.title == "Kanapka"
    assert entry.kcal == 350.5
    assert entry.protein == 12.0 and entry.fat is None
    assert "zamiennik: Kanapka" in client.get(f"/dzis?dzien={today.isoformat()}").text


def test_reset_removes_row(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    slot = _today_slot(store)
    _mark(client, today, slot, "zjedzone")
    resp = _mark(client, today, slot, "reset")
    assert _location(resp)["notice"] == ["ok"]
    assert store.meal_log(today, today) == []


def test_draft_plan_shows_no_actions_and_rejects_post(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    client.post("/plan/generuj", data={"tydzien": _week().isoformat()})
    page = client.get("/dzis")
    assert 'name="stan"' not in page.text
    assert "Zatwierdź" in page.text
    today = date.today()
    resp = _mark(client, today, _today_slot(store), "zjedzone")
    assert _location(resp)["notice"] == ["not_approved"]
    assert store.meal_log(today, today) == []


def test_out_of_window_is_read_only_and_rejected(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    old = today - timedelta(days=8)
    assert 'name="stan"' not in client.get(f"/dzis?dzien={old.isoformat()}").text
    assert 'name="stan"' not in client.get(f"/dzis?dzien={(today + timedelta(days=1)).isoformat()}").text
    resp = _mark(client, old, _today_slot(store), "zjedzone")
    assert _location(resp)["notice"] == ["out_of_window"]
    resp = _mark(client, today + timedelta(days=1), _today_slot(store), "zjedzone")
    assert _location(resp)["notice"] == ["out_of_window"]
    assert store.meal_log(old, today + timedelta(days=1)) == []


def test_bad_input_changes_nothing(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    slot = _today_slot(store)
    cases = [
        _mark(client, today, slot, "xxx"),
        _mark(client, today, slot, "zamiennik", nazwa="A", kcal="abc"),
        _mark(client, today, slot, "zamiennik", nazwa="A", kcal="-5"),
        _mark(client, today, slot, "zamiennik", nazwa="A", kcal="nan"),
        _mark(client, today, slot, "zamiennik", nazwa="   ", kcal="100"),
        _mark(client, today, slot, "zjedzone", bialko="x"),
        client.post("/dzis/odhacz", data={"dzien": "zla", "osoba": "1", "slot": slot, "stan": "zjedzone"}),
        client.post(
            "/dzis/odhacz", data={"dzien": today.isoformat(), "osoba": "3", "slot": slot, "stan": "zjedzone"}
        ),
        client.post(
            "/dzis/odhacz",
            data={"dzien": today.isoformat(), "osoba": "1", "slot": "brunch", "stan": "zjedzone"},
        ),
    ]
    for resp in cases:
        assert _location(resp)["notice"] == ["bad"]
    assert store.meal_log(today, today) == []


def test_substitute_name_is_escaped(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    today = date.today()
    _mark(client, today, _today_slot(store), "zamiennik", nazwa="<script>alert(1)</script>", kcal="100")
    text = client.get(f"/dzis?dzien={today.isoformat()}").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert(1)" not in text


def test_bad_day_param_falls_back_to_today(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    page = client.get("/dzis?dzien=nie-data")
    assert page.status_code == 200
    assert "Uzupełnij profile" in page.text


def test_bilans_without_plan_renders(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    resp = client.get("/bilans?tydzien=2000-01-05")
    assert resp.status_code == 200
    assert "Brak planu na ten tydzień" in resp.text
    assert "Uzupełnij profile" in resp.text


def test_bilans_with_plan_renders_table(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approve(client)
    resp = client.get(f"/bilans?tydzien={_week().isoformat()}")
    assert resp.status_code == 200
    assert "dz-table" in resp.text
    assert "Zostało porcji" in resp.text
    assert 'name="stan"' not in resp.text


def test_out_of_range_dates_do_not_crash(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert client.get("/dzis?dzien=9999-12-31").status_code == 200
    assert client.get("/dzis?dzien=0001-01-01").status_code == 200
    assert client.get("/bilans?tydzien=9999-12-27").status_code == 200
    resp = client.post("/dzis/odhacz", data={"dzien": date.today().isoformat(), "osoba": "9" * 5000})
    assert resp.status_code == 303
