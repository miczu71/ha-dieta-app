"""Lidl Przepisy (przepisy.lidl.pl/przepis/) — JSON-LD Recipe, płaska sitemapa."""

from __future__ import annotations

from dieta.sources.jsonld import JsonLdSource


class LidlSource(JsonLdSource):
    def __init__(self) -> None:
        super().__init__(
            name="lidl",
            sitemap_url="https://przepisy.lidl.pl/sitemap.xml",
            url_marker="/przepis/",
        )
