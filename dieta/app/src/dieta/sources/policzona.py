"""Policzona Szama (policzona-szama.pl/blogs/przepisy/) — blog Shopify bez JSON-LD Recipe.

Port logiki `scripts/e0_policzona.py` (spike E0): parser HTML artykułu, blok „SKŁADNIKI NA N PORCJE”,
lista „nazwa – ilość”, wartości odżywcze z tekstu artykułu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from dieta.sources.base import Fetch, RawIngredient, Recipe
from dieta.sources.jsonld import parse_ingredient, recipe_urls, sitemap_locs, split_amount_unit

SITEMAP_URL = "https://policzona-szama.pl/sitemap_articles_1.xml"
URL_MARKER = "/blogs/przepisy/"
MAX_INGREDIENTS = 40
NUM_WORDS = {
    "jedna": 1, "dwie": 2, "dwa": 2, "trzy": 3, "cztery": 4, "pięć": 5, "sześć": 6, "siedem": 7, "osiem": 8,
}  # fmt: skip
_YIELD_RE = re.compile(r"(\d+|" + "|".join(NUM_WORDS) + r")\s+(?:duż\w+\s+)?(porcj|sztuk|szt|gołąbk)", re.I)
_SECTION_END_RE = re.compile(r"UŻYTE WYPOSAŻENIE|PRZYGOTOWANIE|DO PODANIA")

_HEADER_RE = re.compile(r"SK[ŁL]ADNIKI([^\n]*)\n")
_KCAL_SENTENCE_RE = re.compile(r"(?:dostarcza|porcja[^.]{0,40}|jedna[^.]{0,40})\s+(\d+)\s*kcal", re.I)
_KCAL_MACRO_RE = re.compile(r"(\d+)\s*kcal[^\n]{0,80}\n?[^\n]*?(\d+(?:,\d+)?)\s*g\s+białka")
_MACRO_RES = (
    ("protein", re.compile(r"(\d+(?:,\d+)?)\s*g\s+białka")),
    ("carbs", re.compile(r"(\d+(?:,\d+)?)\s*g\s+węglowodan")),
    ("fat", re.compile(r"(\d+(?:,\d+)?)\s*g\s+tłuszcz")),
)
_BATCH_RE = re.compile(r"z\s+\d+\s*g\s+[^.]{0,120}?będzie\s+mieć\s+(\d+)\s*kcal", re.I)
_BATCH_SCOPE = 200  # ile znaków za frazą partii szukać makro partii
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


@dataclass
class _Page:
    """Tekst artykułu (węzły stripowane, łączone „\\n” jak `get_text("\\n", strip=True)`) i meta."""

    h1: str = ""
    title: str = ""
    og_image: str | None = None
    article_parts: list[str] = field(default_factory=list)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = _Page()
        self._stack: list[str] = []
        self._skip = 0
        self._capture: str | None = None  # "h1" | "title" | None
        self._seen_h1 = False
        self._seen_title = False
        self._art_depth: int | None = None
        self._art_done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag == "meta":
            if attr.get("property") == "og:image" and self.page.og_image is None:
                self.page.og_image = attr.get("content") or None
            return
        if tag in _VOID:
            return
        self._stack.append(tag)
        if self._art_depth is None and "article" in attr.get("class", ""):
            self._art_depth = len(self._stack)
        if tag == "h1" and not self._seen_h1:
            self._seen_h1, self._capture = True, "h1"
        elif tag == "title" and not self._seen_title:
            self._seen_title, self._capture = True, "title"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag in _VOID or tag not in self._stack:
            return
        while self._stack:
            if self._stack.pop() == tag:
                break
        if self._art_depth is not None and len(self._stack) < self._art_depth:
            self._art_done = True
        if tag in ("h1", "title"):
            self._capture = None

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if not text:
            return
        if self._capture == "h1":
            self.page.h1 += (" " if self.page.h1 else "") + text
        elif self._capture == "title":
            self.page.title += (" " if self.page.title else "") + text
        if self._art_depth is not None and not self._art_done:
            self.page.article_parts.append(text)


def _read_page(html: str) -> _Page:
    parser = _PageParser()
    parser.feed(html)
    parser.close()
    return parser.page


def _ingredient(line: str) -> RawIngredient:
    """„nazwa – ilość” → składnik; bez myślnika parsowany jak zwykły składnik."""
    name, sep, quantity = line.partition(" – ")
    if not sep:
        return parse_ingredient(line)
    amount, unit, _rest = split_amount_unit(quantity)
    return RawIngredient(text=line, amount=amount, unit=unit, name=name.strip())


def _ingredients(body: str) -> tuple[RawIngredient, ...]:
    end = _SECTION_END_RE.search(body)
    if end is None:
        return ()
    lines = [ln.strip() for ln in body[: end.start()].split("\n")]
    kept = [
        ln
        for ln in lines
        if ln and not ln.lower().startswith("przyprawy") and not ln.upper().startswith("SKŁADNIKI")
    ]
    return tuple(_ingredient(ln) for ln in kept[:MAX_INGREDIENTS])


def _number(text: str) -> float:
    return float(text.replace(",", "."))


def _first_number(pattern: re.Pattern[str], text: str) -> float | None:
    found = pattern.search(text)
    return _number(found.group(1)) if found else None


class PolicznaSource:
    name = "policzona"

    def list_urls(self, *, fetch: Fetch) -> list[str]:
        return recipe_urls(sitemap_locs(fetch(SITEMAP_URL)), URL_MARKER)

    def parse(self, url: str, html: str) -> Recipe | None:
        page = _read_page(html)
        text = "\n".join(page.article_parts)
        header = _HEADER_RE.search(text)
        if header is None:
            return None
        title = page.h1 or page.title
        if not title:
            return None

        yield_match = _YIELD_RE.search(header.group(1))
        servings: float | None = None
        if yield_match:
            raw = yield_match.group(1)
            servings = float(int(raw) if raw.isdigit() else NUM_WORDS[raw.lower()])

        flat = text.replace("\n", " ")
        kcal: float | None
        macros: dict[str, float | None] = {}
        batch = _BATCH_RE.search(flat)
        if batch is not None:
            # Wartości dotyczą całej partii (np. „z 600 g mięsa surowego będzie mieć 1135 kcal”), nie porcji.
            scope = flat[batch.end() : batch.end() + _BATCH_SCOPE]
            total_kcal = float(batch.group(1))
            totals = {key: _first_number(pattern, scope) for key, pattern in _MACRO_RES}
            if servings:
                kcal = total_kcal / servings
                macros = {key: (v / servings if v is not None else None) for key, v in totals.items()}
            else:
                kcal = None
                macros = {key: None for key, _ in _MACRO_RES}
        else:
            kcal_match = _KCAL_SENTENCE_RE.search(text) or _KCAL_MACRO_RE.search(flat)
            kcal = float(kcal_match.group(1)) if kcal_match else None
            macros = {key: _first_number(pattern, flat) for key, pattern in _MACRO_RES}

        return Recipe(
            source=self.name,
            source_id=url,
            title=title,
            url=url,
            image=page.og_image,
            servings=servings,
            kcal=kcal,
            protein=macros["protein"],
            carbs=macros["carbs"],
            fat=macros["fat"],
            minutes=None,
            tags=(),
            category_hint=None,
            ingredients=_ingredients(text[header.end() :]),
        )
