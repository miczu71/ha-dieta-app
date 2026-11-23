from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from dieta.settings import Settings
from dieta.store import Store
from dieta.web.app import create_app
from dieta.web.kupujemy import review_queue, seconds_until
from dieta.zakupy_client import Purchase

TODAY = date.today()


def _purchase(code: str, name: str, days_ago: int, category: str = "food") -> Purchase:
    day = TODAY - timedelta(days=days_ago)
    return Purchase(code, "lidl", name, (name,), 3, 12.5, day, day, (day,), category)


def _client(tmp_path: Path, *, configured: bool = True, runner=None) -> tuple[TestClient, Store]:  # type: ignore[no-untyped-def]
    s = Settings(
        data_dir=tmp_path,
        log_level="info",
        dev=True,
        zakupy_url="http://zakupy:8099" if configured else "",
        zakupy_token="x" if configured else "",
    )
    store = Store(s.db_path)
    app = create_app(s, sync_runner=runner or (lambda: 0))
    return TestClient(app, follow_redirects=False), store


def _seed(store: Store) -> None:
    store.replace_purchases(
        [
            _purchase("n:1", "mleko 2%", 1),
            _purchase("n:2", "tajemniczy wyrób", 2),
            _purchase("n:3", "ryż", 3),
        ]
    )
    store.upsert_ingredient("mleko", "chilled")
    store.set_mapping("n:1", "rule", "mleko", "chilled", "mleko 2%")
    store.set_mapping("n:2", "pending", None, None, "tajemniczy wyrób")
    store.set_mapping("n:3", "seed", "ryż", None, "ryż")  # bez trwałości → też do uzupełnienia


def test_unconfigured_shows_instructions(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, configured=False)
    r = client.get("/kupujemy")
    assert r.status_code == 200
    assert "Połącz z Zakupami" in r.text
    assert "Odśwież teraz" not in r.text


def test_top_ingredients_with_at_home_flag(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _seed(store)
    r = client.get("/kupujemy")
    assert r.status_code == 200
    assert "mleko" in r.text and "prawdopodobnie w domu" in r.text
    assert "Do zatwierdzenia" in r.text  # kolejka: n:2 + n:3
    assert r.headers["cache-control"] == "no-store"
    assert 'aria-current="page"' in r.text


def test_refresh_runs_sync_and_redirects_relative(tmp_path: Path) -> None:
    calls: list[bool] = []
    client, store = _client(tmp_path, runner=lambda force=False: calls.append(force) or 5)
    r = client.post("/kupujemy/odswiez")
    assert calls == [True]  # ręczne odświeżenie omija tygodniową bramkę katalogu
    assert r.status_code == 303 and r.headers["location"] == "../kupujemy?notice=ok"
    store.set_meta("last_sync_error", "Zakupy odrzuciły token")
    client2, store2 = _client(tmp_path, runner=lambda force=False: 0)
    store2.set_meta("last_sync_error", "Zakupy odrzuciły token")
    r = client2.post("/kupujemy/odswiez")
    assert r.headers["location"] == "../kupujemy?error=sync"
    assert "Zakupy odrzuciły token" in client2.get("/kupujemy?error=sync").text


def test_review_queue_contents(tmp_path: Path) -> None:
    _, store = _client(tmp_path)
    _seed(store)
    assert {r.code for r in review_queue(store)} == {"n:2", "n:3"}
    client, _ = _client(tmp_path)
    page = client.get("/kupujemy/zatwierdz")
    assert "tajemniczy wyrób" in page.text and "brakuje trwałości" in page.text
    assert 'href="../kupujemy"' in page.text and 'href="../"' in page.text


def test_approve_and_exclude(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _seed(store)
    r = client.post(
        "/kupujemy/zatwierdz",
        data={"code": "n:2", "action": "approve", "ingredient": "  Pierś  Z Indyka ", "durability": "fresh"},
    )
    assert r.status_code == 303 and r.headers["location"] == "zatwierdz"
    row = next(m for m in store.mappings() if m.code == "n:2")
    assert (row.status, row.ingredient, row.durability) == ("approved", "pierś z indyka", "fresh")
    client.post("/kupujemy/zatwierdz", data={"code": "n:3", "action": "exclude"})
    assert next(m for m in store.mappings() if m.code == "n:3").status == "excluded"
    assert review_queue(store) == []


def test_invalid_decisions_change_nothing(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _seed(store)
    for data in (
        {"code": "n:2", "action": "approve", "ingredient": "", "durability": "fresh"},
        {"code": "n:2", "action": "approve", "ingredient": "x", "durability": "zły"},
        {"code": "n:2", "action": "wat"},
        {"code": "n:1", "action": "exclude"},  # nie ma w kolejce
        {"code": "n:999", "action": "exclude"},
    ):
        r = client.post("/kupujemy/zatwierdz", data=data)
        assert r.status_code == 303 and "error=" in r.headers["location"]
    assert next(m for m in store.mappings() if m.code == "n:2").status == "pending"
    assert next(m for m in store.mappings() if m.code == "n:1").status == "rule"


def test_profile_page_has_nav(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    r = client.get("/")
    assert 'href="kupujemy"' in r.text and "Profile" in r.text


def test_seconds_until() -> None:
    now = datetime(2026, 11, 22, 10, 0, 0)
    assert seconds_until(now, "11:30") == 5400
    assert seconds_until(now, "03:30") == 17.5 * 3600  # jutro
    assert seconds_until(now, "10:00") == 24 * 3600  # dokładnie teraz → jutro


def test_static_links_resolve_from_every_page(tmp_path: Path) -> None:
    """Adresy względne (Ingress): z /kupujemy/zatwierdz statyki muszą wskazywać na /static/."""
    client, store = _client(tmp_path)
    _seed(store)
    assert 'href="../static/app.css' in client.get("/kupujemy/zatwierdz").text
    assert 'href="static/app.css' in client.get("/kupujemy").text
    assert 'href="static/app.css' in client.get("/").text


def test_review_form_requires_choosing_durability(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    _seed(store)
    html = client.get("/kupujemy/zatwierdz").text
    assert 'name="durability" required' in html and "wybierz…" in html


def test_refresh_survives_runner_exception(tmp_path: Path) -> None:
    def boom(force: bool = False) -> int:
        raise ValueError("Bearer secret-token")

    client, store = _client(tmp_path, runner=boom)
    r = client.post("/kupujemy/odswiez")
    assert r.status_code == 303 and r.headers["location"] == "../kupujemy?error=sync"
    assert "secret-token" not in (store.get_meta("last_sync_error") or "")


def test_default_runner_calls_sync_catalog_after_sync(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from dieta.web import kupujemy

    calls: list[str] = []
    monkeypatch.setattr(kupujemy, "sync", lambda *a, **k: calls.append("sync") or 5)
    forced: list[bool] = []
    monkeypatch.setattr(
        kupujemy, "sync_catalog", lambda *a, **k: (calls.append("catalog"), forced.append(k["force"]), 1)[2]
    )
    monkeypatch.setattr(kupujemy, "sync_shelf", lambda *a, **k: calls.append("shelf") or 1)
    monkeypatch.setattr(kupujemy, "sync_shelf_queue", lambda *a, **k: 0)
    s = Settings(data_dir=tmp_path, log_level="info", dev=True, zakupy_url="http://z:1", zakupy_token="x")
    assert kupujemy.default_runner(s, Store(s.db_path))() == 5
    assert calls == ["sync", "catalog", "shelf"]

    assert kupujemy.default_runner(s, Store(s.db_path))(force=True) == 5
    assert forced == [False, True]

    calls.clear()
    s2 = Settings(data_dir=tmp_path, log_level="info", dev=True, zakupy_url="", zakupy_token="")
    assert kupujemy.default_runner(s2, Store(s2.db_path))() == 5
    assert calls == ["sync"]


def test_default_runner_queues_prices_before_shelf_only_when_not_forced(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from dieta.web import kupujemy

    calls: list[str] = []
    monkeypatch.setattr(kupujemy, "sync", lambda *a, **k: 5)
    monkeypatch.setattr(kupujemy, "sync_catalog", lambda *a, **k: 1)
    monkeypatch.setattr(kupujemy, "sync_shelf", lambda *a, **k: calls.append("shelf") or 1)
    monkeypatch.setattr(kupujemy, "sync_shelf_queue", lambda *a, **k: calls.append("queue") or 0)
    s = Settings(data_dir=tmp_path, log_level="info", dev=True, zakupy_url="http://z:1", zakupy_token="x")
    kupujemy.default_runner(s, Store(s.db_path))()
    assert calls == ["queue", "shelf"]

    calls.clear()
    kupujemy.default_runner(s, Store(s.db_path))(force=True)
    assert calls == ["shelf"]


def _ceny_client(tmp_path: Path, monkeypatch, effect):  # type: ignore[no-untyped-def]
    from dieta.web import kupujemy

    seen: list[dict[str, object]] = []

    def fake(store, settings, today, *, plan_only=False, **kwargs):  # type: ignore[no-untyped-def]
        seen.append({"plan_only": plan_only})
        return effect(store)

    monkeypatch.setattr(kupujemy, "sync_shelf_queue", fake)
    client, store = _client(tmp_path, runner=lambda force=False: 0)
    return client, store, seen


def test_ceny_button_queues_plan_only_and_reports_count(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client, _, seen = _ceny_client(tmp_path, monkeypatch, lambda store: 3)
    r = client.post("/kupujemy/ceny")
    assert r.status_code == 303 and r.headers["location"] == "../kupujemy?notice=ceny-3"
    assert seen == [{"plan_only": True}]
    page = client.get("/kupujemy?notice=ceny-3")
    assert "Zlecono odczyt 3 cen" in page.text


def test_ceny_button_reports_busy_without_error(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def busy(store):  # type: ignore[no-untyped-def]
        store.set_meta("shelf_queue_status", "busy")
        return 0

    client, _, _ = _ceny_client(tmp_path, monkeypatch, busy)
    r = client.post("/kupujemy/ceny")
    assert r.headers["location"] == "../kupujemy?notice=ceny-busy"
    assert "Odczyt już trwa." in client.get("/kupujemy?notice=ceny-busy").text


def test_ceny_button_reports_error_from_meta(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def failed(store):  # type: ignore[no-untyped-def]
        store.set_meta("shelf_queue_error", "Zakupy zwróciły HTTP 500.")
        return 0

    client, _, _ = _ceny_client(tmp_path, monkeypatch, failed)
    r = client.post("/kupujemy/ceny")
    assert r.headers["location"] == "../kupujemy?error=ceny"
    assert "Nie udało się zlecić" in client.get("/kupujemy?error=ceny").text
