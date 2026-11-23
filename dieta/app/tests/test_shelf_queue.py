import json
from datetime import date, timedelta
from pathlib import Path

from dieta import shelf_queue
from dieta.settings import Settings
from dieta.shelf_queue import build_queue, sync_shelf_queue
from dieta.store import Store
from dieta.zakupy_client import CatalogItem, ShelfItem, ShelfRequestResult, ZakupyError

TODAY = date(2026, 11, 23)


def _settings(tmp_path: Path, *, url: str = "http://zakupy:8099") -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_level="info",
        dev=True,
        zakupy_url=url,
        zakupy_token="x" if url else "",
    )


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "dieta.db")


def _catalog(store: Store, eans: list[str]) -> None:
    items = [CatalogItem(ean, f"Produkt {ean}", None, None, None, 4.0, "2026-11-22") for ean in eans]
    store.upsert_catalog_items(items, TODAY.isoformat())


def _shelf(ean: str, fetched: date) -> ShelfItem:
    return ShelfItem(ean, None, None, None, 5.0, None, None, None, False, False, fetched.isoformat())


def _plan_names(monkeypatch, names: set[str]) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(shelf_queue, "plan_ingredient_names", lambda store, today: set(names))


def test_build_queue_plan_first_then_catalog_without_duplicates(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _catalog(store, ["59000001", "59000002", "59000003", "59000004"])
    store.set_catalog_map("59000001", "approved", "mleko", "mleko 3,2%")
    store.set_mapping("b:59000002", "approved", "mleko", None, "mleko bio")
    _plan_names(monkeypatch, {"mleko"})

    assert build_queue(store, TODAY) == ["59000001", "59000002", "59000003", "59000004"]
    assert build_queue(store, TODAY, limit=3) == ["59000001", "59000002", "59000003"]
    assert build_queue(store, TODAY, plan_only=True) == ["59000001", "59000002"]


def test_build_queue_skips_fresh_and_absent(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _catalog(store, ["59000001", "59000002", "59000003", "59000004"])
    store.upsert_shelf_items([_shelf("59000001", TODAY - timedelta(days=1))])
    store.upsert_shelf_items([_shelf("59000002", TODAY - timedelta(days=10))])
    store.set_meta("shelf_absent_json", json.dumps(["59000003"]))
    _plan_names(monkeypatch, set())

    assert build_queue(store, TODAY) == ["59000002", "59000004"]


def test_build_queue_default_limit_is_100(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    eans = [f"59{i:06d}" for i in range(150)]
    _catalog(store, eans)
    _plan_names(monkeypatch, set())

    queue = build_queue(store, TODAY)
    assert len(queue) == 100 and queue == sorted(eans)[:100]


def test_sync_shelf_queue_without_config_sends_nothing(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _catalog(store, ["59000001"])
    _plan_names(monkeypatch, set())
    calls: list[list[str]] = []

    def post(url: str, token: str, eans: list[str]) -> ShelfRequestResult:
        calls.append(eans)
        return ShelfRequestResult(accepted=len(eans))

    assert sync_shelf_queue(store, _settings(tmp_path, url=""), TODAY, post=post) == 0
    assert calls == []
    assert store.get_meta("shelf_queue_error")


def test_sync_shelf_queue_success_writes_meta(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _catalog(store, ["59000001", "59000002"])
    _plan_names(monkeypatch, set())
    calls: list[list[str]] = []

    def post(url: str, token: str, eans: list[str]) -> ShelfRequestResult:
        calls.append(eans)
        return ShelfRequestResult(accepted=len(eans))

    assert sync_shelf_queue(store, _settings(tmp_path), TODAY, post=post) == 2
    assert calls == [["59000001", "59000002"]]
    assert store.get_meta("shelf_queue_at") == TODAY.isoformat()
    assert store.get_meta("shelf_queue_error") == ""


def test_sync_shelf_queue_error_goes_to_meta(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _catalog(store, ["59000001"])
    _plan_names(monkeypatch, set())

    def post(url: str, token: str, eans: list[str]) -> ShelfRequestResult:
        raise ZakupyError("Zakupy zwróciły HTTP 500.")

    assert sync_shelf_queue(store, _settings(tmp_path), TODAY, post=post) == 0
    assert store.get_meta("shelf_queue_error") == "Zakupy zwróciły HTTP 500."


def test_sync_shelf_queue_busy_is_not_an_error(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _catalog(store, ["59000001"])
    _plan_names(monkeypatch, set())

    def post(url: str, token: str, eans: list[str]) -> ShelfRequestResult:
        return ShelfRequestResult(busy=True)

    assert sync_shelf_queue(store, _settings(tmp_path), TODAY, post=post) == 0
    assert store.get_meta("shelf_queue_status") == "busy"
    assert store.get_meta("shelf_queue_error") == ""


def test_build_queue_drops_malformed_eans(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    _catalog(store, ["5900000000002", "05900000000002", "59000X0000002"])
    assert build_queue(store, TODAY) == ["5900000000002"]
