"""Synchronizacja snapshotu zakupów i mapowania składników; statystyki dla ekranu „Co kupujemy".

Ręczne `approved` i `excluded` nigdy nie są nadpisywane. Kody już zmapowane nie są ponownie wysyłane do LLM.
Błąd pobierania zapisuje `meta.last_sync_error` i zostawia poprzedni snapshot.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta

from dieta.catalog_map import map_catalog
from dieta.dictionary import (
    SEED_PATH,
    AskFn,
    Durability,
    classify_rules,
    load_seed,
    map_with_llm,
    normalize_name,
)
from dieta.estimate import load_nutrition_seed
from dieta.pricing import receipt_to_frisco_factor
from dieta.profile import IngredientStat, build_stats
from dieta.settings import Settings
from dieta.store import MappingStatus, Store
from dieta.zakupy_client import (
    CatalogItem,
    Purchase,
    ShelfItem,
    ZakupyError,
    fetch_catalog,
    fetch_shelf_full,
)

FetchFn = Callable[[str, str], list[Purchase]]
CatalogFetchFn = Callable[[str, str], list[CatalogItem]]
ShelfFetchFn = Callable[[str, str], tuple[list[ShelfItem], list[str]]]
CATALOG_REFRESH_DAYS = 7
MISSING_CONFIG = "Brak adresu albo tokena Zakupów w opcjach Diety."
EMPTY_CATALOG = "Zakupy zwróciły pusty katalog."


def _no_ask(names: list[str]) -> list[dict[str, object]] | None:
    return None


def _resolve(store: Store, ingredient: str, hint: Durability | None) -> Durability | None:
    """Trwałość składnika: podpowiedź, potem już zapisana w słowniku, potem reguły."""
    return hint or store.ingredient_durability(ingredient) or classify_rules(ingredient)[1]


def _map_new(store: Store, purchases: list[Purchase], ask: AskFn | None) -> None:
    done = store.mapped_codes()
    seed = load_seed(SEED_PATH)
    unresolved: list[Purchase] = []
    for purchase in purchases:
        if purchase.code in done:
            continue
        name = normalize_name(purchase.name)
        if purchase.category != "food":
            store.set_mapping(purchase.code, "excluded", None, None, name)
            continue
        entry = seed.get(name)
        if entry is not None:
            if entry.klasa != "skladnik":
                store.set_mapping(purchase.code, "excluded", None, None, name)
            elif entry.skladnik:
                durability = _resolve(store, entry.skladnik, None)
                store.set_mapping(purchase.code, "seed", entry.skladnik, durability, name)
            else:
                store.set_mapping(purchase.code, "pending", None, None, name)
            continue
        ingredient, rule_durability = classify_rules(name)
        if ingredient is not None:
            durability = _resolve(store, ingredient, rule_durability)
            store.set_mapping(purchase.code, "rule", ingredient, durability, name)
            continue
        unresolved.append(purchase)

    if not unresolved:
        return
    names = list(dict.fromkeys(normalize_name(p.name) for p in unresolved))
    results = {r.name: r for r in map_with_llm(names, ask=ask or _no_ask)}
    for purchase in unresolved:
        name = normalize_name(purchase.name)
        result = results[name]
        durability = None
        if result.ingredient is not None:
            durability = _resolve(store, result.ingredient, result.durability)
        status: MappingStatus = "ai"
        if result.ingredient is None or durability is None:
            status = "pending"
        store.set_mapping(purchase.code, status, result.ingredient, durability, name)


def sync(
    store: Store,
    settings: Settings,
    today: date,
    *,
    fetch: FetchFn,
    ask: AskFn | None,
) -> int:
    """Pobiera zakupy, zapisuje snapshot i mapuje nowe kody.

    Zwraca liczbę produktów; 0 przy błędzie (wtedy meta.last_sync_error jest ustawione).
    """
    url, token = settings.zakupy_url, settings.zakupy_token
    if not url or not token:
        store.set_meta("last_sync_error", MISSING_CONFIG)
        return 0
    try:
        purchases = fetch(url, token)
    except ZakupyError as exc:
        store.set_meta("last_sync_error", str(exc))
        return 0
    store.replace_purchases(purchases)
    _map_new(store, purchases, ask)
    store.set_meta("last_sync_at", today.isoformat())
    store.set_meta("last_sync_error", "")
    return len(purchases)


def _piece_g_by_ingredient(store: Store) -> dict[str, float]:
    """Masa sztuki składnika (baza, w razie braku tabela startowa) — do ceny pakowanych bez gramatury."""
    seed = load_nutrition_seed()
    result: dict[str, float] = {}
    for row in store.ingredient_nutrition().values():
        found = seed.find(row.name)
        piece_g = row.piece_g if row.piece_g is not None else (found[1].piece_g if found else None)
        if piece_g:
            result[row.name] = piece_g
    return result


def _catalog_fresh(store: Store, today: date) -> bool:
    last = store.get_meta("catalog_sync_at")
    if not last:
        return False
    try:
        return date.fromisoformat(last) >= today - timedelta(days=CATALOG_REFRESH_DAYS)
    except ValueError:
        return False


def sync_catalog(
    store: Store,
    settings: Settings,
    today: date,
    *,
    fetch: CatalogFetchFn = fetch_catalog,
    ask: AskFn | None = None,
    force: bool = False,
) -> int:
    """Pobiera katalog Frisco, upsertuje snapshot, mapuje nowe EAN i przelicza współczynnik cen.

    Bez ``force`` nic nie robi (zwraca 0, nie rusza ``catalog_sync_error``), gdy poprzednie pobranie
    było najwyżej 7 dni temu — nocna pętla odświeża katalog raz w tygodniu.

    Wiersze nieobecne w odpowiedzi zostają (``observed_at`` mówi o świeżości). Zwraca liczbę produktów;
    0 przy błędzie (wtedy ``meta.catalog_sync_error`` jest ustawione, bez wyjątku).
    """
    if not force and _catalog_fresh(store, today):
        return 0
    url, token = settings.zakupy_url, settings.zakupy_token
    if not url or not token:
        store.set_meta("catalog_sync_error", MISSING_CONFIG)
        return 0
    try:
        items = fetch(url, token)
    except ZakupyError as exc:
        store.set_meta("catalog_sync_error", str(exc))
        return 0
    if not items:
        store.set_meta("catalog_sync_error", EMPTY_CATALOG)
        return 0
    store.upsert_catalog_items(items, today.isoformat())
    mapped = {row.ean for row in store.catalog_map_rows()}
    fresh = [i for i in store.catalog_items() if i.ean not in mapped]
    for decision in map_catalog(fresh, store.used_ingredient_names(), ask):
        store.set_catalog_map(decision.ean, decision.status, decision.ingredient, decision.source_name)
    mappings = store.mappings()
    factor = receipt_to_frisco_factor(
        store.purchases(),
        store.catalog_items(),
        _piece_g_by_ingredient(store),
        ingredient_by_code={r.code: r.ingredient for r in mappings if r.ingredient is not None},
        durability_by_code={r.code: r.durability for r in mappings if r.durability is not None},
    )
    store.set_meta(
        "frisco_factor_json",
        json.dumps({"value": factor.value, "n": factor.n, "by_durability": factor.by_durability}),
    )
    store.set_meta("catalog_sync_at", today.isoformat())
    store.set_meta("catalog_sync_error", "")
    return len(items)


def sync_shelf(
    store: Store,
    settings: Settings,
    today: date,
    *,
    fetch: ShelfFetchFn = fetch_shelf_full,
) -> int:
    """Pobiera cache cen Biedronki z Zakupów (tani GET) i upsertuje snapshot.

    Jeden GET: zapisuje też listę EAN „brak w Biedronce” do ``meta.shelf_absent_json``.
    Zwraca liczbę wierszy; 0 przy błędzie (``meta.shelf_sync_error``, bez wyjątku).
    """
    url, token = settings.zakupy_url, settings.zakupy_token
    if not url or not token:
        store.set_meta("shelf_sync_error", MISSING_CONFIG)
        return 0
    try:
        items, absent = fetch(url, token)
    except ZakupyError as exc:
        store.set_meta("shelf_sync_error", str(exc))
        return 0
    store.upsert_shelf_items(items)
    store.set_meta("shelf_absent_json", json.dumps(sorted(set(absent))))
    store.set_meta("shelf_sync_at", today.isoformat())
    store.set_meta("shelf_sync_error", "")
    return len(items)


def stats(store: Store, settings: Settings, today: date) -> list[IngredientStat]:
    """Składniki z zakupów ze statusem rule/seed/ai/approved, z flagą „prawdopodobnie w domu"."""
    mapping: dict[str, tuple[str, Durability | None]] = {}
    for row in store.mappings():
        if row.status in ("rule", "seed", "ai", "approved") and row.ingredient is not None:
            mapping[row.code] = (row.ingredient, row.durability)
    return build_stats(store.purchases(), mapping, today, settings.shelf_days)


def map_recipe_ingredients(store: Store, ask: AskFn | None) -> int:
    """Mapuje składniki przepisów na słownik: seed → reguły → AI (paczki 25) → pending.

    Wynik zapisany w ``mapping`` pod kodem ``ing:<nazwa>`` (nie pytamy ponownie); powiązanie trafia do
    ``recipe_ingredient.ingredient_id``. Wątpliwe i ``excluded`` zostają nieznane (``NULL``). Ręczne wpisy
    ``approved``/``excluded`` nie są nadpisywane. Zwraca liczbę powiązanych nazw.
    """
    seed = load_seed(SEED_PATH)
    existing = {row.code: row for row in store.mappings()}
    linked = 0
    unresolved: list[str] = []
    for raw in store.unlinked_ingredient_names():
        name = normalize_name(raw)
        code = f"ing:{name}"
        row = existing.get(code)
        if row is not None:
            if row.ingredient is not None and row.status in ("rule", "seed", "ai", "approved"):
                store.link_recipe_ingredient(raw, row.ingredient, row.durability)
                linked += 1
            continue
        entry = seed.get(name)
        if entry is not None and entry.klasa != "skladnik":
            store.set_mapping(code, "excluded", None, None, name)
            continue
        if entry is not None and entry.skladnik:
            durability = _resolve(store, entry.skladnik, None)
            store.set_mapping(code, "seed", entry.skladnik, durability, name)
            store.link_recipe_ingredient(raw, entry.skladnik, durability)
            linked += 1
            continue
        ingredient, rule_durability = classify_rules(name)
        if ingredient is not None:
            durability = _resolve(store, ingredient, rule_durability)
            store.set_mapping(code, "rule", ingredient, durability, name)
            store.link_recipe_ingredient(raw, ingredient, durability)
            linked += 1
            continue
        unresolved.append(raw)

    if not unresolved or ask is None:
        return linked  # bez LLM nic nie zapisujemy jako pending — spróbujemy po jego skonfigurowaniu
    names = list(dict.fromkeys(normalize_name(r) for r in unresolved))
    results = {r.name: r for r in map_with_llm(names, ask=ask)}
    for raw in unresolved:
        name = normalize_name(raw)
        result = results[name]
        if not result.answered:
            continue  # awaria LLM: bez wpisu, nazwa wróci przy następnym przebiegu (jak enrich_meta)
        code = f"ing:{name}"
        durability = _resolve(store, result.ingredient, result.durability) if result.ingredient else None
        if result.ingredient is None:
            excluded = result.klasa is not None and result.klasa != "skladnik"
            store.set_mapping(code, "excluded" if excluded else "pending", None, None, name)
            continue
        store.set_mapping(code, "ai", result.ingredient, durability, name)
        store.link_recipe_ingredient(raw, result.ingredient, durability)
        linked += 1
    return linked
