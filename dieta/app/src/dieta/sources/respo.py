"""Centrum Respo (centrumrespo.pl/przepisy/) — JSON-LD Recipe, URL-e z indeksu sitemap."""

from __future__ import annotations

from dieta.sources.jsonld import JsonLdSource


class RespoSource(JsonLdSource):
    def __init__(self) -> None:
        super().__init__(
            name="respo",
            sitemap_url="https://centrumrespo.pl/sitemap_index.xml",
            url_marker="/przepisy/",
            sub_sitemap_hint="przepisy-sitemap",
        )
