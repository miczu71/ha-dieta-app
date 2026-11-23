"""Katalog przepisów: metadane planera z reguł (+ AI), crawl źródeł z limitem i bez ponownych pobrań.

Sieć tylko przez wstrzyknięte ``fetch`` (tempo ≤ 1/s robi ``fetcher.make_fetch``); klucze API poza logami.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from dieta.dictionary import BATCH_SIZE, AskFn, normalize_name
from dieta.fetcher import FetchError
from dieta.sources.base import Fetch, Recipe, RecipeSource
from dieta.store import MEAL_CATEGORIES, RecipeMeta, Store

_log = logging.getLogger(__name__)

MAX_STORAGE_DAYS = 14
SKIP_TTL_DAYS = 30  # po tylu dniach strona „nie-przepis” jest pobierana ponownie
RATE_LIMIT_STOP = 3  # tyle kolejnych HTTP 429 (po backoffie fetchera) przerywa przebieg źródła

_CATEGORY_HINTS: dict[str, str] = {
    "breakfast": "breakfast",
    "śniadanie": "breakfast",
    "sniadanie": "breakfast",
    "lunch": "lunch",
    "obiad": "lunch",
    "dinner": "dinner",
    "kolacja": "dinner",
    "snack": "snack",
    "przekąska": "snack",
    "przekaska": "snack",
    "dessert": "dessert",
    "deser": "dessert",
    "ciasto": "dessert",
}

# (słowa kluczowe w tytule/tagach, kategoria, dni przechowywania, mrożenie); pierwsze trafienie.
_MEAL_RULES: tuple[tuple[tuple[str, ...], str | None, int | None, bool | None], ...] = (
    (("zupa", "zupy", "krem z", "rosół", "barszcz", "chłodnik"), "lunch", 4, True),
    (("gulasz", "bigos", "chili", "leczo", "curry"), "lunch", 4, True),
    (("sałatka", "salatka", "surówka", "surowka"), "lunch", 1, False),
    (
        ("owsianka", "jaglanka", "naleśniki", "nalesniki", "omlet", "jajecznica", "szakszuka", "granola"),
        "breakfast",
        2,
        False,
    ),
    (("ciasto", "ciasteczka", "brownie", "babka", "deser", "muffin", "sernik", "lody"), "dessert", 3, True),
    (("smoothie", "koktajl", "batonik", "hummus", "przekąska"), "snack", 3, False),
    (("kanapka", "tosty", "tost", "wrap"), None, 1, False),
    (("lasagne", "zapiekanka", "pieczeń", "pulpety", "kotlety", "klopsiki"), "dinner", 4, True),
)


def _norm_hint(hint: str | None) -> str | None:
    return None if hint is None else _CATEGORY_HINTS.get(normalize_name(hint))


def classify_meal(recipe: Recipe) -> tuple[str | None, int | None, bool | None]:
    """(kategoria posiłku, przechowywanie w dniach, mrożenie) z reguł; ``None`` = brak reguły."""
    haystack = " ".join((recipe.title, *recipe.tags, recipe.url)).lower()
    category = _norm_hint(recipe.category_hint)
    if category is None:
        for tag in recipe.tags:
            category = _norm_hint(tag)
            if category is not None:
                break
    days: int | None = None
    freezable: bool | None = None
    for keywords, rule_category, rule_days, rule_freeze in _MEAL_RULES:
        if any(re.search(rf"\b{re.escape(k)}", haystack) for k in keywords):
            category = category or rule_category
            days, freezable = rule_days, rule_freeze
            break
    return category, days, freezable


def meta_from_rules(recipe: Recipe) -> RecipeMeta:
    category, days, freezable = classify_meal(recipe)
    complete = category is not None and days is not None and freezable is not None
    return RecipeMeta(category, days, freezable, "rule" if complete else "pending")


@dataclass(frozen=True)
class CrawlResult:
    added: int
    skipped: int  # strony niebędące przepisem (zapamiętane, nie pobierane ponownie)
    errors: int
    already_known: int
    limit_reached: bool


def _used_today(store: Store, source: str, today: date) -> int:
    raw = store.get_meta(f"crawl_used:{source}") or ""
    day, _, count = raw.partition(":")
    return int(count) if day == today.isoformat() and count.isdigit() else 0


def _skip_active(value: str | None, today: date) -> bool:
    """Wpis ``crawl_skip`` (``<data ISO>[ powód]``) obowiązuje przez ``SKIP_TTL_DAYS`` dni."""
    try:
        since = date.fromisoformat((value or "").split(" ", 1)[0])
    except ValueError:
        return False
    return (today - since).days < SKIP_TTL_DAYS


def _record_counts(store: Store, name: str, errors: int, skipped: int) -> None:
    store.set_meta(f"last_crawl_errors:{name}", str(errors))
    store.set_meta(f"last_crawl_skipped:{name}", str(skipped))


def crawl(
    store: Store,
    source: RecipeSource,
    *,
    fetch: Fetch,
    today: date,
    limit: int,
) -> CrawlResult:
    """Pobiera nowe przepisy źródła; dzienny limit zapytań o nowe strony.

    Przepis raz pobrany nie jest pobierany ponownie; nie-przepis i trwałe błędy (4xx poza 429, robots) są
    pomijane przez ``SKIP_TTL_DAYS`` dni. Wyjątek per URL trafia do ``meta.last_crawl_error:<źródło>`` i nie
    przerywa pętli (błąd przejściowy wróci następnego dnia). Pusta lista URL to błąd (bez ``last_crawl_at``).
    """
    name = source.name
    error_key = f"last_crawl_error:{name}"
    cooldown_key = f"crawl_cooldown:{name}"
    if store.get_meta(cooldown_key) == today.isoformat():
        store.set_meta(error_key, "wstrzymane do jutra po HTTP 429 (serwis ogranicza tempo)")
        return CrawlResult(0, 0, 0, 0, False)
    store.set_meta(error_key, "")
    try:
        urls = source.list_urls(fetch=fetch)
    except Exception as exc:
        store.set_meta(error_key, f"list_urls: {type(exc).__name__}: {exc}"[:300])
        _record_counts(store, name, 1, 0)
        return CrawlResult(0, 0, 1, 0, False)
    if not urls:
        store.set_meta(error_key, "sitemapa nie zawiera przepisów")
        _record_counts(store, name, 1, 0)
        return CrawlResult(0, 0, 1, 0, False)

    known = store.recipe_source_ids(name)
    skip_prefix = f"crawl_skip:{name}:"
    skipped_urls = {
        key[len(skip_prefix) :]
        for key in store.meta_keys(skip_prefix)
        if _skip_active(store.get_meta(key), today)
    }
    used = _used_today(store, name, today)
    added = skipped = errors = already = rate_limited = 0
    limit_reached = False
    prefetch = getattr(source, "prefetched", None)  # strona z kanału: bez zapytania, poza dziennym limitem
    for url in urls:
        if url in known or url in skipped_urls:
            already += 1
            continue
        cached = prefetch(url) if prefetch is not None else None
        if cached is None:
            if used >= limit:
                limit_reached = True
                break
            used += 1
            store.set_meta(f"crawl_used:{name}", f"{today.isoformat()}:{used}")
        try:
            recipe = source.parse(url, cached if cached is not None else fetch(url))
            if recipe is None:
                store.set_meta(skip_prefix + url, today.isoformat())
                skipped += 1
                continue
            store.upsert_recipe(recipe, meta_from_rules(recipe))
            added += 1
            rate_limited = 0
        except Exception as exc:
            errors += 1
            store.set_meta(error_key, f"{url}: {type(exc).__name__}: {exc}"[:300])
            if isinstance(exc, FetchError) and exc.status == 429:
                used -= 1  # zapytanie odrzucone przez serwis nie zjada dziennego limitu
                store.set_meta(f"crawl_used:{name}", f"{today.isoformat()}:{used}")
                rate_limited += 1
                if rate_limited >= RATE_LIMIT_STOP:
                    store.set_meta(error_key, f"ograniczenie tempa (HTTP 429), przerwano: {url}"[:300])
                    store.set_meta(cooldown_key, today.isoformat())  # ponowna próba najwcześniej jutro
                    break
                continue
            rate_limited = 0
            if isinstance(exc, FetchError) and exc.permanent:
                store.set_meta(skip_prefix + url, f"{today.isoformat()} {exc}"[:200])
    _record_counts(store, name, errors, skipped)
    store.set_meta(f"last_crawl_at:{name}", today.isoformat())
    return CrawlResult(added, skipped, errors, already, limit_reached)


META_PROMPT = (
    "Dostajesz listę tytułów przepisów kulinarnych. Dla każdego zwróć obiekt JSON: "
    '{"i": numer, "kategoria": jedna z ' + str(list(MEAL_CATEGORIES)) + ', "dni": liczba całkowita 1-'
    f"{MAX_STORAGE_DAYS}"
    " (ile dni gotowe danie wytrzymuje w lodówce), "
    '"mrozenie": true albo false (czy nadaje się do zamrożenia)}. '
    "Odpowiedz WYŁĄCZNIE tablicą JSON, bez komentarza."
)


def _valid_days(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= MAX_STORAGE_DAYS else None


def enrich_meta(store: Store, ask: AskFn | None) -> int:
    """Uzupełnia braki metadanych (status pending) przez AI w paczkach po 25; zwraca liczbę uzupełnionych.

    Poprawne wartości → ``ai``; śmieci/braki zostają ``pending`` (kolejka zatwierdzania) i nie są pytane
    ponownie (``meta.recipe_meta_asked:<id>``). Ręcznie zatwierdzone przepisy nie są ruszane.
    """
    if ask is None:
        return 0
    asked = store.meta_keys("recipe_meta_asked:")
    todo = [p for p in store.pending_recipe_meta() if f"recipe_meta_asked:{p.id}" not in asked]
    done = 0
    for start in range(0, len(todo), BATCH_SIZE):
        batch = todo[start : start + BATCH_SIZE]
        reply = ask([p.title for p in batch])
        if reply is None:
            reply = ask([p.title for p in batch])
        if reply is None:
            continue  # awaria LLM: bez recipe_meta_asked, pozycje wrócą przy następnym przebiegu
        by_index: dict[int, dict[str, object]] = {}
        for item in reply:
            index = item.get("i") if isinstance(item, dict) else None
            if isinstance(index, int) and not isinstance(index, bool):
                by_index[index] = item
        for offset, pending in enumerate(batch):
            store.set_meta(f"recipe_meta_asked:{pending.id}", "1")
            row = by_index.get(offset, {})
            raw_category = row.get("kategoria")
            category = pending.meal_category or (
                raw_category if isinstance(raw_category, str) and raw_category in MEAL_CATEGORIES else None
            )
            days = pending.storage_days or _valid_days(row.get("dni"))
            raw_freeze = row.get("mrozenie")
            freezable = pending.freezable
            if freezable is None and isinstance(raw_freeze, bool):
                freezable = raw_freeze
            complete = category is not None and days is not None and freezable is not None
            store.set_recipe_meta(
                pending.id, RecipeMeta(category, days, freezable, "ai" if complete else "pending")
            )
            done += int(complete)
    _log.info("Katalog: AI uzupełniło metadane %d przepisów", done)
    return done
