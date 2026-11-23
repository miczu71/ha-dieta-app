"""Konektory źródeł przepisów: parsowanie fixtur (bez sieci) i listowanie URL-i przez fałszywy `fetch`."""

from __future__ import annotations

from pathlib import Path

import pytest

from dieta.sources.aniagotuje import AniagotujeSource
from dieta.sources.base import RawIngredient
from dieta.sources.jsonld import parse_ingredient, split_amount_unit
from dieta.sources.lidl import LidlSource
from dieta.sources.policzona import PolicznaSource
from dieta.sources.respo import RespoSource

FIXTURES = Path(__file__).parent / "fixtures" / "sources"


def _page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fake_fetch(pages: dict[str, str]):
    """Fałszywy `fetch`: zwraca zapisaną treść, a nieznany URL to błąd testu (brak sieci)."""
    requested: list[str] = []

    def fetch(url: str) -> str:
        requested.append(url)
        if url not in pages:
            raise AssertionError(f"nieoczekiwane pobranie: {url}")
        return pages[url]

    fetch.requested = requested  # type: ignore[attr-defined]
    return fetch


# --- JSON-LD: Centrum Respo ---------------------------------------------------------------------------------


def test_respo_kcal_i_makro_na_porcje() -> None:
    url = "https://centrumrespo.pl/przepisy/salatka-z-komosa-ryzowa-granatem-i-orzechami/"
    r = RespoSource().parse(url, _page("respo_salatka.html"))

    assert r is not None
    assert r.source == "respo"
    assert r.source_id == url
    assert r.title == "Sałatka z komosą ryżową, granatem i orzechami"
    assert (r.kcal, r.protein, r.carbs, r.fat) == (504.0, 22.0, 59.0, 22.0)
    assert r.servings == 2.0
    assert r.minutes == 30
    assert r.image is not None and r.image.startswith("https://media.centrumrespo.pl/")
    assert len(r.ingredients) == 13
    assert r.ingredients[0] == RawIngredient(
        text="100 g komosy ryżowej", amount=100.0, unit="g", name="komosy ryżowej"
    )


def test_respo_recipe_z_grafu_kategoria_i_tagi() -> None:
    url = "https://centrumrespo.pl/przepisy/makaron-z-boczkiem-i-wedzonym-twarogiem/"
    r = RespoSource().parse(url, _page("respo_makaron.html"))

    assert r is not None
    assert (r.kcal, r.protein, r.carbs, r.fat) == (479.0, 24.0, 37.0, 24.0)
    assert r.servings == 2.0
    assert r.minutes == 25
    assert r.category_hint == "Dania główne"
    assert "Bez cukru" in r.tags
    assert len(r.ingredients) == 7


def test_respo_strona_bez_przepisu_daje_none() -> None:
    assert RespoSource().parse("https://centrumrespo.pl/kontakt/", _page("respo_bez_przepisu.html")) is None


def test_uszkodzony_json_ld_daje_none_bez_wyjatku() -> None:
    url = "https://centrumrespo.pl/przepisy/uszkodzony/"
    assert RespoSource().parse(url, _page("respo_uszkodzony.html")) is None


# --- JSON-LD: Lidl Przepisy ---------------------------------------------------------------------------------


def test_lidl_kcal_dziesietne_i_czas_iso() -> None:
    url = "https://przepisy.lidl.pl/przepis/zielona-pasta-jajeczna"
    r = LidlSource().parse(url, _page("lidl_pasta.html"))

    assert r is not None
    assert r.source == "lidl"
    assert r.kcal == 248.8
    assert (r.protein, r.carbs, r.fat) == (16.7, 2.0, 18.7)
    assert r.servings == 2.0
    assert r.minutes == 20
    assert r.category_hint == "Śniadania"
    assert r.image is not None and r.image.startswith("https://cdn.recipes.lidl/")


def test_lidl_ilosc_jednostka_i_ulamek_w_skladnikach() -> None:
    url = "https://przepisy.lidl.pl/przepis/kajzerki-z-warzywami"
    r = LidlSource().parse(url, _page("lidl_kajzerki.html"))

    assert r is not None
    assert r.servings == 4.0
    assert r.minutes == 60
    assert r.kcal == 183.1
    by_name = {i.name: i for i in r.ingredients}
    assert by_name["pieprz czarny"] == RawIngredient(
        text="1 szczypta pieprz czarny", amount=1.0, unit="szczypta", name="pieprz czarny"
    )
    assert by_name["papryka czerwona"].amount == 0.5
    assert by_name["papryka czerwona"].unit == "szt"


def test_skladnik_z_ulamkiem_i_bez_ilosci() -> None:
    assert split_amount_unit("1/2 łyżeczki soli") == (0.5, "łyżeczki", "soli")
    cebula = RawIngredient(text="½ cebuli", amount=0.5, unit=None, name="cebuli")
    assert parse_ingredient("½ cebuli") == cebula
    assert parse_ingredient("1 1/2 szklanki mąki") == RawIngredient(
        text="1 1/2 szklanki mąki", amount=1.5, unit="szklanki", name="mąki"
    )
    assert parse_ingredient("0,25 kg ziemniaków") == RawIngredient(
        text="0,25 kg ziemniaków", amount=0.25, unit="kg", name="ziemniaków"
    )
    assert parse_ingredient("mała cebula") == RawIngredient(
        text="mała cebula", amount=None, unit=None, name="mała cebula"
    )


# --- Policzona Szama (HTML) ---------------------------------------------------------------------------------


def test_policzona_kcal_i_makro_na_porcje_z_tekstu() -> None:
    url = "https://policzona-szama.pl/blogs/przepisy/domowy-kebab"
    r = PolicznaSource().parse(url, _page("policzona_kebab.html"))

    assert r is not None
    assert r.source == "policzona"
    assert r.title.startswith("Jak zrobić KEBABA")
    assert r.kcal == 684.0
    assert (r.protein, r.carbs, r.fat) == (48.0, 76.0, 20.0)
    assert r.image is not None
    first = r.ingredients[0]
    assert first.name == "mąka pszenna typ 650"
    assert (first.amount, first.unit) == (400.0, "g")


def test_policzona_porcje_i_skladniki_bez_naglowka() -> None:
    url = "https://policzona-szama.pl/blogs/przepisy/szarpana-wieprzowina-przepis-na-pyszny-obiad"
    r = PolicznaSource().parse(url, _page("policzona_pulled.html"))

    assert r is not None
    assert r.servings == 3.0
    # Fraza partii „z 600 g … będzie mieć 1135 kcal” przeliczona na porcję (÷ 3).
    assert r.kcal == pytest.approx(1135 / 3)
    assert r.protein == pytest.approx(126 / 3)
    assert r.carbs == pytest.approx(37 / 3)
    assert r.fat == pytest.approx(53 / 3)
    lopatka = RawIngredient(
        text="łopatka wieprzowa – 600 g", amount=600.0, unit="g", name="łopatka wieprzowa"
    )
    assert lopatka in r.ingredients
    assert RawIngredient(text="mała cebula", amount=None, unit=None, name="mała cebula") in r.ingredients
    assert not any(i.text.upper().startswith("SKŁADNIKI") for i in r.ingredients)


def test_policzona_partia_bez_porcji_daje_none() -> None:
    url = "https://policzona-szama.pl/blogs/przepisy/zupa-testowa"
    r = PolicznaSource().parse(url, _page("policzona_partia_bez_porcji.html"))

    assert r is not None
    assert r.servings is None
    assert r.kcal is None
    assert r.protein is None and r.carbs is None and r.fat is None


def test_policzona_bez_kcal_daje_none() -> None:
    url = "https://policzona-szama.pl/blogs/przepisy/owsianka-testowa"
    r = PolicznaSource().parse(url, _page("policzona_bez_kcal.html"))

    assert r is not None
    assert r.kcal is None
    assert r.protein is None and r.carbs is None and r.fat is None
    assert r.servings == 2.0
    assert [(i.name, i.amount, i.unit) for i in r.ingredients] == [
        ("płatki owsiane", 80.0, "g"),
        ("mleko", 250.0, "ml"),
        ("banan", 1.0, "szt"),
    ]
    assert r.image == "https://policzona-szama.pl/cdn/shop/articles/owsianka.jpg"


def test_policzona_strona_bez_przepisu_daje_none() -> None:
    url = "https://policzona-szama.pl/blogs/przepisy/jak-planowac-zakupy"
    assert PolicznaSource().parse(url, _page("policzona_bez_przepisu.html")) is None


# --- Listowanie URL-i (fetch jako seam; brak sieci) ---------------------------------------------------------


def test_respo_list_urls_przez_indeks_sitemap() -> None:
    index = "https://centrumrespo.pl/sitemap_index.xml"
    recipes = "https://centrumrespo.pl/przepisy-sitemap.xml"
    pages = {
        index: (
            "<sitemapindex>"
            "<sitemap><loc>https://centrumrespo.pl/post-sitemap.xml</loc></sitemap>"
            "<sitemap><loc>https://centrumrespo.pl/przepisy-sitemap.xml</loc></sitemap>"
            "<sitemap><loc>https://centrumrespo.pl/category-sitemap.xml</loc></sitemap>"
            "</sitemapindex>"
        ),
        recipes: (
            "<urlset>"
            "<url><loc>https://centrumrespo.pl/przepisy/</loc></url>"
            "<url><loc>https://centrumrespo.pl/przepisy/salatka-z-komosa/</loc></url>"
            "<url><loc>https://centrumrespo.pl/przepisy/makaron/</loc></url>"
            "<url><loc>https://centrumrespo.pl/przepisy/makaron/</loc></url>"
            "</urlset>"
        ),
    }
    fetch = _fake_fetch(pages)

    urls = RespoSource().list_urls(fetch=fetch)

    assert urls == [
        "https://centrumrespo.pl/przepisy/salatka-z-komosa/",
        "https://centrumrespo.pl/przepisy/makaron/",
    ]
    assert fetch.requested == [index, recipes]  # nie pobrano post/category-sitemap


def test_lidl_list_urls_filtruje_przepisy_i_odcina_duplikaty() -> None:
    sitemap = "https://przepisy.lidl.pl/sitemap.xml"
    pages = {
        sitemap: (
            "<urlset>"
            "<url><loc>https://przepisy.lidl.pl</loc></url>"
            "<url><loc>https://przepisy.lidl.pl/wszystkie-przepisy</loc></url>"
            "<url><loc>https://przepisy.lidl.pl/przepis/serniczki-w-jablkach</loc></url>"
            "<url><loc>https://przepisy.lidl.pl/przepis/serniczki-w-jablkach</loc></url>"
            "<url><loc>https://przepisy.lidl.pl/przepis/a-b?x=1&amp;y=2</loc></url>"
            "</urlset>"
        ),
    }
    fetch = _fake_fetch(pages)

    assert LidlSource().list_urls(fetch=fetch) == [
        "https://przepisy.lidl.pl/przepis/serniczki-w-jablkach",
        "https://przepisy.lidl.pl/przepis/a-b?x=1&y=2",
    ]
    assert fetch.requested == [sitemap]


def test_policzona_list_urls_z_sitemapy_artykulow() -> None:
    sitemap = "https://policzona-szama.pl/sitemap_articles_1.xml"
    pages = {
        sitemap: (
            "<urlset>"
            "<url><loc>https://policzona-szama.pl/blogs/przepisy/domowy-kebab</loc></url>"
            "<url><loc>https://policzona-szama.pl/blogs/przepisy/szarpana-wieprzowina</loc></url>"
            "<url><loc>https://policzona-szama.pl/blogs/news/zakupy</loc></url>"
            "</urlset>"
        ),
    }
    fetch = _fake_fetch(pages)

    assert PolicznaSource().list_urls(fetch=fetch) == [
        "https://policzona-szama.pl/blogs/przepisy/domowy-kebab",
        "https://policzona-szama.pl/blogs/przepisy/szarpana-wieprzowina",
    ]
    # Kanał Atom jest próbowany najpierw (tu fałszywy fetch go nie ma → błąd tolerowany), sitemapa zawsze.
    assert fetch.requested[-1] == sitemap
    assert fetch.requested.count(sitemap) == 1


# --- Microdata: Ania Gotuje ---------------------------------------------------------------------------


def test_aniagotuje_microdata_przepis() -> None:
    url = "https://aniagotuje.pl/przepis/kotlety-z-ciecierzycy"
    r = AniagotujeSource().parse(url, _page("aniagotuje_kotlety.html"))

    assert r is not None
    assert r.source == "aniagotuje"
    assert r.source_id == url
    assert r.title == "Kotlety z ciecierzycy"
    assert r.servings == 12.0
    assert r.minutes == 750
    assert r.category_hint == "dania bez mięsa"
    assert "obiad" in r.tags
    assert r.image is not None and r.image.startswith("https://cdn.aniagotuje.com/")
    # wartości na stronie są „W 100 g”, więc nie są podawane jako na porcję
    assert (r.kcal, r.protein, r.carbs, r.fat) == (None, None, None, None)
    assert len(r.ingredients) >= 3
    assert r.ingredients[0] == RawIngredient(
        text="200 g suchej ciecierzycy - niecała szklanka",
        amount=200.0,
        unit="g",
        name="suchej ciecierzycy - niecała szklanka",
    )


def test_aniagotuje_kcal_100g_z_microdata_calories() -> None:
    url = "https://aniagotuje.pl/przepis/kotlety-z-ciecierzycy"
    r = AniagotujeSource().parse(url, _page("aniagotuje_kotlety.html"))

    assert r is not None
    assert r.kcal_100g is not None and 50 <= r.kcal_100g <= 600
    assert r.kcal_100g == 168.0
    assert r.kcal is None


def test_aniagotuje_bez_bloku_wartosci_daje_kcal_100g_none() -> None:
    page = (
        '<html><body><article itemscope itemtype="https://schema.org/Recipe">'
        '<h1 itemprop="name">Test</h1>'
        '<span itemprop="recipeIngredient">200 g mąki</span>'
        "</article></body></html>"
    )
    r = AniagotujeSource().parse("https://aniagotuje.pl/przepis/test", page)

    assert r is not None
    assert r.kcal_100g is None


def test_inne_konektory_nie_ustawiaja_kcal_100g() -> None:
    r = RespoSource().parse("https://centrumrespo.pl/przepis/x", _page("respo_salatka.html"))
    assert r is not None
    assert r.kcal_100g is None


def test_aniagotuje_strona_bez_recipe_to_none() -> None:
    page = '<html><body><h1 itemprop="name">Blog</h1><p>tekst bez przepisu</p></body></html>'
    assert AniagotujeSource().parse("https://aniagotuje.pl/przepis/x", page) is None


def test_aniagotuje_list_urls_filtruje_przepisy() -> None:
    sitemap = "https://aniagotuje.pl/sitemap.xml"
    fetch = _fake_fetch({sitemap: _page("aniagotuje_sitemap.xml")})

    assert AniagotujeSource().list_urls(fetch=fetch) == [
        "https://aniagotuje.pl/przepis/torcik-sernik-mango-z-andrutami-i-beza",
        "https://aniagotuje.pl/przepis/kotlety-z-ciecierzycy",
        "https://aniagotuje.pl/przepis/omlet-cesarski-z-truskawkami",
    ]
