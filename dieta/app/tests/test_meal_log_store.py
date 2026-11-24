import sqlite3
from datetime import date
from pathlib import Path

import pytest

from dieta.store import _MIGRATIONS, MealLogEntry, Store


def _entry(
    day: date = date(2026, 11, 23), person: int = 1, slot: str = "lunch", **kw: object
) -> MealLogEntry:
    fields: dict[str, object] = {
        "status": "eaten",
        "recipe_id": 3,
        "title": "Zupa",
        "portions": 1.5,
        "kcal": 500.0,
        "protein": 30.0,
        "fat": 10.0,
        "carbs": 60.0,
    }
    fields.update(kw)
    return MealLogEntry(day, person, slot, **fields)  # type: ignore[arg-type]


def test_migration_v5_to_v6_keeps_data_and_adds_meal_log(tmp_path: Path) -> None:
    path = tmp_path / "v5.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:5], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute("INSERT INTO weight (person_id, day, kg) VALUES (1, '2026-11-01', 80)")
    conn.execute(
        "INSERT INTO plan (week_start, status, seed, params_json, created_at) "
        "VALUES ('2026-11-23', 'draft', 1, '{}', 'x')"
    )
    conn.commit()
    conn.close()
    assert len(_MIGRATIONS) == 11

    store = Store(path)

    check = sqlite3.connect(path)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 11
    assert check.execute("SELECT COUNT(*) FROM meal_log").fetchone()[0] == 0
    assert check.execute("SELECT COUNT(*) FROM plan").fetchone()[0] == 1
    assert store.latest_weight(1) == 80.0


def test_set_and_read_entry(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.set_meal_log(_entry())
    assert store.meal_log(date(2026, 11, 23), date(2026, 11, 23)) == [_entry()]


def test_same_key_overwrites_state(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.set_meal_log(_entry())
    store.set_meal_log(_entry(status="skipped", kcal=0.0, protein=None, fat=None, carbs=None))
    rows = store.meal_log(date(2026, 11, 23), date(2026, 11, 23))
    assert len(rows) == 1
    assert rows[0].status == "skipped"
    assert rows[0].kcal == 0.0


def test_clear_removes_row_and_missing_is_noop(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.set_meal_log(_entry())
    store.clear_meal_log(date(2026, 11, 23), 1, "lunch")
    store.clear_meal_log(date(2026, 11, 23), 1, "lunch")
    assert store.meal_log(date(2026, 11, 1), date(2026, 11, 30)) == []


def test_range_is_inclusive_and_sorted(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.set_meal_log(_entry(date(2026, 11, 25)))
    store.set_meal_log(_entry(date(2026, 11, 23), person=2))
    store.set_meal_log(_entry(date(2026, 11, 23), person=1))
    store.set_meal_log(_entry(date(2026, 11, 26)))
    rows = store.meal_log(date(2026, 11, 23), date(2026, 11, 25))
    assert [(r.day.day, r.person_id) for r in rows] == [(23, 1), (23, 2), (25, 1)]


def test_invalid_person_rejected(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    with pytest.raises(ValueError):
        store.set_meal_log(_entry(person=3))


def test_check_constraints(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    with pytest.raises(sqlite3.IntegrityError):
        store.set_meal_log(_entry(status="bogus"))
    with pytest.raises(sqlite3.IntegrityError):
        store.set_meal_log(_entry(slot="brunch"))
