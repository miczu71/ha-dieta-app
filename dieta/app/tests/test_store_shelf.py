import sqlite3
from pathlib import Path

from dieta.store import _MIGRATIONS, Store
from dieta.zakupy_client import ShelfItem


def _item(ean: str = "590001", price: float = 5.0, promo: bool = False) -> ShelfItem:
    return ShelfItem(
        ean,
        "Masło 200 g",
        0.2,
        "Kilogram",
        price,
        7.0,
        "2026-11-23",
        "2026-11-29",
        promo,
        False,
        "2026-11-23T03:00:00",
    )


def test_upsert_roundtrip_and_overwrite(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_shelf_items([_item(), _item("590002", 2.0)])
    assert [i.ean for i in store.shelf_items()] == ["590001", "590002"]
    store.upsert_shelf_items([_item(price=4.0, promo=True)])
    first = store.shelf_items()[0]
    assert first.price == 4.0 and first.is_promotion is True and first.card_only is False
    assert len(store.shelf_items()) == 2


def test_data_version_changes_on_write(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    before = store.data_version
    store.upsert_shelf_items([_item()])
    assert store.data_version > before


def test_migration_v8_to_v9_creates_table_and_backup_once(tmp_path: Path) -> None:
    path = tmp_path / "dieta.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:8], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.close()
    Store(path)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 9
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "shelf_item" in tables
    backup = tmp_path / "dieta.db.pre-v9"
    assert backup.exists()
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 8
    backup.write_text("stara kopia")
    Store(path)  # już v9: kopii nie nadpisujemy
    assert backup.read_text() == "stara kopia"
    assert not (tmp_path / "dieta.db.pre-v8").exists()  # v8 → v9 nie tworzy kopii v8
