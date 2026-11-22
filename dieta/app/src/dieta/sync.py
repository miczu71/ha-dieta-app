"""Synchronizacja snapshotu zakupów i mapowania składników; statystyki dla ekranu „Co kupujemy".

Ręczne `approved` i `excluded` nigdy nie są nadpisywane. Kody już zmapowane nie są ponownie wysyłane do LLM.
Błąd pobierania zapisuje `meta.last_sync_error` i zostawia poprzedni snapshot.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from dieta.dictionary import (
    SEED_PATH,
    AskFn,
    Durability,
    classify_rules,
    load_seed,
    map_with_llm,
    normalize_name,
)
from dieta.profile import IngredientStat, build_stats
from dieta.settings import Settings
from dieta.store import MappingStatus, Store
from dieta.zakupy_client import Purchase, ZakupyError

FetchFn = Callable[[str, str], list[Purchase]]
MISSING_CONFIG = "Brak adresu albo tokena Zakupów w opcjach Diety."


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


def stats(store: Store, settings: Settings, today: date) -> list[IngredientStat]:
    """Składniki z zakupów ze statusem rule/seed/ai/approved, z flagą „prawdopodobnie w domu"."""
    mapping: dict[str, tuple[str, Durability | None]] = {}
    for row in store.mappings():
        if row.status in ("rule", "seed", "ai", "approved") and row.ingredient is not None:
            mapping[row.code] = (row.ingredient, row.durability)
    return build_stats(store.purchases(), mapping, today, settings.shelf_days)
