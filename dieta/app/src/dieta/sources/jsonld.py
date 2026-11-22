"""Wspólny parser schema.org `Recipe` (JSON-LD) i składników w języku polskim.

Nie rzuca wyjątków na uszkodzonym JSON-LD: taki blok jest pomijany, a brak poprawnego `Recipe` daje None.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

from dieta.sources.base import Fetch, RawIngredient, Recipe

_BLOCK_RE = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")
_AMOUNT_RE = re.compile(
    r"\s*(?:(?P<whole>\d+)\s+(?P<fn>\d+)/(?P<fd>\d+)|(?P<n>\d+)/(?P<d>\d+)"
    r"|(?P<dec>\d+(?:[.,]\d+)?)|(?P<sym>[½¼¾⅓⅔]))"
)
_SYMBOL_FRACTIONS = {"½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3}
_UNIT_WORD_RE = re.compile(r"([^\W\d_]+)\.?(?:\s+|$)")
# Jednostki rozpoznawane po słowie; znormalizowane bez kropki i małymi literami.
UNITS = frozenset(
    {
        "g", "kg", "mg", "ml", "l", "dl", "cl",
        "szt", "łyżka", "łyżki", "łyżek", "łyżeczka", "łyżeczki", "łyżeczek",
        "szklanka", "szklanki", "szklanek", "szczypta", "szczypty", "szczypt",
        "ząbek", "ząbki", "ząbków", "pęczek", "pęczki", "pęczków",
        "plaster", "plastry", "plasterki", "plastrów", "opakowanie", "opakowania",
        "puszka", "puszki", "puszek", "kostka", "kostki", "kostek", "garść",
        "główka", "główki", "liść", "liście", "pajda", "pajdy", "kawałek", "kawałki",
    }
)  # fmt: skip


def split_amount_unit(text: str) -> tuple[float | None, str | None, str]:
    """Wyciąga (ilość, jednostka, reszta) z początku tekstu, np. "1/2 łyżeczki soli"."""
    match = _AMOUNT_RE.match(text)
    if match is None:
        return None, None, text.strip()
    if match.group("whole"):
        amount: float | None = int(match["whole"]) + int(match["fn"]) / int(match["fd"])
    elif match.group("n"):
        amount = int(match["n"]) / int(match["d"]) if int(match["d"]) else None
    elif match.group("dec"):
        amount = float(match["dec"].replace(",", "."))
    else:
        amount = _SYMBOL_FRACTIONS[match["sym"]]
    rest = text[match.end() :].strip()
    unit: str | None = None
    word = _UNIT_WORD_RE.match(rest)
    if word and word.group(1).lower() in UNITS:
        unit = word.group(1).lower()
        rest = rest[word.end() :].strip()
    return amount, unit, rest


def parse_ingredient(text: str) -> RawIngredient:
    """Składnik „ilość jednostka nazwa”; bez ilości → amount/unit None, name = cały tekst."""
    clean = " ".join(text.split())
    amount, unit, rest = split_amount_unit(clean)
    return RawIngredient(text=clean, amount=amount, unit=unit, name=rest or clean)


def parse_number(value: Any) -> float | None:
    """Pierwsza liczba z wartości: "504 kcal" → 504.0, "248.8 calories" → 248.8, "2 porcje" → 2.0."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUM_RE.search(str(value))
    return float(match.group(0).replace(",", ".")) if match else None


def _duration_minutes(value: Any) -> int | None:
    """ISO 8601 (PT1H30M, P1DT2H) → minuty."""
    if not isinstance(value, str):
        return None
    match = _DURATION_RE.match(value.strip())
    if match is None or not any(match.groups()):
        return None
    days, hours, minutes = (int(g) if g else 0 for g in match.groups())
    return days * 1440 + hours * 60 + minutes


def _total_minutes(node: dict[str, Any]) -> int | None:
    total = _duration_minutes(node.get("totalTime"))
    if total is not None:
        return total
    parts = (_duration_minutes(node.get("prepTime")), _duration_minutes(node.get("cookTime")))
    known = [m for m in parts if m is not None]
    return sum(known) if known else None


def _iter_nodes(value: Any) -> list[dict[str, Any]]:
    """Spłaszcza listy i @graph do słowników."""
    if isinstance(value, list):
        out: list[dict[str, Any]] = []
        for item in value:
            out.extend(_iter_nodes(item))
        return out
    if isinstance(value, dict):
        graph = value.get("@graph")
        return [value] if graph is None else _iter_nodes(graph) + [value]
    return []


def _json_ld_nodes(page: str) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for block in _BLOCK_RE.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes.extend(_iter_nodes(data))
    return nodes


def _is_recipe(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    return "Recipe" in (kind if isinstance(kind, list) else [kind])


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value if isinstance(value, list) else str(value).split(",")
    return tuple(s.strip() for s in items if isinstance(s, str) and s.strip())


def _image_url(value: Any, index: dict[str, dict[str, Any]], depth: int = 0) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        for item in value:
            url = _image_url(item, index, depth)
            if url:
                return url
        return None
    if isinstance(value, dict):
        for key in ("url", "contentUrl"):
            direct = value.get(key)
            if isinstance(direct, str) and direct.strip():
                return direct.strip()
        ref = value.get("@id")
        if depth == 0 and isinstance(ref, str) and ref in index:
            return _image_url(index[ref], index, depth + 1)
    return None


def _first_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, dict)), {})
    return value if isinstance(value, dict) else {}


def _recipe_from_node(
    source: str, url: str, node: dict[str, Any], index: dict[str, dict[str, Any]]
) -> Recipe | None:
    title = node.get("name")
    if not isinstance(title, str) or not title.strip():
        return None
    nutrition = _first_dict(node.get("nutrition"))
    yield_value = node.get("recipeYield")
    if isinstance(yield_value, list):
        yield_value = yield_value[0] if yield_value else None
    ingredient_values = node.get("recipeIngredient")
    if isinstance(ingredient_values, str):
        ingredient_values = [ingredient_values]
    ingredients = tuple(
        parse_ingredient(item) for item in (ingredient_values or []) if isinstance(item, str) and item.strip()
    )
    categories = _strings(node.get("recipeCategory"))
    return Recipe(
        source=source,
        source_id=url,
        title=title.strip(),
        url=url,
        image=_image_url(node.get("image"), index),
        servings=parse_number(yield_value),
        kcal=parse_number(nutrition.get("calories")),
        protein=parse_number(nutrition.get("proteinContent")),
        carbs=parse_number(nutrition.get("carbohydrateContent")),
        fat=parse_number(nutrition.get("fatContent")),
        minutes=_total_minutes(node),
        tags=_strings(node.get("keywords")),
        category_hint=categories[0] if categories else None,
        ingredients=ingredients,
    )


def parse_jsonld_recipe(source: str, url: str, page: str) -> Recipe | None:
    """Pierwszy poprawny (z nazwą) węzeł `Recipe` ze strony → Recipe; None gdy brak lub uszkodzony JSON-LD."""
    nodes = _json_ld_nodes(page)
    index = {n["@id"]: n for n in nodes if isinstance(n.get("@id"), str)}
    for node in nodes:
        if _is_recipe(node):
            recipe = _recipe_from_node(source, url, node, index)
            if recipe is not None:
                return recipe
    return None


def sitemap_locs(xml: str) -> list[str]:
    return [html.unescape(loc) for loc in _LOC_RE.findall(xml)]


class JsonLdSource:
    """Konektor oparty o JSON-LD Recipe; URL-e z sitemapy (opcjonalnie indeksu sitemap)."""

    def __init__(
        self,
        *,
        name: str,
        sitemap_url: str,
        url_marker: str,
        sub_sitemap_hint: str | None = None,
    ) -> None:
        self.name = name
        self.sitemap_url = sitemap_url
        self.url_marker = url_marker
        self.sub_sitemap_hint = sub_sitemap_hint

    def list_urls(self, *, fetch: Fetch) -> list[str]:
        locs = sitemap_locs(fetch(self.sitemap_url))
        candidates: list[str] = []
        for loc in locs:
            if loc.split("?")[0].endswith(".xml"):
                if self.sub_sitemap_hint and self.sub_sitemap_hint in loc:
                    candidates.extend(sitemap_locs(fetch(loc)))
            else:
                candidates.append(loc)
        return recipe_urls(candidates, self.url_marker)

    def parse(self, url: str, html: str) -> Recipe | None:
        return parse_jsonld_recipe(self.name, url, html)


def recipe_urls(candidates: list[str], marker: str) -> list[str]:
    """Unikalne URL-e zawierające `marker` z niepustym slugiem (bez samej strony listingu)."""
    seen: dict[str, None] = {}
    for url in candidates:
        if marker in url and url.split(marker, 1)[1].strip("/"):
            seen.setdefault(url, None)
    return list(seen)
