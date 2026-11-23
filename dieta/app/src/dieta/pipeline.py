"""Nocny potok katalogu: crawl włączonych źródeł → mapowanie składników przepisów → metadane AI.

Ten sam kod obsługuje „Odśwież teraz”. Seamy ``fetch``/``ask`` wstrzykiwane w testach.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from dieta.catalog import META_PROMPT, CrawlResult, crawl, enrich_meta
from dieta.dictionary import AskFn, llm_ask_from
from dieta.estimate import (
    COVERAGE_OK,
    Line,
    Measures,
    NutritionSeed,
    Per100,
    assumed_cap,
    estimate_recipe,
    is_free,
    is_noise,
    load_measures,
    load_nutrition_seed,
    to_grams,
)
from dieta.fetcher import make_fetch
from dieta.nutrition_lookup import (
    GRAMS_PROMPT,
    NUTRITION_PROMPT,
    OFF_INTERVAL_S,
    NutritionValue,
    OffClient,
    describe_error,
    fill_ingredient_nutrition,
    is_implausible,
    lookup_assumed_grams,
    make_off_fetch,
)
from dieta.settings import Settings
from dieta.sources.aniagotuje import AniagotujeSource
from dieta.sources.base import Fetch, RecipeSource
from dieta.sources.lidl import LidlSource
from dieta.sources.policzona import PolicznaSource
from dieta.sources.respo import RespoSource
from dieta.store import IngredientNutrition, RecipeLine, Store
from dieta.sync import map_recipe_ingredients

KCAL_100G_TOLERANCE = 0.25

log = logging.getLogger(__name__)

SOURCE_FACTORIES: dict[str, Callable[[], RecipeSource]] = {
    "respo": RespoSource,
    "lidl": LidlSource,
    "policzona": PolicznaSource,
    "aniagotuje": AniagotujeSource,
}
SOURCE_LABELS = {
    "respo": "Centrum Respo",
    "lidl": "Lidl Przepisy",
    "policzona": "Policzona Szama",
    "aniagotuje": "Ania Gotuje",
}

# zrodlo (None = wszystkie włączone) → wyniki per źródło
CrawlRunner = Callable[[str | None], dict[str, CrawlResult]]


def run_catalog(
    store: Store,
    settings: Settings,
    *,
    source: str | None = None,
    fetch: Fetch,
    ask_ingredients: AskFn | None,
    ask_meta: AskFn | None,
    today: date,
    ask_nutrition: AskFn | None = None,
    ask_grams: AskFn | None = None,
    fetch_off: Fetch | None = None,
) -> dict[str, CrawlResult]:
    """Crawl ``source`` (albo wszystkich włączonych), potem mapowanie, metadane AI i szacunek kcal."""
    names = [source] if source is not None else list(settings.sources)
    results: dict[str, CrawlResult] = {}
    for name in names:
        factory = SOURCE_FACTORIES.get(name)
        if factory is None:
            continue
        log.info("Katalog: start crawla %s", name)
        started = time.monotonic()
        result = crawl(store, factory(), fetch=fetch, today=today, limit=settings.crawl_daily_limit)
        results[name] = result
        log.info(
            "Katalog: koniec crawla %s po %.0f s: +%d, błędy %d, pominięte %d, znane %d, limit=%s, błąd: %s",
            name,
            time.monotonic() - started,
            result.added,
            result.errors,
            result.skipped,
            result.already_known,
            result.limit_reached,
            (store.get_meta(f"last_crawl_error:{name}") or "brak")[:120],
        )
    started = time.monotonic()
    log.info("Katalog: start mapowania składników (LLM=%s)", ask_ingredients is not None)
    linked = map_recipe_ingredients(store, ask_ingredients)
    log.info("Katalog: mapowanie składników: %d powiązań w %.0f s", linked, time.monotonic() - started)
    started = time.monotonic()
    enriched = enrich_meta(store, ask_meta)
    log.info("Katalog: metadane AI: %d w %.0f s", enriched, time.monotonic() - started)
    started = time.monotonic()
    stats = estimate_nutrition(store, ask_nutrition, fetch_off, ask_grams=ask_grams)
    log.info(
        "Katalog: szacunek kcal: %d przepisów (auto %d, do sprawdzenia %d, błędy %d); składniki: "
        "tabela %d, OFF %d, LLM %d, bez wpisu %d; linie z ilością domyślną: %d; %.0f s",
        stats.recipes,
        stats.auto,
        stats.pending,
        stats.errors,
        stats.table,
        stats.off,
        stats.llm,
        stats.failed,
        stats.grams,
        time.monotonic() - started,
    )
    return results


@dataclass
class EstimateStats:
    recipes: int = 0
    auto: int = 0
    pending: int = 0
    errors: int = 0
    table: int = 0
    off: int = 0
    llm: int = 0
    failed: int = 0
    grams: int = 0


def _per100(name: str, n: IngredientNutrition, seed: NutritionSeed) -> Per100 | None:
    if n.kcal is None or n.protein is None or n.carbs is None or n.fat is None:
        return None
    base = seed.find(name)
    extra = base[1] if base is not None else None
    return Per100(
        kcal=n.kcal,
        protein=n.protein,
        carbs=n.carbs,
        fat=n.fat,
        piece_g=n.piece_g if n.piece_g is not None else (extra.piece_g if extra else None),
        density_g_ml=extra.density_g_ml if extra else None,
        unit_g=extra.unit_g if extra else {},
    )


def estimate_nutrition(
    store: Store,
    ask: AskFn | None,
    fetch: Fetch | None,
    *,
    measures: Measures | None = None,
    ask_grams: AskFn | None = None,
    seed: NutritionSeed | None = None,
    off_interval: float = OFF_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> EstimateStats:
    """Szacunek kcal/makro dla przepisów bez kcal ze źródła (nie rusza ``kcal_source='source'``).

    ``ask`` = LLM dla wartości składników, ``ask_grams`` = LLM dla linii bez ilości, ``fetch`` = pobieranie
    z Open Food Facts (None = bez OFF). Bez LLM: tabela + OFF; składniki bez wpisu zostają nieznane
    (brak cichych zer). Wyjątek w jednym przepisie nie przerywa kroku.
    """
    stats = EstimateStats()
    measures = measures or load_measures()
    seed = seed or load_nutrition_seed()

    ingredients = [(i, n) for i, n in store.ingredients_needing_nutrition() if not is_free(n, measures)]
    off = OffClient(fetch, interval=off_interval, sleep=sleep, clock=clock) if fetch is not None else None

    def save(ingredient_id: int, v: NutritionValue) -> None:
        store.save_ingredient_nutrition(
            ingredient_id,
            kcal=v.kcal,
            protein=v.protein,
            carbs=v.carbs,
            fat=v.fat,
            piece_g=v.piece_g,
            source=v.source,
            confidence=v.confidence,
            status=v.status,
        )

    try:
        counts = fill_ingredient_nutrition(ingredients, seed, off, ask, save)
        stats.table, stats.off, stats.llm, stats.failed = counts.table, counts.off, counts.llm, counts.failed
    except Exception as exc:
        log.warning(
            "Szacunek kcal: uzupełnianie składników przerwane (%s)", describe_error(exc), exc_info=True
        )

    nutrition = store.ingredient_nutrition()
    per100: dict[int, Per100] = {}
    for ingredient_id, row in nutrition.items():
        per = _per100(row.name, row, seed)
        if per is None:
            continue
        if row.status == "pending" and is_implausible(row.name, per.kcal, per.protein, per.carbs, per.fat):
            continue  # jawnie niewiarygodne wartości liczą się jak nieznany składnik
        per100[ingredient_id] = per

    if ask_grams is not None:
        try:
            candidates: list[tuple[RecipeLine, str]] = []
            for ln in store.lines_without_amount():
                name = ln.ingredient_name or ln.name
                if not is_free(name, measures) and not is_noise(name, ln.text, measures):
                    candidates.append((ln, ln.text))
            for ln in store.lines_with_amount_unassumed():
                name = ln.ingredient_name or ln.name
                per = per100.get(ln.ingredient_id) if ln.ingredient_id is not None else None
                if per is None or is_free(name, measures):
                    continue
                grams, _ = to_grams(
                    ln.amount,
                    ln.unit,
                    measures=measures,
                    piece_g=per.piece_g,
                    density_g_ml=per.density_g_ml,
                    unit_g=per.unit_g,
                )
                if grams is None:
                    desc = " ".join(p for p in (f"{ln.amount:g}", ln.unit, name) if p)
                    candidates.append((ln, desc))
            lines = [ln for ln, _ in candidates]
            caps = [
                assumed_cap(ln.ingredient_name or ln.name, measures) if ln.amount is None else None
                for ln in lines
            ]
            answers = lookup_assumed_grams([(d, ln.recipe_title) for ln, d in candidates], ask_grams, caps)
            for index, grams in answers.items():
                store.set_grams_assumed(lines[index].recipe_id, lines[index].pos, grams)
                stats.grams += 1
        except Exception as exc:
            log.warning("Szacunek kcal: ilości domyślne przerwane (%s)", describe_error(exc), exc_info=True)

    for recipe in store.recipes_to_estimate():
        try:
            stored = store.recipe_lines(recipe.id)
            if not stored:
                continue
            est_lines = [
                Line(
                    name=ln.ingredient_name or ln.name,
                    amount=ln.amount,
                    unit=ln.unit,
                    ingredient_id=ln.ingredient_id,
                    grams_assumed=ln.grams_assumed,
                    text=ln.text,
                )
                for ln in stored
            ]
            est = estimate_recipe(est_lines, per100, recipe.servings, measures=measures)
            consistent = True
            # kcal/100 g ze strony dotyczy całego dania, także wody/lodu/octu, których est.total_g nie
            # liczy; przy linii „free” z ilością masa jest niepełna, więc kontroli nie da się uczciwie zrobić
            free_mass_skipped = any(is_free(ln.name, measures) and ln.amount is not None for ln in est_lines)
            if recipe.kcal_100g and not free_mass_skipped:
                if est.total_g <= 0:
                    consistent = False
                else:
                    computed = est.kcal * est.servings / est.total_g * 100
                    consistent = abs(computed - recipe.kcal_100g) / recipe.kcal_100g <= KCAL_100G_TOLERANCE
            auto = est.coverage >= COVERAGE_OK and consistent
            has_values = est.known > 0
            auto = auto and has_values
            saved = store.save_recipe_estimate(
                recipe.id,
                kcal=est.kcal if has_values else None,
                protein=est.protein if has_values else None,
                carbs=est.carbs if has_values else None,
                fat=est.fat if has_values else None,
                servings_estimated=est.servings_estimated,
                coverage=est.coverage,
                status="auto" if auto else "pending",
                unknown=est.unknown_names,
                assumed=est.assumed_lines,
            )
            if saved:
                stats.recipes += 1
                if auto:
                    stats.auto += 1
                else:
                    stats.pending += 1
        except Exception:
            stats.errors += 1
            log.warning("Szacunek kcal: błąd przepisu %d (%s)", recipe.id, recipe.title, exc_info=True)
    return stats


def default_crawl_runner(settings: Settings, store: Store) -> CrawlRunner:
    """Prawdziwy potok (sieć, LLM z opcji); jeden przebieg naraz (nocny i ręczny nie nakładają się)."""
    lock = threading.Lock()

    def run(source: str | None = None) -> dict[str, CrawlResult]:
        with lock:
            return run_catalog(
                store,
                settings,
                source=source,
                fetch=make_fetch(),
                ask_ingredients=llm_ask_from(settings),
                ask_meta=llm_ask_from(settings, META_PROMPT),
                today=date.today(),
                ask_nutrition=llm_ask_from(settings, NUTRITION_PROMPT),
                ask_grams=llm_ask_from(settings, GRAMS_PROMPT),
                fetch_off=make_off_fetch(),
            )

    return run
