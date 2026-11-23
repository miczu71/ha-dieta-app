import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from dieta.store import Profile, Store


def _profile(person_id: int = 1, **overrides: object) -> Profile:
    base: dict[str, object] = {
        "person_id": person_id,
        "sex": "m",
        "age": 40,
        "height_cm": 180.0,
        "pal_level": 2,
        "goal": "lose",
        "pace_kg_week": 0.5,
        "kcal_override": None,
        "protein_g_kg": None,
        "fat_pct": 0.3,
    }
    base.update(overrides)
    return Profile(**base)  # type: ignore[arg-type]


def test_profile_is_none_when_missing(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    assert store.profile(1) is None


def test_save_and_read_profile(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    prof = _profile(kcal_override=1800, protein_g_kg=1.8)
    store.save_profile(prof)
    assert store.profile(1) == prof


def test_save_profile_overwrites(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    store.save_profile(_profile())
    updated = _profile(goal="maintain", pace_kg_week=0.0, kcal_override=2100)
    store.save_profile(updated)
    assert store.profile(1) == updated
    assert store.profile(2) is None


def test_add_weight_upserts_same_day(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    day = date(2026, 11, 1)
    store.add_weight(1, day, 80.0)
    store.add_weight(1, day, 79.5)
    assert store.weights(1) == [(day, 79.5)]


def test_weights_window_is_last_n_days_from_latest_entry(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    start = date(2026, 1, 1)
    for offset in range(120):
        store.add_weight(1, start + timedelta(days=offset), 80.0 - offset * 0.01)
    latest = start + timedelta(days=119)

    window = store.weights(1, days=90)

    assert len(window) == 90
    assert window[0][0] == latest - timedelta(days=89)
    assert window[-1][0] == latest
    assert [d for d, _ in window] == sorted(d for d, _ in window)


def test_weights_window_anchors_on_latest_not_today(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    store.add_weight(1, date(2026, 1, 1), 81.0)
    store.add_weight(1, date(2026, 1, 3), 80.0)
    store.add_weight(1, date(2026, 3, 1), 79.0)

    assert store.weights(1, days=5) == [(date(2026, 3, 1), 79.0)]


def test_weights_are_per_person(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    store.add_weight(1, date(2026, 5, 1), 70.0)
    store.add_weight(2, date(2026, 5, 2), 60.0)
    assert store.weights(1) == [(date(2026, 5, 1), 70.0)]
    assert store.weights(2) == [(date(2026, 5, 2), 60.0)]


def test_weights_empty_for_new_person(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    assert store.weights(2) == []


def test_latest_weight(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    assert store.latest_weight(1) is None
    store.add_weight(1, date(2026, 6, 10), 77.0)
    store.add_weight(1, date(2026, 6, 12), 76.4)
    store.add_weight(1, date(2026, 6, 11), 76.9)
    assert store.latest_weight(1) == 76.4
    assert store.latest_weight(2) is None


def test_migration_from_empty_file_sets_user_version(tmp_path: Path) -> None:
    path = tmp_path / "dieta.sqlite"
    assert not path.exists()
    Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"profile", "weight"} <= tables


def test_reopen_keeps_data_and_does_not_remigrate(tmp_path: Path) -> None:
    path = tmp_path / "dieta.sqlite"
    first = Store(path)
    first.save_profile(_profile())
    first.add_weight(1, date(2026, 7, 4), 82.2)

    second = Store(path)
    assert second.profile(1) == _profile()
    assert second.latest_weight(1) == 82.2
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8


def test_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "dieta.sqlite"
    Store(path)
    assert path.exists()


def test_invalid_person_id_raises_value_error(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.sqlite")
    with pytest.raises(ValueError):
        store.profile(3)
    with pytest.raises(ValueError):
        store.save_profile(_profile(person_id=3))
    with pytest.raises(ValueError):
        store.add_weight(3, date(2026, 1, 1), 80.0)
    with pytest.raises(ValueError):
        store.weights(0)
    with pytest.raises(ValueError):
        store.latest_weight(3)
