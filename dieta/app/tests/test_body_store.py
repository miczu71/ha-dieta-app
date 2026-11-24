import sqlite3
from datetime import date
from pathlib import Path

from dieta.store import _MIGRATIONS, BodyRow, Store, ZeppAccount


def _row(ts: int, **kw: object) -> BodyRow:
    base: dict[str, object] = {"ts": ts, "source": "zepp", "weight_kg": 80.0}
    base.update(kw)
    return BodyRow(**base)  # type: ignore[arg-type]


def test_migration_from_v11(tmp_path: Path) -> None:
    path = tmp_path / "dieta.sqlite"
    with sqlite3.connect(path) as conn:
        for v, script in enumerate(_MIGRATIONS[:11], start=1):
            conn.executescript(f"BEGIN;{script}PRAGMA user_version = {v};COMMIT;")
        conn.execute("INSERT INTO weight (person_id, day, kg) VALUES (1, '2026-01-01', 70.0)")
    conn.close()
    store = Store(path)
    assert (tmp_path / "dieta.sqlite.pre-v12").exists()
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 12
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"zepp_account", "body_measurement"} <= tables
        assert check.execute("SELECT source FROM weight").fetchone()[0] == "manual"
    assert store.latest_weight(1) == 70.0


def test_zepp_account_roundtrip_and_repr(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    assert store.zepp_account(1) is None
    acc = ZeppAccount(1, "dev", '{"s": "COOKIESECRET"}', "u1", "TOKENSECRET", 7, "linked", None, None)
    store.save_zepp_account(acc)
    assert store.zepp_account(1) == acc
    assert "COOKIESECRET" not in repr(acc)
    assert "TOKENSECRET" not in repr(acc)
    store.set_zepp_last_sync(1, 123)
    store.set_zepp_status(1, "reconnect", last_error="boom")
    got = store.zepp_account(1)
    assert got is not None
    assert (got.last_sync_ts, got.status, got.last_error) == (123, "reconnect", "boom")


def test_delete_tokens_keeps_history(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    store.save_zepp_account(ZeppAccount(1, "dev", '{"a": 1}', "u", "tok", 1, "linked", None, None))
    assert store.add_body_measurements(1, [_row(1000)]) == 1
    store.delete_zepp_tokens(1)
    got = store.zepp_account(1)
    assert got is not None
    assert (got.cookies_json, got.app_token, got.status) == ("{}", None, "reconnect")
    assert len(store.body_measurements(1)) == 1


def test_body_measurements_dedupe_order_since_latest(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    assert store.latest_body(1) is None
    assert store.add_body_measurements(1, [_row(2000), _row(1000)]) == 2
    assert store.add_body_measurements(1, [_row(2000), _row(3000)]) == 1
    assert [r.ts for r in store.body_measurements(1)] == [1000, 2000, 3000]
    assert [r.ts for r in store.body_measurements(1, since_ts=2000)] == [2000, 3000]
    latest = store.latest_body(1)
    assert latest is not None and latest.ts == 3000
    assert store.body_measurements(2) == []


def test_add_weight_if_not_manual(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.sqlite")
    d = date(2026, 5, 1)
    assert store.add_weight_if_not_manual(1, d, 80.0) is True
    assert store.add_weight_if_not_manual(1, d, 79.0) is True  # zepp nadpisuje zepp
    assert store.weights(1) == [(d, 79.0)]
    store.add_weight(1, d, 75.0)  # ręczny przejmuje
    assert store.add_weight_if_not_manual(1, d, 70.0) is False
    assert store.weights(1) == [(d, 75.0)]
