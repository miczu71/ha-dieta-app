# Dieta E3c — kcal i makro na porcję ze składników (przed planerem E4)

## Context
Po 0.1.4 katalog ma 791 przepisów, z czego 229 jest bez kcal. Ania Gotuje ma 148/148 bez kcal, bo strona podaje wartości tylko „w 100 g”.
Planer (E4) bierze wyłącznie przepisy z kcal, więc przy progu 50% użyteczne jest tylko 9,2% katalogu.
Wywiad: sesja `a031f378` (2026-11-23) i dzisiejsze rundy. „+” w „kcal ze składników+” wcisnęło się przypadkiem.
Repo `/config/addons/ha-dieta-app` (PUBLIC), `main`, kod `dieta/app/src/dieta/`.

**Ustalenia (dla kogo i po czym poznamy sukces):**
1. **Kto korzysta:** Katalog (teraz) i planer E4. Każdy przepis bez kcal ze źródła, z dowolnego źródła, dostaje szacunek kcal i makro na porcję. Przepisy z kcal ze źródła zostają nietknięte.
2. **Automatycznie:** wartości na 100 g idą kaskadą: wbudowana tabela → Open Food Facts po nazwie → LLM `auto:text`. Wyniki są zapamiętane per składnik słownika. Miary (łyżka, szklanka, szt.) przeliczamy tabelą miar, a wagę sztuki podaje LLM raz na składnik. Przyprawy i „do smaku” = 0 kcal. Przepis bez liczby porcji: porcje = masa / 400 g, z plakietką „porcje szacowane”.
3. **Za zgodą:** do kolejki trafia tylko niska pewność. Są dwa przypadki:
   - składnik, przy którym LLM zgłosił niską pewność, albo z wartością odstającą (>900 kcal/100 g, makro niezgodne z kcal o >20%);
   - przepis z policzoną masą poniżej 85% albo z rozbieżnością z kontrolą „w 100 g” ze strony (niżej).

   Taki przepis dostaje plakietkę „niepełny szacunek” i jest poza planerem do decyzji usera.
4. **UI:** „~420 kcal (szacunek)”, pokrycie „9/11 policzonych”, lista nieznanych składników, filtr „szacunki” w Katalogu.
5. **Sukces (skill `pomiar`):** liczę metodę na przepisach z kcal ze źródła (~560). Warunki GO:
   - mediana |błędu| ≤ 15%;
   - ≥ 80% przepisów mieści się w ±30%;
   - ≥ 80% przepisów bez kcal dostaje szacunek, który wchodzi do planera.

   Potem powtarzam pomiar znajomości.

**Rozstrzygnięcie uwagi o brakujących ilościach** (w E0 tylko ~45% linii Ani Gotuje miało ilość):
- Linia bez ilości, która nie jest przyprawą ani wodą, ma nieznaną masę. Taki przepis nie może dostać szacunku „pełnego”.
- Dlatego LLM dostaje taką linię razem z wagą sztuki i zwraca typową ilość w gramach dla tego przepisu, z flagą `assumed`.
- Linie `assumed` liczą się do pokrycia z wagą 0,5, więc przepis z dużą masą zgadywaną spada poniżej 85% i trafia do kolejki zamiast cicho zaniżać kcal.
- **Kontrola Ani Gotuje:** konektor zaczyna zapisywać `kcal_100g` ze strony. Jeśli policzone kcal/100 g różni się od strony o >25%, przepis idzie do kolejki z oboma wartościami.
- Pomiar raportuje osobno odsetek przepisów z liniami `assumed`. Jeśli kryterium ≥ 80% nie przejdzie z ich powodu, staję i pytam (to podważa założenie z wywiadu).

## Kroki
1. Kopia planu → `docs/PLAN_E3c.md`, wpis „E3c PLAN GOTOWY” w `docs/ROADMAP.md`, commit.
2. **Ja (Opus), przed workerami:** tabela `dieta/seed/nutrition_basic.json`.
   - Zakres: ~150 podstawowych produktów, w tym wszystkie 133 nazwy ogólne słownika i najczęstsze składniki przepisów z żywego katalogu (odczyt `GET /katalog`).
   - Pola: kcal, białko, węglowodany, tłuszcz /100 g; `piece_g` dla produktów na sztuki; `density` dla płynów.
   - Wartości typowe z polskich tabel składu (IŻŻ) / USDA, zaokrąglone. Plik ma nagłówek ze źródłem.
   - Tabela miar `dieta/seed/measures.json`: łyżka 15 ml, łyżeczka 5 ml, szklanka 250 ml, szczypta 0,5 g, opakowanie/puszka per składnik, jeśli znane.
3. `wt new` ×2, BASELINE; **W1 ‖ W2a**, potem **W2b**, potem **W3**. Po każdej paczce `claimcheck` → `verifier`.
4. `wt land`, pełne `uv run pytest`, ruff, mypy.
5. Pomiar walidacji na kopii bazy:
   - kopia przez SSH tylko do odczytu z hosta HA (`/data` add-onu `b969c720_dieta` → `~/dev/dieta-spike/data/e3c.db`);
   - komenda `python -m dieta estimate --validate`.

   Wynik idzie do `docs/POMIAR_kcal.md` i do ROADMAP. NO-GO → stop i pytanie z liczbami.
6. Wydanie **0.1.5** od razu: E4 go potrzebuje, a migracja v4 to ryzyko. Łańcuch:
   - `simplify`;
   - `code-review` ‖ `silent-failure-hunter` ‖ `pr-test-analyzer` ‖ security review (`git diff v0.1.4..HEAD`: SSRF/zapytania do OFF, injection w promptach);
   - `release`: bump `dieta/config.yaml`, CHANGELOG, NOTICE (atrybucja OFF, ODbL), tag non-draft.
7. Na żywo: sprawdzam, że po aktualizacji (restart robi Supervisor przy update) migracja v4 przeszła. Potem „Odśwież teraz”, a po zakończeniu przebiegu: liczba szacunków, kolejka, przykładowe karty Playwright 390/1280 px. Potem powtórny pomiar znajomości (≥70/60/50% ogółem i „z kcal”).
8. ROADMAP „E3c ZROBIONE”, `stage-end` (następny: E4 planer, wywiad `grill-me`).

## Wykonawca

### W1 — czysty kalkulator (`haiku-worker`)
- Pliki: nowy `src/dieta/estimate.py`, `tests/test_estimate.py`. Czyta seedy z kroku 2 i nie dotyka store, sieci ani LLM.
- API:
  - `to_grams(amount, unit, name, *, measures, piece_g, density) -> tuple[float|None, Literal["exact","measure","piece","none"]]`;
  - `is_free(name) -> bool` (sól, pieprz, przyprawy suszone, woda, „do smaku”);
  - `estimate_recipe(lines: list[Line], per100: Mapping[int, Per100], servings: float|None) -> Estimate`.
- `Estimate`: kcal, protein, carbs, fat na porcję; `coverage` (udział masy z wartościami, linie `assumed` ×0,5); `known`/`total` linie; `unknown_names`; `servings_estimated` (porcje = masa/400 g, gdy `servings` brak lub ≤0); `total_g`.
- Testy: przeliczenia miar, sztuki, płyny z gęstością, przyprawy = 0, brak porcji → 400 g, pokrycie z `assumed`, zaokrąglenia, puste wejście.
- Test: `uv run pytest tests/test_estimate.py && uv run ruff check src tests && uv run mypy src`.
- DONE: wszystkie powyższe funkcje z testami, zielone.
- NIE RÓB: store.py, pipeline.py, web/, sieć, LLM, zmiany w seedach.

### W2a — konektor Ani Gotuje: `kcal_100g` (`haiku-worker`)
- Pliki: `sources/base.py` (pole `kcal_100g: float | None = None` w `Recipe`, z domyślną wartością, żeby pozostałe konektory się nie zmieniły), `sources/aniagotuje.py` (odczyt bloku „W 100 g” → `kcal_100g`; kcal na porcję zostaje None), testy w `tests/test_sources.py` na istniejącej fixturze.
- Test: `uv run pytest tests/test_sources.py`, ruff, mypy. DONE: fixtura Ani daje `kcal_100g` > 0, inne źródła bez zmian.
- NIE RÓB: store.py, inne konektory.

### W2b — migracja v4, OFF, LLM, krok przebiegu (`sonnet-worker`, **ryzyko: migracja**) — po W1 i W2a
- `store.py`: nowa migracja na końcu `_MIGRATIONS` (istniejących wpisów nie ruszamy):
  - tabela `ingredient_nutrition(ingredient_id PK, kcal, protein, carbs, fat, piece_g, source CHECK IN ('table','off','llm','manual'), confidence CHECK IN ('high','low'), status CHECK IN ('auto','pending','approved'), updated_at)`;
  - kolumny `recipe`: `kcal_source TEXT CHECK IN ('source','estimate')`, `kcal_100g REAL`, `est_coverage REAL`, `est_status TEXT CHECK IN ('auto','pending','approved')`, `servings_estimated INTEGER`, `est_unknown_json TEXT`;
  - kolumna `recipe_ingredient.grams_assumed REAL`;
  - backfill: `kcal_source='source'` tam, gdzie `has_nutrition=1`.

  Do tego metody zapisu i odczytu oraz test migracji z bazy v3 (fixtura z wierszami).
- `nutrition_lookup.py` (nowy), kaskada per `ingredient_id` bez wpisu w `ingredient_nutrition`:
  1. tabela (seed, po nazwie znormalizowanej `normalize_name`);
  2. OFF: `https://world.openfoodfacts.org/api/v2/search` (`fields=product_name,nutriments`, `countries_tags=poland`), przez istniejący `fetcher` (allowlista hosta), tempo ≥ 6 s/zapytanie, `User-Agent: ha-dieta-app/<wersja>` bez danych osobowych. Trafienie przyjmuję tylko z kompletnymi `energy-kcal_100g` i makro, przy medianie z top 5 wyników w pasie ±25%; inaczej przechodzę do kroku 3;
  3. LLM przez `make_llm_ask`: paczki po 20, JSON {kcal, protein, carbs, fat, piece_g, confidence}. Ten sam prompt zwraca gramy dla linii bez ilości (`grams_assumed`).
- Walidacja wartości: kcal ≤ 900, |4p + 4c + 9f − kcal| ≤ 20% → inaczej `confidence=low`, `status=pending`.
- `pipeline.py`: krok `estimate_nutrition(store, ask, fetch)` po `map_recipe_ingredients`, tylko dla przepisów bez kcal ze źródła. Wynik idzie przez `estimate_recipe`.
  - Ustawia `est_status`: `auto` przy pokryciu ≥ 0,85, bez składnika `pending` i przy zgodności z `kcal_100g` (±25%, jeśli jest); inaczej `pending`.
  - Przepis z `est_status='auto'` lub `approved` traktowany jak „z kcal” w `has_nutrition`/filtrach.
  - Awaria LLM lub OFF → składnik zostaje bez wartości (ponowna próba w kolejnym przebiegu), log WARNING z przyczyną, bez cichego 0.
- CLI `python -m dieta estimate --validate <db>`: liczy metodą przepisy z `kcal_source='source'` i wypisuje medianę |błędu|, odsetek w ±30%, rozkład per źródło. Bez zapisu.
- Testy: migracja, kaskada (fałszywe fetch/ask), walidacja wartości, statusy, awaria LLM/OFF, CLI na małej bazie.
- Test: pełne `uv run pytest`, ruff, mypy. DONE: zielone + migracja z fixtury v3.
- NIE RÓB: zmian w istniejących migracjach, web/, sieci w testach, zapisu kcal do przepisów z `kcal_source='source'`.

### W3 — UI Katalogu i kolejka (`haiku-worker`) — po W2b
- Pliki: `web/catalog.py`, `web/templates/` (karta, lista, kolejka), `web/static/app.css` (tylko istniejące tokeny Pojemników), `tests/test_web_katalog.py`.
- Karta i lista: zamiast „brak kcal” pokazują „~420 kcal (szacunek)”, a także pokrycie „9/11 policzonych”, nieznane składniki oraz plakietki „porcje szacowane” i „niepełny szacunek”. Filtr `szacunki` (wszystkie/źródło/szacunek). Ikony SVG.
- Kolejka „Do sprawdzenia” rozszerzona o składniki `ingredient_nutrition.status='pending'` (edycja 4 liczb + zatwierdź) i przepisy `est_status='pending'` (zatwierdź/odrzuć, przy Ani pokaz obie wartości /100 g). Zatwierdzenie składnika przelicza jego przepisy.
- Test: `uv run pytest tests/test_web_katalog.py`, ruff, mypy. DONE: testy szablonów dla każdej plakietki, filtra i akcji kolejki.
- NIE RÓB: store.py (poza wywołaniem metod z W2b), migracje, pipeline.

## Rollback
- Kod: `git revert` commitów E3c.
- Wydanie: poprzednia wersja 0.1.4 w Supervisorze. Migracja v4 tylko dodaje tabelę i kolumny, więc 0.1.4 na bazie v4 jest w stanie działać (kolumny ignorowane). Sprawdzam to w W2b testem „stary odczyt na bazie v4”, a jeśli test nie przejdzie, stop przed wydaniem.
- Dane: snapshot add-onu przed aktualizacją (Supervisor robi backup przy update, jeśli włączony; sprawdzam przed `release`).

## Weryfikacja
- `uv run pytest` (pełny), ruff, mypy, CI zielone (test + docker amd64/arm64).
- Pomiar walidacji (krok 5) przed wydaniem; pomiar na żywo i znajomości po wydaniu.
- Playwright 390 px jasny i 1280 px ciemny: karta szacunku, kolejka, bez przewijania poziomego.
- Log add-onu: krok `estimate_nutrition` z licznikami (tabela/OFF/LLM, auto/pending), bez błędów.
