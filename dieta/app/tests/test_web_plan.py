from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import Profile, RecipeMeta, Store
from dieta.web.app import create_app
from dieta.zakupy_client import Purchase

WEEK = "2026-11-23"  # poniedziałek


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
        ingredients=(RawIngredient("400 g ryż", 400, "g", "ryż"),),
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


def _location(resp) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    return parse_qs(urlsplit(resp.headers["location"]).query)


def test_empty_screen_without_profiles(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    resp = client.get("/plan")
    assert resp.status_code == 200
    assert "Uzupełnij profile" in resp.text


def test_nav_has_plan_tab(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert 'href="plan"' in client.get("/katalog").text


def test_generate_without_profiles_reports_and_saves_nothing(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    resp = client.post("/plan/generuj", data={"tydzien": WEEK})
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("../plan?")
    assert _location(resp)["notice"] == ["no_profiles"]
    assert store.plan(date(2026, 11, 23)) is None


def test_generate_shows_plan_and_redirects(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    resp = client.post("/plan/generuj", data={"tydzien": WEEK})
    assert resp.status_code == 303
    loc = _location(resp)
    assert loc["notice"] == ["generated"]
    assert loc["tydzien"] == [WEEK]
    page = client.get(f"/plan?tydzien={WEEK}&notice=generated")
    assert page.status_code == 200
    assert "Plan wygenerowany" in page.text
    assert "dz-meal" in page.text
    assert "Danie" in page.text


def test_non_monday_is_normalized_to_monday(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    assert "2026-11-23" in client.get("/plan?tydzien=2026-11-25").text


def test_approve_then_regenerate_needs_confirm(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    client.post("/plan/generuj", data={"tydzien": WEEK})
    resp = client.post("/plan/zatwierdz", data={"tydzien": WEEK})
    assert _location(resp)["notice"] == ["approved"]
    plan = store.plan(date(2026, 11, 23))
    assert plan is not None and plan.status == "approved"
    before = store.plan_meals(plan.id)
    resp = client.post("/plan/generuj", data={"tydzien": WEEK})
    assert _location(resp)["notice"] == ["needs_confirm"]
    assert store.plan_meals(plan.id) == before
    resp = client.post("/plan/generuj", data={"tydzien": WEEK, "potwierdz": "1"})
    assert _location(resp)["notice"] == ["generated"]
    assert store.plan(date(2026, 11, 23)).status == "draft"  # type: ignore[union-attr]


def test_swap_session_then_undo(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    client.post("/plan/generuj", data={"tydzien": WEEK})
    plan = store.plan(date(2026, 11, 23))
    assert plan is not None
    session = store.plan_sessions(plan.id)[0]
    resp = client.post(
        "/plan/wymien", data={"tydzien": WEEK, "zakres": "sesja", "tryb": "los", "id": str(session.id)}
    )
    assert resp.status_code == 303
    assert _location(resp)["notice"][0] in ("swapped", "unchanged")
    resp = client.post("/plan/cofnij", data={"tydzien": WEEK})
    assert _location(resp)["notice"][0] in ("undone", "nothing_to_undo")


def test_swap_day_with_liked_mode_and_undo_round_trip(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    client.post("/plan/generuj", data={"tydzien": WEEK})
    plan = store.plan(date(2026, 11, 23))
    assert plan is not None
    before = store.plan_meals(plan.id)
    resp = client.post(
        "/plan/wymien", data={"tydzien": WEEK, "zakres": "dzien", "tryb": "polubione", "id": "0:lunch"}
    )
    assert resp.status_code == 303
    if _location(resp)["notice"] == ["swapped"]:
        client.post("/plan/cofnij", data={"tydzien": WEEK})
        assert store.plan_meals(plan.id) == before


def test_invalid_swap_requests_change_nothing(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    client.post("/plan/generuj", data={"tydzien": WEEK})
    plan = store.plan(date(2026, 11, 23))
    assert plan is not None
    before = store.plan_meals(plan.id)
    bad = [
        {"zakres": "tydzien", "tryb": "los", "id": "1"},
        {"zakres": "sesja", "tryb": "zly", "id": "1"},
        {"zakres": "sesja", "tryb": "los", "id": "abc"},
        {"zakres": "dzien", "tryb": "los", "id": "9:lunch"},
        {"zakres": "dzien", "tryb": "los", "id": "1:snack9"},
    ]
    for fields in bad:
        resp = client.post("/plan/wymien", data={"tydzien": WEEK, **fields})
        assert _location(resp)["notice"] == ["bad"], fields
    assert store.plan_meals(plan.id) == before


def test_undo_without_history_is_reported(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    client.post("/plan/generuj", data={"tydzien": WEEK})
    resp = client.post("/plan/cofnij", data={"tydzien": WEEK})
    assert _location(resp)["notice"] == ["nothing_to_undo"]


def test_warnings_and_notice_are_shown_escaped(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    page = client.get(f"/plan?tydzien={WEEK}&notice=generated&w=Ostrze%C5%BCenie&w=%3Cb%3Ex%3C%2Fb%3E")
    assert "Plan wygenerowany" in page.text
    assert "Ostrzeżenie" in page.text
    assert "&lt;b&gt;x&lt;/b&gt;" in page.text
    assert "<b>x</b>" not in page.text


def test_plan_recipe_titles_are_escaped(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    store.upsert_recipe(_recipe("xss", "<i>Zupa</i>", 350.0), RecipeMeta("lunch", 3, True, "rule"))
    client.post("/plan/generuj", data={"tydzien": WEEK})
    assert "<i>Zupa</i>" not in client.get(f"/plan?tydzien={WEEK}").text


def test_week_navigation_links(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    text = client.get(f"/plan?tydzien={WEEK}").text
    prev = (date(2026, 11, 23) - timedelta(days=7)).isoformat()
    assert f"plan?tydzien={prev}" in text


def test_swap_rejects_unicode_digits_without_500(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    for bad in ("²", "٣:lunch", "9:lunch"):
        zakres = "sesja" if ":" not in bad else "dzien"
        r = client.post(
            "/plan/wymien",
            data={"zakres": zakres, "tryb": "los", "id": bad, "tydzien": "2026-11-23"},
            follow_redirects=False,
        )
        assert r.status_code == 303


def _approved_plan(client: TestClient) -> None:
    client.post("/plan/generuj", data={"tydzien": WEEK})
    client.post("/plan/zatwierdz", data={"tydzien": WEEK})


def test_cost_bar_without_prices_shows_single_line(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    _approved_plan(client)
    resp = client.get("/plan", params={"tydzien": WEEK})
    assert resp.status_code == 200
    assert "Brak cen z Zakupów" in resp.text
    assert "Koszt tygodnia" not in resp.text


def test_cost_bar_with_prices_shows_total_coverage_and_session(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _demo(store)
    store.link_recipe_ingredient("ryż", "ryż", "dry")  # type: ignore[arg-type]
    store.upsert_ingredient("ryż", None)
    store.replace_purchases(
        [
            Purchase(
                code="r1",
                chain="lidl",
                name="Ryż biały 400g",
                names=("Ryż biały 400g",),
                purchases=1,
                spend=3.2,
                first=date(2026, 11, 1),
                last=date(2026, 11, 1),
                dates=(date(2026, 11, 1),),
                category="food",
                unit_price=3.2,
                is_weight=False,
            )
        ]
    )
    store.set_mapping("r1", "approved", "ryż", "dry", "x")
    _approved_plan(client)
    resp = client.get("/plan", params={"tydzien": WEEK})
    assert resp.status_code == 200
    assert "Koszt tygodnia" in resp.text
    assert "pokrycie cen" in resp.text
    assert "zł/porcja" in resp.text


def test_session_chip_without_coverage_shows_dash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dieta.shopping import PlanCost, SessionCost

    client, store = _client(tmp_path)
    _demo(store)
    _approved_plan(client)
    plan = store.plan(date.fromisoformat(WEEK))
    assert plan is not None

    def fake(store_: Store, settings: Settings, week: date) -> PlanCost:
        sessions = tuple(
            SessionCost(s.id, s.recipe_id, "Test", 0.0, 0.0, 0.0) for s in store_.plan_sessions(plan.id)
        )
        return PlanCost(10.0, 0.5, sessions, coverage_by_source={"receipt": 0.5})

    monkeypatch.setattr("dieta.web.plan.plan_cost", fake)
    resp = client.get("/plan", params={"tydzien": WEEK})
    assert resp.status_code == 200
    assert "sesja —" in resp.text
    assert "w tym: paragony 50%" in resp.text
