import json
from datetime import date
from pathlib import Path

from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import RecipeMeta, Store
from dieta.sync import sync_catalog
from dieta.zakupy_client import CatalogItem, Purchase, ZakupyError, fetch_catalog

TODAY = date(2026, 11, 22)
FIXTURE = Path(__file__).parent / "fixtures" / "frisco_catalog_small.json"


class _Opener:
    def __call__(self, req):  # type: ignore[no-untyped-def]
        class _R:
            def read(self) -> bytes:
                return FIXTURE.read_bytes()

            def close(self) -> None:
                return None

        return _R()


def _fetch(url: str, token: str) -> list[CatalogItem]:
    return fetch_catalog(url, token, opener=_Opener())


def _settings(tmp_path: Path, url: str = "http://z:8099", token: str = "T") -> Settings:
    return Settings(data_dir=tmp_path, log_level="info", dev=True, zakupy_url=url, zakupy_token=token)


def _store(tmp_path: Path, names: tuple[str, ...] = ("pomidory", "mleko", "cebula", "ryż", "sól")) -> Store:
    store = Store(tmp_path / "d.db")
    recipe = Recipe(
        "respo", "u1", "Danie", "u1", None, 2, 300, 10, 30, 5, 20, ("a", "b"), None,
        tuple(RawIngredient(n, None, None, n) for n in names),
    )  # fmt: skip
    store.upsert_recipe(recipe, RecipeMeta("lunch", 4, True, "rule"))
    for name in names:
        store.link_recipe_ingredient(name, name, None)
    return store


class _Ask:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.calls = 0

    def __call__(self, names: list[str]) -> list[dict[str, object]] | None:
        self.calls += 1
        return [{"i": i, "ok": self.verdict} for i in range(len(names))]


def _statuses(store: Store) -> dict[str, str]:
    return {r.ean: r.status for r in store.catalog_map_rows()}


def test_sync_without_ask_maps_only_strict(tmp_path: Path) -> None:
    store = _store(tmp_path)
    count = sync_catalog(store, _settings(tmp_path), TODAY, fetch=_fetch)
    assert count == 18  # bez obserwacji i bez ceny odpadają w kliencie
    statuses = _statuses(store)
    assert statuses["5900000000002"] == "rule"  # Pomidory
    assert statuses["5900000000005"] == "rule"  # Mleko
    assert "5900000000003" not in statuses  # klopsiki: luźne, brak LLM
    assert "5900000000012" not in statuses  # sól pomijana
    assert "5900000000009" not in statuses  # piwo
    assert store.get_meta("catalog_sync_at") == TODAY.isoformat()
    assert store.get_meta("catalog_sync_error") == ""
    assert json.loads(store.get_meta("frisco_factor_json") or "{}") == {
        "value": 1.0,
        "n": 0,
        "by_durability": {},
    }


def test_sync_with_ask_maps_loose_and_manual_survives(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ask = _Ask("tak")
    sync_catalog(store, _settings(tmp_path), TODAY, fetch=_fetch, ask=ask)
    assert _statuses(store)["5900000000003"] == "ai"
    # ręczna decyzja przeżywa kolejny sync (i nie jest ponownie pytana)
    store.exclude_catalog("5900000000003")
    store.approve_catalog("5900000000005", "mleko")
    calls = ask.calls
    sync_catalog(store, _settings(tmp_path), TODAY, fetch=_fetch, ask=_Ask("nie"))
    statuses = _statuses(store)
    assert statuses["5900000000003"] == "excluded" and statuses["5900000000005"] == "approved"
    assert ask.calls == calls


def test_sync_ask_watpliwe_goes_to_pending_queue(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sync_catalog(store, _settings(tmp_path), TODAY, fetch=_fetch, ask=_Ask("watpliwe"))
    names = {p.name for p in store.pending_catalog()}
    assert "Klopsiki w sosie pomidorowym" in names


def test_sync_computes_factor_from_pairs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    day = TODAY
    store.replace_purchases(
        [
            Purchase(
                "b:5900000000002",
                "biedronka",
                "Pomidory",
                ("Pomidory",),
                2,
                10.0,
                day,
                day,
                (day,),
                "food",
                unit_price=16.0,
                is_weight=True,
            ),
        ]
    )
    sync_catalog(store, _settings(tmp_path), TODAY, fetch=_fetch)
    factor = json.loads(store.get_meta("frisco_factor_json") or "{}")
    assert factor["n"] == 1
    assert abs(factor["value"] - 2.0) < 1e-9  # 1,6 zł/100 g vs Frisco 0,8 zł/100 g


def test_zakupy_error_goes_to_meta_without_raising(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def boom(url: str, token: str) -> list[CatalogItem]:
        raise ZakupyError("Brak połączenia z Zakupami.")

    assert sync_catalog(store, _settings(tmp_path), TODAY, fetch=boom) == 0
    assert store.get_meta("catalog_sync_error") == "Brak połączenia z Zakupami."
    assert store.catalog_items() == []


def test_missing_config_sets_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert sync_catalog(store, _settings(tmp_path, url=""), TODAY, fetch=_fetch) == 0
    assert store.get_meta("catalog_sync_error")


def test_stale_rows_stay_after_resync(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sync_catalog(store, _settings(tmp_path), TODAY, fetch=_fetch)
    sync_catalog(store, _settings(tmp_path), TODAY, fetch=lambda u, t: [], ask=None)
    assert len(store.catalog_items()) == 18
