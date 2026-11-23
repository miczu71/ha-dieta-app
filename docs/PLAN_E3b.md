# Dieta E3b — próg 50% + nowe źródła (przed planerem E4)

## Context
Bramka „znajomość” nie przeszła na żywym katalogu (301 przepisów: ≥70% → 1,3%, ≥60% → 4,7%, ≥50% → 14,6%).
Decyzja usera 2026-11-23: **domyślny próg 50% (suwak zostaje) + szersze źródła**, dopiero potem E4.
Policzona Szama (fit, kcal) jest zablokowana przez HTTP 429 przy pobieraniu pojedynczych artykułów.
**Sprawdzone dziś na żywo:** `https://policzona-szama.pl/blogs/przepisy.atom?page=N` → 200, 30 wpisów na stronę,
pełny HTML artykułu w `<content type="html">`. Obecny parser `PolicznaSource.parse` na stronie złożonej z wpisu
Atom: 2 strony = 60 wpisów → 58 przepisów, 56 z kcal. Aniagotuje: JSON-LD Recipe (E0: 12/12, najwyższa średnia
znajomość 58%), `robots.txt` Allow /, `sitemap.xml` płaska, 2302 URL-e `/przepis/`.

Repo: `/config/addons/ha-dieta-app` (PUBLIC), gałąź `main`, kod `dieta/app/src/dieta/`.

## KOREKTA 2026-11-23 (po raportach workerów — moje błędy w założeniach planu)
- Policzona: `?page=N` kanału Atom NIE paginuje (każda strona = te same 30 najnowszych wpisów; wcześniejsze „60 wpisów” to 30 policzone dwa razy). Kontrakt W2 → wariant (c): kanał (dedupe, kończy na stronie bez nowych URL-i) + sitemapa dla zaległości; reszta przez dotychczasowe `fetch` (429 → cooldown jak dotąd). Spodziewane: ~29 nowych przepisów bez 429, zaległości stopniowo.
- Aniagotuje: strona NIE ma JSON-LD Recipe (tylko WebSite/WebPage), przepis jest w microdata (`itemtype=Recipe`, `itemprop=recipeIngredient`). Kontrakt W1 → własny parser microdata w `sources/aniagotuje.py` zamiast `JsonLdSource`.

## Kroki
1. Skopiować plan do `docs/PLAN_E3b.md` (z sekcją Wykonawca poniżej), wpis w `docs/ROADMAP.md`, commit.
2. `wt new` ×2, BASELINE `git status --short`; **W1 ‖ W2** (`haiku-worker`), potem `claimcheck` → `verifier` per paczka.
3. `wt land` obu, pełne `uv run pytest`, ruff, mypy w repo.
4. Wydanie **0.1.4** od razu (etap potrzebny przed E4): `simplify` → `code-review` ‖ `silent-failure-hunter` ‖
   `pr-test-analyzer` ‖ security review (`git diff v0.1.3..HEAD`) → `release` (bump `dieta/config.yaml`, CHANGELOG, tag non-draft).
5. Na żywo: opcje zainstalowanego add-onu mają zapisane `familiar_threshold: 70` i 3 źródła, nowa wartość domyślna ich
   nie zmieni. Najpierw sprawdzam, czy `ha_manage_app` scala opcje częściowo (bez ruszania tokenów). Jeśli tak, ustawiam
   `familiar_threshold: 50` i dopisuję `aniagotuje` do `sources`; jeśli nie, robi to user w UI (2 pola). Potem
   „Odśwież teraz” dla `policzona` i `aniagotuje` (POST `/katalog/odswiez?zrodlo=…`).
6. Pomiar `pomiar` (ten sam kontrakt co w ROADMAP 0.1.3): liczba przepisów per źródło, % z kcal, odsetek ≥70/60/50%
   ogółem i per źródło, po zakończeniu mapowania LLM. Wynik do ROADMAP.
7. Koniec: ROADMAP „E3b ZROBIONE”, `stage-end` (następny: E4, wywiad `grill-me`).

## Wykonawca

### W1 — konektor Aniagotuje + domyślny próg 50% (`haiku-worker`)
- Pliki: nowy `dieta/app/src/dieta/sources/aniagotuje.py` (wzorzec `sources/lidl.py`: `JsonLdSource(name="aniagotuje",
  sitemap_url="https://aniagotuje.pl/sitemap.xml", url_marker="/przepis/")`); `settings.py` (`KNOWN_SOURCES` + "aniagotuje",
  `familiar_threshold` domyślnie 50 w polu i w `_bounded`); `pipeline.py` (mapa źródeł); `dieta/config.yaml` (options:
  `familiar_threshold: 50`, `aniagotuje` w `sources`; schema `list(respo|lidl|policzona|aniagotuje)`);
  `dieta/translations/*.yaml` i `DOCS.md`, jeśli wymieniają źródła lub próg; testy w `tests/test_sources.py`
  i `tests/test_settings.py` + fixtura `tests/fixtures/sources/aniagotuje_*.html` (zapisana z jednej strony przepisu,
  bez danych osobowych).
- Test: `cd dieta/app && uv run pytest -q && uv run ruff check . && uv run mypy src`.
- DONE: parser zwraca `Recipe` z tytułem, porcjami i ≥3 składnikami na fixturze; `list_urls` filtruje `/przepis/`
  na fixturze sitemapy; `Settings` bez opcji → próg 50 i 4 źródła; test configu (`test_config.py`) zielony.
- NIE RÓB: `sources/policzona.py`, `catalog.py`, `fetcher.py`, zmian wersji/CHANGELOG, commitów.

### W2 — Policzona przez kanał Atom (`haiku-worker`)
- Pliki: `sources/policzona.py`: `list_urls` pobiera `FEED_URL = "https://policzona-szama.pl/blogs/przepisy.atom"`
  z `?page=N` od 1 aż do strony bez `<entry>` (limit `MAX_FEED_PAGES = 40`), parsuje XML (`xml.etree`, przestrzeń Atom),
  dla każdego wpisu z linkiem zawierającym `URL_MARKER` zapamiętuje w `self._pages[url]` syntetyczną stronę
  `<html><head><title>T</title></head><body><h1>T</h1><div class="article">CONTENT</div></body></html>` (T escapowane
  `html.escape`; obraz = pierwszy `<img src>` z treści jako `<meta property="og:image">`, gdy jest) i zwraca listę URL-i;
  nowa metoda `prefetched(url) -> str | None`. `catalog.py` w `crawl`: przed `fetch(url)` użyć
  `getattr(source, "prefetched", None)`; strona z prefetch **nie** zwiększa `used` (nie zjada dziennego limitu)
  i nie wysyła zapytania. Testy w nowym `tests/test_policzona_atom.py` (fixtura `tests/fixtures/sources/policzona_feed.atom`:
  2–3 wpisy przycięte z prawdziwego kanału, 2. strona pusta).
- Test: jak W1.
- DONE: `list_urls` z fałszywym `fetch` zwraca URL-e z obu stron i kończy na pustej; `crawl` z tym źródłem dodaje
  przepisy bez wywołania `fetch` dla artykułów (fałszywy fetch liczy wywołania: tylko strony kanału), `crawl_used`
  bez zmian; przepis z fixtury ma kcal; istniejące testy Policzonej (strony HTML) zielone.
- NIE RÓB: `settings.py`, `pipeline.py`, `config.yaml`, `tests/test_sources.py`, `fetcher.py`, zmian wersji, commitów.

## Rollback
Kod: `git revert` commitów E3b; wydanie: Supervisor → reinstall 0.1.3 (opcje zachowane); opcje: przywrócić
`familiar_threshold: 70` i 3 źródła. Przepisy Aniagotuje w katalogu: zostają (nieszkodliwe), albo wyłączenie źródła w opcjach.

## Weryfikacja / sukces
- Testy, ruff, mypy czyste; CI zielone (test + docker amd64/arm64).
- Na żywo: log „koniec crawla policzona” z +≥40 przepisami i 0×429; aniagotuje +≤150 w pierwszym przebiegu (limit dzienny).
- Katalog pokazuje domyślnie próg 50%; pomiar znajomości per źródło zapisany w ROADMAP. Gdy ≥50% nadal < 10% → stop i pytanie.
