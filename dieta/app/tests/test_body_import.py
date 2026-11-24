from datetime import date, datetime
from pathlib import Path

from dieta.body import import_records
from dieta.store import Store
from dieta.zepp import BodyRecord


def _rec(ts: int, weight: float | None = 80.0, **kw: object) -> BodyRecord:
    base: dict[str, object] = {
        "ts": ts, "device_id": "d", "weight_kg": weight, "fat_pct": 20.0, "water_pct": 55.0,
        "bone_kg": 3.0, "muscle_rate": 40.0, "visceral": 8.0, "bmr_kcal": 1700.0, "bmi": 24.0,
        "impedance": 500.0, "body_score": 80.0, "metabolic_age": 30.0, "raw": {"b": 1, "a": 2},
    }  # fmt: skip
    base.update(kw)
    return BodyRecord(**base)  # type: ignore[arg-type]


def _ts(y: int, m: int, d: int, h: int = 8) -> int:
    return int(datetime(y, m, d, h).timestamp())


def test_idempotent_and_mapping(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    recs = [_rec(_ts(2026, 5, 1))]
    r1 = import_records(store, 1, recs)
    assert (r1.new, r1.skipped, r1.days) == (1, 0, 1)
    r2 = import_records(store, 1, recs)
    assert (r2.new, r2.skipped) == (0, 1)
    assert store.weights(1) == [(date(2026, 5, 1), 80.0)]
    row = store.latest_body(1)
    assert row is not None
    assert row.source == "zepp" and row.muscle_pct == 40.0
    assert row.raw_json == '{"a": 2, "b": 1}'


def test_duplicate_ts_in_batch(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    t = _ts(2026, 5, 1)
    r = import_records(store, 1, [_rec(t), _rec(t)])
    assert r.new == 1 and r.skipped == 1


def test_last_of_day_wins_and_overwrites_zepp(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    import_records(store, 1, [_rec(_ts(2026, 5, 1, 9), 81.0), _rec(_ts(2026, 5, 1, 7), 82.0)])
    assert store.weights(1) == [(date(2026, 5, 1), 81.0)]
    import_records(store, 1, [_rec(_ts(2026, 5, 1, 20), 79.5)])
    assert store.weights(1) == [(date(2026, 5, 1), 79.5)]


def test_manual_wins_and_add_weight_takes_over(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    store.add_weight(1, date(2026, 5, 1), 75.0)
    r = import_records(store, 1, [_rec(_ts(2026, 5, 1))])
    assert r.new == 1
    assert store.weights(1) == [(date(2026, 5, 1), 75.0)]
    import_records(store, 1, [_rec(_ts(2026, 5, 2))])
    store.add_weight(1, date(2026, 5, 2), 74.0)
    assert store.weights(1)[-1] == (date(2026, 5, 2), 74.0)


def test_record_without_weight_not_in_weight(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    r = import_records(store, 1, [_rec(_ts(2026, 5, 1), None)])
    assert r.new == 1 and r.days == 0
    assert store.weights(1) == []
    assert len(store.body_measurements(1)) == 1


def test_person_2_independent(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    import_records(store, 2, [_rec(_ts(2026, 5, 1))])
    assert store.weights(1) == [] and store.body_measurements(1) == []
    assert store.weights(2) == [(date(2026, 5, 1), 80.0)]
