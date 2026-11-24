import sqlite3
from pathlib import Path

from dieta.store import _MIGRATIONS, Store
from dieta.zakupy_client import CatalogItem


def _item(ean: str = "590001", name: str = "Mleko 2%", price: float = 3.5) -> CatalogItem:
    return CatalogItem(ean, name, "Mlekovita", 1.0, "Litre", price, "2026-11-20T08:00:00")


def _v7_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:7], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute("INSERT INTO ingredient (name, durability) VALUES ('ryż','dry')")
    conn.commit()
    conn.close()


def test_migration_v7_to_v9_creates_tables_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "dieta.db"
    _v7_db(path)
    store = Store(path)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 12
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"catalog_item", "catalog_map"} <= tables
    assert store.ingredient_durability("ryż") == "dry"
    backup = tmp_path / "dieta.db.pre-v8"
    assert backup.exists()
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 7


def test_backup_not_overwritten_and_not_created_for_fresh_db(tmp_path: Path) -> None:
    Store(tmp_path / "fresh.db")
    assert not (tmp_path / "fresh.db.pre-v8").exists()
    path = tmp_path / "dieta.db"
    _v7_db(path)
    (tmp_path / "dieta.db.pre-v8").write_text("stara kopia")
    Store(path)
    assert (tmp_path / "dieta.db.pre-v8").read_text() == "stara kopia"


def test_upsert_catalog_items_overwrites(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_catalog_items([_item(), _item("590002", "Masło")], "2026-11-21")
    store.upsert_catalog_items([_item(price=4.0)], "2026-11-22")
    items = {i.ean: i for i in store.catalog_items()}
    assert len(items) == 2 and items["590001"].price == 4.0


def test_catalog_map_and_pending_queue(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_catalog_items([_item(), _item("590002", "Masło extra")], "2026-11-21")
    store.set_catalog_map("590001", "pending", "Mleko", "mleko 2%")
    store.set_catalog_map("590002", "rule", "masło", "masło extra")
    [pending] = store.pending_catalog()
    assert (pending.ean, pending.name, pending.ingredient) == ("590001", "Mleko 2%", "mleko")
    rows = {r.ean: r for r in store.catalog_map_rows()}
    assert rows["590002"].status == "rule" and rows["590002"].ingredient == "masło"

    store.approve_catalog("590001", "mleko")
    assert store.pending_catalog() == []
    assert {r.ean: r.status for r in store.catalog_map_rows()}["590001"] == "approved"
    store.exclude_catalog("590002")
    rows = {r.ean: r for r in store.catalog_map_rows()}
    assert rows["590002"].status == "excluded" and rows["590002"].ingredient is None
    assert rows["590002"].source_name == "masło extra"
