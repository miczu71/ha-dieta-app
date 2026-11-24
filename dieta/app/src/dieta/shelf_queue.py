"""Nocna kolejka odczytu cen Biedronki po EAN (`POST /api/v1/shelf` w Zakupach).

Kolejność: EAN-y składników zatwierdzonego planu (bieżący i następny tydzień), potem katalog.
Pomijamy EAN-y świeże (≤7 dni w ``shelf_item``) i „brak w Biedronce” (``meta.shelf_absent_json``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import date, timedelta

from dieta.dates import parse_iso_date
from dieta.settings import Settings
from dieta.shopping import CATALOG_PRICED_STATUSES, PRICED_MAPPING_STATUSES, _all_masses, build_context
from dieta.store import Store
from dieta.sync import MISSING_CONFIG
from dieta.zakupy_client import ShelfRequestResult, ZakupyError, post_shelf_request

QUEUE_LIMIT = 100
FRESH_DAYS = 7
BIEDRONKA_PREFIX = "b:"

PostFn = Callable[..., ShelfRequestResult]


def plan_ingredient_names(store: Store, today: date) -> set[str]:
    """Nazwy składników zatwierdzonych planów bieżącego i następnego tygodnia (jak w liście zakupów)."""
    monday = today - timedelta(days=today.weekday())
    names: set[str] = set()
    for week in (monday, monday + timedelta(days=7)):
        plan = store.plan(week)
        if plan is None or plan.status != "approved":
            continue
        ctx = build_context(store, today, week)
        for item in _all_masses(store, ctx, plan.id):
            names.update(mass.name for mass in item.masses)
    return names


def plan_eans(store: Store, today: date) -> list[str]:
    names = plan_ingredient_names(store, today)
    if not names:
        return []
    eans = {
        row.ean
        for row in store.catalog_map_rows()
        if row.status in CATALOG_PRICED_STATUSES and row.ingredient in names
    }
    for row in store.mappings():
        if (
            row.code.startswith(BIEDRONKA_PREFIX)
            and row.status in PRICED_MAPPING_STATUSES
            and row.ingredient in names
        ):
            eans.add(row.code[len(BIEDRONKA_PREFIX) :])
    return sorted(eans)


def catalog_eans(store: Store) -> list[str]:
    """Katalog: najpierw EAN-y zmapowane na składniki z przepisów, potem reszta katalogu."""
    used = set(store.used_ingredient_names())
    first = sorted(
        row.ean
        for row in store.catalog_map_rows()
        if row.status in CATALOG_PRICED_STATUSES and row.ingredient in used
    )
    rest = sorted(item.ean for item in store.catalog_items())
    return list(dict.fromkeys(first + rest))


def _fresh_eans(store: Store, today: date) -> set[str]:
    cutoff = today - timedelta(days=FRESH_DAYS)
    fresh: set[str] = set()
    for item in store.shelf_items():
        if (fetched := parse_iso_date(item.fetched_at)) is None:
            continue
        if fetched >= cutoff:
            fresh.add(item.ean)
    return fresh


def _absent_eans(store: Store) -> set[str]:
    try:
        raw = json.loads(store.get_meta("shelf_absent_json") or "[]")
    except ValueError:
        return set()
    return {str(ean) for ean in raw} if isinstance(raw, list) else set()


# Zakupy odrzucają CAŁE zlecenie (400), gdy choć jeden EAN nie pasuje — filtrujemy po swojej stronie.
_EAN_RE = re.compile(r"\d{8,13}")


def build_queue(store: Store, today: date, *, limit: int = QUEUE_LIMIT, plan_only: bool = False) -> list[str]:
    """Lista EAN do odczytu: plan, potem katalog (bez ``plan_only``), bez duplikatów, świeżych i „brak”."""
    skip = _fresh_eans(store, today) | _absent_eans(store)
    candidates = plan_eans(store, today) + ([] if plan_only else catalog_eans(store))
    queue = [ean for ean in dict.fromkeys(candidates) if ean not in skip and _EAN_RE.fullmatch(ean)]
    return queue[:limit]


def sync_shelf_queue(
    store: Store,
    settings: Settings,
    today: date,
    *,
    post: PostFn = post_shelf_request,
    plan_only: bool = False,
) -> int:
    """Zleca w Zakupach odczyt cen z kolejki; zwraca liczbę przyjętych EAN (``accepted``).

    Błędy i brak konfiguracji idą do ``meta.shelf_queue_error`` (bez wyjątku); 409 ``busy`` to
    ``meta.shelf_queue_status = "busy"`` bez błędu.
    """
    url, token = settings.zakupy_url, settings.zakupy_token
    if not url or not token:
        store.set_meta("shelf_queue_error", MISSING_CONFIG)
        return 0
    eans = build_queue(store, today, plan_only=plan_only)
    if not eans:
        store.set_meta("shelf_queue_at", today.isoformat())
        store.set_meta("shelf_queue_status", "empty")
        store.set_meta("shelf_queue_error", "")
        return 0
    try:
        result = post(url, token, eans)
    except ZakupyError as exc:
        store.set_meta("shelf_queue_error", str(exc))
        return 0
    store.set_meta("shelf_queue_at", today.isoformat())
    store.set_meta("shelf_queue_error", "")
    if result.busy:
        store.set_meta("shelf_queue_status", "busy")
        return 0
    store.set_meta("shelf_queue_status", "accepted")
    return result.accepted
