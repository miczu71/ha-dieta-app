"""Nocny potok katalogu: crawl włączonych źródeł → mapowanie składników przepisów → metadane AI.

Ten sam kod obsługuje „Odśwież teraz”. Seamy ``fetch``/``ask``/``sleep`` wstrzykiwane w testach.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import date

from dieta.catalog import CrawlResult, SleepFn, crawl, enrich_meta, make_meta_ask
from dieta.dictionary import AskFn, make_llm_ask
from dieta.fetcher import make_fetch
from dieta.settings import Settings
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
}
SOURCE_LABELS = {"respo": "Centrum Respo", "lidl": "Lidl Przepisy", "policzona": "Policzona Szama"}

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
    sleep: SleepFn = time.sleep,
) -> dict[str, CrawlResult]:
    """Crawl ``source`` (albo wszystkich włączonych), potem mapowanie składników i metadane AI."""
    names = [source] if source is not None else list(settings.sources)
    results: dict[str, CrawlResult] = {}
    for name in names:
        factory = SOURCE_FACTORIES.get(name)
        if factory is None:
            continue
        results[name] = crawl(
            store, factory(), fetch=fetch, today=today, limit=settings.crawl_daily_limit, sleep=sleep
        )
    map_recipe_ingredients(store, ask_ingredients)
    enrich_meta(store, ask_meta)
    return results


def default_crawl_runner(settings: Settings, store: Store) -> CrawlRunner:
    """Prawdziwy potok (sieć, LLM z opcji); jeden przebieg naraz (nocny i ręczny nie nakładają się)."""
    lock = threading.Lock()

    def run(source: str | None = None) -> dict[str, CrawlResult]:
        with lock:
            llm = bool(settings.llm_url and settings.llm_key)
            # fetch dławi sam (≤ 1/s), więc sleep crawla jest pusty — inaczej czekalibyśmy podwójnie
            return run_catalog(
                store,
                settings,
                source=source,
                fetch=make_fetch(),
                ask_ingredients=make_llm_ask(settings.llm_url, settings.llm_key) if llm else None,
                ask_meta=make_meta_ask(settings.llm_url, settings.llm_key) if llm else None,
                today=date.today(),
                sleep=lambda _: None,
            )

    return run
