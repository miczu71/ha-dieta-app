"""Policzona przez kanał Atom: URL-e z `?page=N` (stop na powtórce), sitemapa dopisana, crawl bez zapytań."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from dieta.catalog import crawl
from dieta.sources.policzona import FEED_URL, SITEMAP_URL, PolicznaSource
from dieta.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "sources" / "policzona_feed.atom"
SHOARMA = "https://policzona-szama.pl/blogs/przepisy/shoarma-idealna-kolacja-dla-dwojga"
PAD_THAI = "https://policzona-szama.pl/blogs/przepisy/pad-thai-kuchnia-azjatycka-na-wyciagniecie-reki"
KEBAB = "https://policzona-szama.pl/blogs/przepisy/domowy-kebab"
EMPTY_FEED = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<feed xmlns="http://www.w3.org/2005/Atom"><title>Policzona Szama</title></feed>'
)
SITEMAP = (
    "<urlset>"
    f"<url><loc>{SHOARMA}</loc></url>"
    f"<url><loc>{KEBAB}</loc></url>"
    "<url><loc>https://policzona-szama.pl/blogs/news/zakupy</loc></url>"
    "</urlset>"
)


class FakeFetch:
    """Fałszywy `fetch`: zapisuje wywołania; nieznany URL to błąd (brak sieci)."""

    def __init__(self, pages: dict[str, str | Exception]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, url: str) -> str:
        self.requested.append(url)
        page = self.pages.get(url)
        if page is None:
            raise AssertionError(f"nieoczekiwane pobranie: {url}")
        if isinstance(page, Exception):
            raise page
        return page

    def feed_calls(self) -> list[str]:
        return [u for u in self.requested if u.startswith(FEED_URL)]


def _feed_page(number: int) -> str:
    return f"{FEED_URL}?page={number}"


def _fixture() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_list_urls_kanal_przed_sitemapa_bez_duplikatow_i_stop_na_powtorce() -> None:
    fetch = FakeFetch({_feed_page(1): _fixture(), _feed_page(2): _fixture(), SITEMAP_URL: SITEMAP})

    urls = PolicznaSource().list_urls(fetch=fetch)

    assert urls == [SHOARMA, PAD_THAI, KEBAB]
    assert fetch.feed_calls() == [_feed_page(1), _feed_page(2)]
    assert fetch.requested.count(SITEMAP_URL) == 1


def test_list_urls_stop_na_pustej_stronie() -> None:
    fetch = FakeFetch({_feed_page(1): _fixture(), _feed_page(2): EMPTY_FEED, SITEMAP_URL: SITEMAP})

    urls = PolicznaSource().list_urls(fetch=fetch)

    assert urls[:2] == [SHOARMA, PAD_THAI]
    assert fetch.feed_calls() == [_feed_page(1), _feed_page(2)]


def test_list_urls_blad_kanalu_nie_gubi_sitemapy() -> None:
    fetch = FakeFetch({_feed_page(1): RuntimeError("awaria"), SITEMAP_URL: SITEMAP})

    assert PolicznaSource().list_urls(fetch=fetch) == [SHOARMA, KEBAB]


def test_list_urls_blad_sitemapy_nie_gubi_kanalu() -> None:
    fetch = FakeFetch(
        {_feed_page(1): _fixture(), _feed_page(2): EMPTY_FEED, SITEMAP_URL: RuntimeError("404")}
    )

    assert PolicznaSource().list_urls(fetch=fetch) == [SHOARMA, PAD_THAI]


def test_prefetched_zwraca_strone_z_wpisu_z_przepisem_i_kcal() -> None:
    source = PolicznaSource()
    source.list_urls(
        fetch=FakeFetch({_feed_page(1): _fixture(), _feed_page(2): EMPTY_FEED, SITEMAP_URL: SITEMAP})
    )

    page = source.prefetched(PAD_THAI)
    assert page is not None
    recipe = source.parse(PAD_THAI, page)

    assert recipe is not None
    assert recipe.title.startswith("PAD THAI")
    assert recipe.kcal is not None
    assert source.prefetched(KEBAB) is None


def test_crawl_przepisy_z_kanalu_bez_zapytan_o_artykuly(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    source = PolicznaSource()
    kebab_html = (FIXTURE.parent / "policzona_kebab.html").read_text(encoding="utf-8")
    fetch = FakeFetch(
        {_feed_page(1): _fixture(), _feed_page(2): EMPTY_FEED, SITEMAP_URL: SITEMAP, KEBAB: kebab_html}
    )

    result = crawl(store, source, fetch=fetch, today=date(2026, 11, 23), limit=10)

    assert result.added == 3  # dwa z kanału + KEBAB z sitemapy (pobrany zwykłym fetch)
    assert fetch.requested == [_feed_page(1), _feed_page(2), SITEMAP_URL, KEBAB]
    assert store.get_meta("crawl_used:policzona") == "2026-11-23:1"
