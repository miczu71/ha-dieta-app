"""Nocny potok katalogu: crawl włączonych źródeł → mapowanie składników przepisów → metadane AI.

Ten sam kod obsługuje „Odśwież teraz”. Seamy ``fetch``/``ask`` wstrzykiwane w testach.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date

from dieta.catalog import META_PROMPT, CrawlResult, crawl, enrich_meta
from dieta.dictionary import AskFn, llm_ask_from
from dieta.fetcher import make_fetch
from dieta.settings import Settings
from dieta.sources.aniagotuje import AniagotujeSource
from dieta.sources.base import Fetch, RecipeSource
from dieta.sources.lidl import LidlSource
from dieta.sources.policzona import PolicznaSource
from dieta.sources.respo import RespoSource
from dieta.store import Store
from dieta.sync import map_recipe_ingredients

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
) -> dict[str, CrawlResult]:
    """Crawl ``source`` (albo wszystkich włączonych), potem mapowanie składników i metadane AI."""
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
    return results


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
            )

    return run
