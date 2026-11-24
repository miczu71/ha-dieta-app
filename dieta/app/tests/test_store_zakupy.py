import sqlite3
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from dieta.settings import Settings
from dieta.store import _MIGRATIONS, Profile, Store
from dieta.sync import stats, sync
from dieta.zakupy_client import Purchase, ZakupyError

TODAY = date(2026, 11, 22)


def _settings(tmp_path: Path, url: str = "http://f0987e0f-lidl:8099", token: str = "T-123") -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_level="info",
        dev=True,
        zakupy_url=url,
        zakupy_token=token,
    )


def _buy(code: str, name: str, category: str = "food", day: date = TODAY) -> Purchase:
    return Purchase(
        code=code,
        chain="lidl",
        name=name,
        names=(name,),
        purchases=1,
        spend=10.0,
        first=day,
        last=day,
        dates=(day,),
        category=category,
    )


class _Fetch:
    def __init__(self, items: list[Purchase] | None = None, error: Exception | None = None) -> None:
        self.items = items or []
        self.error = error
        self.calls = 0

    def __call__(self, url: str, token: str) -> list[Purchase]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.items


class _Ask:
    def __init__(self, reply: list[dict[str, Any]] | None) -> None:
        self.reply = reply
        self.calls: list[list[str]] = []

    def __call__(self, names: list[str]) -> list[dict[str, Any]] | None:
        self.calls.append(names)
        if self.reply is None:
            return None
        return [dict(r, i=i) for i, r in enumerate(self.reply)]


def test_migration_v1_to_v2_keeps_profile(tmp_path: Path) -> None:
    path = tmp_path / "dieta.db"
    conn = sqlite3.connect(path)
    conn.executescript(_MIGRATIONS[0])
    conn.execute(
        "INSERT INTO profile VALUES (1,'m',40,180.0,2,'lose',0.5,NULL,NULL,0.3)",
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()

    store = Store(path)
    profile = store.profile(1)
    assert (
        profile is not None
        and profile.birth_date.year == date.today().year - 40
        and profile.birth_date_approx
    )
    assert store.purchases() == []
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS)


def test_save_profile_still_works_after_migration(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    store.save_profile(
        Profile(1, "f", date(date.today().year - 30, 1, 1), 165.0, 2, "maintain", 0.0, None, None, 0.3),
    )
    assert store.profile(1) is not None


def test_sync_maps_seed_rule_ai_and_excluded(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    items = [
        _buy("c1", "Chleb tost. pszenny1"),  # seed skladnik
        _buy("c2", "Mleko bez laktozy 1l"),  # regula
        _buy("c3", "Dziwny produkt"),  # AI
        _buy("c4", "Piramidki smaku"),  # seed: slodycze -> excluded
        _buy("c5", "Papier toaletowy", category="drugstore"),  # non-food
    ]
    ask = _Ask([{"klasa": "skladnik", "skladnik": "Kasza Jaglana", "trwalosc": "dry"}])
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch(items), ask=ask)

    by_code = {m.code: m for m in store.mappings()}
    assert by_code["c1"].status == "seed"
    assert by_code["c1"].ingredient == "chleb tostowy pszenny"
    assert by_code["c2"].status == "rule"
    assert by_code["c2"].ingredient == "mleko"
    assert by_code["c3"].status == "ai"
    assert by_code["c3"].ingredient == "kasza jaglana"
    assert by_code["c3"].durability == "dry"
    assert by_code["c4"].status == "excluded"
    assert by_code["c5"].status == "excluded"
    assert ask.calls == [["dziwny produkt"]]
    assert store.get_meta("last_sync_error") == ""
    assert store.get_meta("last_sync_at") == TODAY.isoformat()


def test_sync_is_idempotent_and_does_not_reask_known_codes(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    items = [_buy("c3", "Dziwny produkt")]
    ask = _Ask([{"klasa": "skladnik", "skladnik": "kasza", "trwalosc": "dry"}])
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch(items), ask=ask)
    first = [(m.code, m.status, m.ingredient, m.durability) for m in store.mappings()]
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch(items), ask=ask)
    second = [(m.code, m.status, m.ingredient, m.durability) for m in store.mappings()]
    assert first == second
    assert len(ask.calls) == 1
    assert len(store.purchases()) == 1


def test_approved_and_excluded_survive_sync(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    items = [_buy("c3", "Dziwny produkt"), _buy("c6", "Coś innego")]
    ask = _Ask([{"klasa": "skladnik", "skladnik": "kasza", "trwalosc": "dry"}] * 2)
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch(items), ask=ask)
    store.approve("c3", "Kasza Gryczana", "chilled")
    store.exclude("c6")
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch(items), ask=ask)

    by_code = {m.code: m for m in store.mappings()}
    assert by_code["c3"].status == "approved"
    assert by_code["c3"].ingredient == "kasza gryczana"
    assert by_code["c3"].durability == "chilled"
    assert by_code["c6"].status == "excluded"


def test_ai_garbage_goes_to_pending_and_queue(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    ask = _Ask([{"klasa": "bzdura", "skladnik": 5, "trwalosc": "wieczne"}])
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch([_buy("c9", "Ciastka X")]), ask=ask)
    assert [m.code for m in store.pending()] == ["c9"]
    assert store.pending()[0].name == "Ciastka X"


def test_failed_llm_batch_goes_to_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    sync(
        store,
        _settings(tmp_path),
        TODAY,
        fetch=_Fetch([_buy("c9", "Ciastka X")]),
        ask=_Ask(None),
    )
    assert [m.code for m in store.pending()] == ["c9"]


def test_missing_llm_url_still_syncs_with_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch([_buy("c9", "Ciastka X")]), ask=None)
    assert [m.code for m in store.pending()] == ["c9"]


def test_fetch_error_keeps_snapshot_and_records_error(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch([_buy("c1", "Mleko bez laktozy 1l")]), ask=None)
    sync(store, _settings(tmp_path), TODAY, fetch=_Fetch(error=ZakupyError("zły token")), ask=None)
    assert [p.code for p in store.purchases()] == ["c1"]
    assert store.get_meta("last_sync_error") == "zły token"


def test_missing_token_skips_fetch(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    fetch = _Fetch([_buy("c1", "Mleko")])
    sync(store, _settings(tmp_path, token=""), TODAY, fetch=fetch, ask=None)
    assert fetch.calls == 0
    assert store.get_meta("last_sync_error")


def test_replace_purchases_drops_old_rows(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    store.replace_purchases([_buy("a", "A")])
    store.replace_purchases([_buy("b", "B")])
    assert [p.code for p in store.purchases()] == ["b"]
    restored = store.purchases()[0]
    assert restored.dates == (TODAY,)
    assert restored.first == TODAY and restored.last == TODAY


def test_stats_use_approved_mapping_and_shelf_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    old = TODAY - timedelta(days=11)
    sync(
        store,
        _settings(tmp_path),
        TODAY,
        fetch=_Fetch([_buy("c1", "Mleko bez laktozy 1l", day=old), _buy("c2", "Coś")]),
        ask=None,
    )
    store.approve("c2", "ryż", "dry")
    result = stats(store, _settings(tmp_path), TODAY)
    by_name = {s.ingredient: s for s in result}
    assert by_name["mleko"].at_home is False  # 11 dni > 10 dni chłodnia
    assert by_name["ryż"].at_home is True


def test_upsert_and_meta_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    assert store.get_meta("missing") is None
    store.set_meta("k", "v")
    assert store.get_meta("k") == "v"
    store.set_mapping("zz", "pending", None, None, "src")
    assert store.mapped_codes() == {"zz"}
    assert store.ingredient_durability("nieistnieje") is None


def test_migration_v6_to_v7_keeps_data_and_adds_columns(tmp_path: Path) -> None:
    path = tmp_path / "v6.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:6], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute(
        "INSERT INTO purchase VALUES ('c1','lidl','Mleko','[\"Mleko 1l\"]',3,9.0,"
        "'2026-01-01','2026-11-01','[\"2026-01-01\"]','food','x')"
    )
    conn.commit()
    conn.close()
    assert len(_MIGRATIONS) == 11

    store = Store(path)

    [p] = store.purchases()
    assert p.code == "c1" and p.spend == 9.0
    assert (p.unit_price, p.is_weight, p.last_unit_price) == (None, None, None)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 11
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "shopping_state" in tables


def test_replace_purchases_roundtrips_prices_and_old_response(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    new = replace(_buy("c1", "Kurczak"), unit_price=24.9, is_weight=True, last_unit_price=26.0)
    store.replace_purchases([new, _buy("c2", "Mleko")])
    got = {p.code: p for p in store.purchases()}
    assert (got["c1"].unit_price, got["c1"].is_weight, got["c1"].last_unit_price) == (24.9, True, 26.0)
    assert (got["c2"].unit_price, got["c2"].is_weight, got["c2"].last_unit_price) == (None, None, None)
    store.replace_purchases([replace(new, is_weight=False)])
    assert store.purchases()[0].is_weight is False


def test_shopping_state_set_clear_and_isolated_by_week(tmp_path: Path) -> None:
    store = Store(tmp_path / "dieta.db")
    assert store.shopping_states("2026-11-23") == {}
    store.set_shopping_state("2026-11-23", 5, "force")
    store.set_shopping_state("2026-11-23", 5, "force")  # idempotentne
    store.set_shopping_state("2026-11-23", 6, "checked")
    store.set_shopping_state("2026-11-30", 7, "sent")
    assert store.shopping_states("2026-11-23") == {"force": {5}, "checked": {6}}
    store.clear_shopping_state("2026-11-23", 5, "force")
    assert store.shopping_states("2026-11-23") == {"checked": {6}}
    assert store.shopping_states("2026-11-30") == {"sent": {7}}
    with pytest.raises(sqlite3.IntegrityError):
        store.set_shopping_state("2026-11-23", 1, "bogus")
