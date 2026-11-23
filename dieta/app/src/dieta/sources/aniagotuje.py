"""Ania Gotuje (aniagotuje.pl/przepis/) — przepis w microdata (schema.org/Recipe), bez JSON-LD Recipe.

Wartości odżywcze na stronie są „W 100 g”, a nie na porcję, więc kcal/makro zostają None.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from dieta.sources.base import Fetch, Recipe
from dieta.sources.jsonld import _duration_minutes, parse_ingredient, parse_number, recipe_urls, sitemap_locs

SITEMAP_URL = "https://aniagotuje.pl/sitemap.xml"
URL_MARKER = "/przepis/"
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


@dataclass
class _Microdata:
    recipe_found: bool = False
    # (itemprop, znacznik, wartość) w kolejności dokumentu
    props: list[tuple[str, str, str]] = field(default_factory=list)


class _MicrodataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.data = _Microdata()
        self._stack: list[str] = []
        self._skip = 0
        # aktywne przechwycenia tekstu: (itemprop, znacznik, długość stosu przy otwarciu, bufor)
        self._open: list[tuple[str, str, int, list[str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if tag in ("script", "style"):
            self._skip += 1
            return
        if "schema.org/Recipe" in attr.get("itemtype", ""):
            self.data.recipe_found = True
        prop = attr.get("itemprop", "")
        if tag in _VOID:
            if prop and tag == "meta" and "content" in attr:
                self.data.props.append((prop, tag, attr["content"]))
            return
        self._stack.append(tag)
        if prop and tag != "link":
            self._open.append((prop, tag, len(self._stack), []))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if tag not in self._stack:
            return
        while self._stack:
            top = self._stack.pop()
            depth = len(self._stack) + 1
            while self._open and self._open[-1][2] >= depth:
                prop, name, _, buf = self._open.pop()
                self.data.props.append((prop, name, " ".join("".join(buf).split())))
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        for _, _, _, buf in self._open:
            buf.append(data)


def _first(props: list[tuple[str, str, str]], prop: str, tag: str | None = None) -> str | None:
    for name, kind, value in props:
        if name == prop and (tag is None or kind == tag) and value.strip():
            return value.strip()
    return None


def _split_keywords(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


class AniagotujeSource:
    name = "aniagotuje"

    def list_urls(self, *, fetch: Fetch) -> list[str]:
        return recipe_urls(sitemap_locs(fetch(SITEMAP_URL)), URL_MARKER)

    def parse(self, url: str, html: str) -> Recipe | None:
        parser = _MicrodataParser()
        parser.feed(html)
        parser.close()
        props = parser.data.props
        if not parser.data.recipe_found:
            return None
        title = _first(props, "name", "h1")
        if title is None:
            return None
        ingredients = tuple(
            parse_ingredient(text) for name, _, text in props if name == "recipeIngredient" and text
        )
        return Recipe(
            source=self.name,
            source_id=url,
            title=title,
            url=url,
            image=_first(props, "image"),
            servings=parse_number(_first(props, "recipeYield")),
            kcal=None,
            protein=None,
            carbs=None,
            fat=None,
            minutes=_duration_minutes(_first(props, "totalTime")),
            tags=_split_keywords(_first(props, "keywords") or ""),
            category_hint=_first(props, "recipeCategory"),
            ingredients=ingredients,
        )
