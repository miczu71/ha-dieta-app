"""Kontrakt konektora źródła przepisów (stały dla E3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

# Seam sieci: konektor nie otwiera połączeń sam; pobieranie dostaje z zewnątrz.
Fetch = Callable[[str], str]


@dataclass(frozen=True)
class RawIngredient:
    text: str
    amount: float | None
    unit: str | None  # znormalizowana jednostka bez kropki, np. "szt", "łyżki"
    name: str


@dataclass(frozen=True)
class Recipe:
    source: str  # nazwa źródła, jak w KNOWN_SOURCES (settings.py)
    source_id: str  # stabilny: URL kanoniczny
    title: str
    url: str
    image: str | None
    servings: float | None
    kcal: float | None  # NA PORCJĘ
    protein: float | None  # NA PORCJĘ, g
    carbs: float | None  # NA PORCJĘ, g
    fat: float | None  # NA PORCJĘ, g
    minutes: int | None
    tags: tuple[str, ...]
    category_hint: str | None
    ingredients: tuple[RawIngredient, ...]
    # kcal na 100 g gotowego dania, tylko kontrola szacunku; nie jest kcal na porcję
    kcal_100g: float | None = None


class RecipeSource(Protocol):
    name: str

    def list_urls(self, *, fetch: Fetch) -> list[str]:
        """URL-e przepisów z sitemapy/listingu. Sieć tylko przez `fetch`."""
        ...

    def parse(self, url: str, html: str) -> Recipe | None:
        """Przepis z HTML strony; None gdy to nie jest przepis."""
        ...

    def prefetched(self, url: str) -> str | None:
        """Strona pobrana już przy listowaniu (np. z kanału): bez zapytania i limitu w crawl; inaczej None."""
        ...
