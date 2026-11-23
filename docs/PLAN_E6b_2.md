# E6b.2 — Dieta: katalog Frisco → składniki, współczynnik cen (repo `/config/addons/ha-dieta-app`, bez wydania)

Źródło prawdy: `ROADMAP.md` (E6b), plan nadrzędny `PLAN_E6b.md` pkt 5, pomiar `POMIAR_ceny.md`.
Wartość etapu sama z siebie: Dieta zna ceny rynkowe składników (Frisco × współczynnik) i trzyma wątpliwe mapowania w kolejce; kaskada cen (E6b.3) i UI (E6b.4) dopiero po nim — `build_context`/koszt planu bez zmian.

## Stan sprawdzony 2026-11-23
- Dieta 0.3.0, `_MIGRATIONS` w `store.py` ma 7 wpisów (`user_version` = 7) → nowa migracja = **v8**; tylko `CREATE TABLE`; `store.py` nie robi dziś kopii przed migracją → `Store.__init__` kopiuje istniejący plik do `<path>.pre-v8` (`shutil.copy2`), gdy `user_version` < 8 i baza nie jest pusta (raz; istniejąca kopia nie jest nadpisywana).
- `mapping` (code→ingredient, status rule/seed/ai/pending/approved/excluded) dotyczy kodów zakupu i `ing:<nazwa>`; **nie mieszamy** EAN Frisco z kodami zakupu → osobna tabela.
- `dictionary.py`: `map_with_llm`, `make_llm_ask(url, key, prompt)`, `llm_ask_from(settings, prompt)`, `AskFn` (paczki 25, model `auto:text`, błąd → `None`, brak wpisu → ponowna próba przy następnym przebiegu).
- `pricing.py`: `price_per_100g(Purchase, piece_g)`, `ingredient_prices(...)` (mediana kodów) — bez zmian; nowe funkcje obok.
- Zakupy `GET /api/v1/catalog?source=frisco` zwraca `{generated_at, products:[{ean,name,brand,size,size_unit('Kilogram'|'Litre'|'Piece'),frisco_id,observations:[{source,price,regular,promo_from,promo_to,observed_at}]}]}`; `size` w kg/l, `price` za opakowanie → zł/100 g = `price / (size*1000) * 100`; `Piece` pomijamy. Katalog **nie zawiera kategorii Frisco** → filtr tylko po nazwie + klasa z AI.
- Kody zakupu Biedronki w Zakupach: `b:{ean}` (Dieta może mieć kod z prefiksem lub gołym EAN — parowanie po `code.split(":",1)[-1]`).
- Reguły startowe z pomiaru (`~/dev/biedronka-spike/scripts/e6b_frisco_map.py`) są luźne (np. „pomidory” łapią „klopsiki w sosie pomidorowym”) — surowy wynik reguł to tylko *kandydaci*.

## Zakres
1. **Migracja v8:** `catalog_item(ean TEXT PRIMARY KEY, name TEXT NOT NULL, brand TEXT, size REAL, size_unit TEXT, price REAL NOT NULL, observed_at TEXT NOT NULL, fetched_at TEXT NOT NULL)` (snapshot Frisco, nadpisywany upsertem) oraz `catalog_map(ean TEXT PRIMARY KEY, ingredient_id INTEGER REFERENCES ingredient(id), status TEXT NOT NULL CHECK(status IN ('rule','ai','pending','approved','excluded')), source_name TEXT NOT NULL DEFAULT '')`.
2. **Klient** `zakupy_client.fetch_catalog(base_url, token, source="frisco", *, opener=None) -> list[CatalogItem]` (te same błędy `ZakupyError` i walidacja URL co `fetch_purchases`; `CatalogItem`: ean, name, brand, size, size_unit, price, observed_at — z ostatniej obserwacji `source`; produkty bez obserwacji/ceny pomijane).
3. **Mapowanie** `catalog_map.py` (nowy moduł, czysty + wstrzykiwany `ask`):
   - filtr wstępny: `size_unit != 'Piece'`, `price > 0`, `size > 0`, nazwa bez słów z krótkiej listy non-food/alkohol (`wino`, `piwo`, `wódka`, `whisky`, `szampon`, `żel`, `płyn`, `karma`, `pieluch` …);
   - kandydaci: ten sam dopasowujący stemmer co w spike'u (prefiks 5 liter tokenów nazwy składnika ⊂ tokeny nazwy produktu) dla składników użytych w `recipe_ingredient` (bez `sól`, `woda`, `pieprz*`);
   - `rule` = ścisłe dopasowanie: `len(tokeny_produktu) <= len(stemy_składnika) + 1` (nazwa produktu ≈ nazwa składnika);
   - reszta kandydatów (luźne) → `ask` w paczkach po 25 z promptem „czy produkt jest zwykłym składnikiem <X>?” (odpowiedź JSON `{"i":n,"ok":"tak"|"nie"|"watpliwe"}`): `tak`→`ai`, `nie`→`excluded`, `watpliwe`→`pending`; bez `ask` (brak LLM) luźni kandydaci **nie dostają wpisu** (wrócą po skonfigurowaniu LLM); odpowiedź `None` → brak wpisu (ponowna próba następnym razem);
   - ręczne `approved`/`excluded` nigdy nie nadpisywane; mapowanie tylko dla EAN bez wpisu w `catalog_map` (nowe produkty co tydzień).
4. **Współczynnik** `pricing.receipt_to_frisco_factor(purchases, frisco, piece_g_by_ingredient=None) -> Factor(value, n, by_durability: dict)`: pary kod zakupu↔EAN Frisco (po `code.split(":",1)[-1]`), iloraz `price_per_100g(purchase)` / zł/100 g Frisco, mediana; `value=1.0, n=0` gdy brak par; per trwałość składnika (z `mapping`/`ingredient.durability`) tylko przy `n>=30` w grupie, inaczej globalny. Wynik zapisany w `meta` (`frisco_factor_json`).
5. **Ceny Frisco** `pricing.frisco_prices(items, mapped, factor) -> dict[str, FriscoPrice(price100, n_products, regular100)]`: mediana zł/100 g zmapowanych (`rule`/`ai`/`approved`) produktów × współczynnik trwałości/globalny; składnik bez produktów nie trafia do wyniku.
6. **Sync:** `sync.sync_catalog(store, settings, today, *, fetch=fetch_catalog, ask=None)` — pobranie, upsert `catalog_item` (usuwa wiersze, których nie ma w odpowiedzi? **nie** — zostają, `observed_at` mówi o świeżości), mapowanie nowych EAN, przeliczenie współczynnika, `meta`: `catalog_sync_at`, `catalog_sync_error`; błąd `ZakupyError` → meta, bez wyjątku (jak `sync`). Dziś `sync` woła tylko `web/kupujemy.py:65` (przycisk odświeżenia zakupów; `refresh_time` nie jest podpięty do synca) → `sync_catalog` wołany tuż po nim w tym samym handlerze, tylko gdy skonfigurowano `zakupy_url`; harmonogram tygodniowy = E6b.3.
7. **Store:** `upsert_catalog_items`, `catalog_items`, `catalog_map_rows`, `set_catalog_map`, `pending_catalog()` (kolejka z nazwą produktu i kandydackim składnikiem), `approve_catalog(ean, ingredient_name)`, `exclude_catalog(ean)`.
8. Poza zakresem: UI kolejki zatwierdzania i plakietki (E6b.4), kaskada i użycie cen w koszcie planu (E6b.3), Biedronka po EAN (E6b.3), wydanie, bump wersji.

## Kroki
1. Test-first: `tests/test_catalog_map.py`, rozszerzenia `test_store.py`-owe w nowym `tests/test_store_catalog.py`, `test_zakupy_client.py` (fetch_catalog), `test_pricing.py` (współczynnik, frisco_prices), `test_sync_catalog.py`.
2. Implementacja jak wyżej; `DOCS.md`/`CHANGELOG.md` (wpis „niewydane”).
3. `uv run --extra dev pytest -q` (~3 min), `ruff check`, `ruff format --check`, `mypy`.
4. Pomiar na kopii: kopia `history.db` Zakupów jest dopiero w kroku E6b.1/4 (niewykonany) — w tym etapie `sync_catalog` sprawdzony na fixture `~/dev/biedronka-spike/data/e6b/frisco_feed.json` przez wstrzyknięty `fetch` (skrypt w `scratchpad`, tylko liczby: ile zmapowanych rule/ai/pending, pokrycie składników, `n` par współczynnika) — wynik do raportu, nie do repo.
5. Commit w repo Diety (bez bumpu), ROADMAP → `stage-end`.

## Wykonawca (kontrakt dla `sonnet-worker` — migracja = „ryzyko”; jedna paczka, kroki zależne)
- **Pliki:** `dieta/app/src/dieta/store.py`, `dieta/app/src/dieta/zakupy_client.py`, `dieta/app/src/dieta/catalog_map.py` (nowy), `dieta/app/src/dieta/pricing.py`, `dieta/app/src/dieta/sync.py`, `dieta/app/src/dieta/web/kupujemy.py` (wywołanie `sync_catalog` po `sync`, test w `test_web_kupujemy.py`), `dieta/app/tests/test_catalog_map.py`, `test_store_catalog.py`, `test_sync_catalog.py` (nowe), `test_zakupy_client.py`, `test_pricing.py` (rozszerzenia), `dieta/DOCS.md`, `dieta/CHANGELOG.md`.
- **Zmiana:** jak „Zakres” pkt 1–7; migracja v8 dopisana na końcu `_MIGRATIONS` (istniejących nie edytować); `config.yaml` bez zmian (wersja 0.3.0 zostaje).
- **Komenda testu:** `cd <WORKTREE>/dieta/app && uv run --extra dev pytest -q tests/test_catalog_map.py tests/test_store_catalog.py tests/test_sync_catalog.py tests/test_zakupy_client.py tests/test_pricing.py tests/test_store.py`; na końcu pełny `uv run --extra dev pytest -q`, `ruff check .`, `ruff format --check .`, `mypy` (jak w CI repo).
- **Seamy testów:** `fetch`/`opener` wstrzykiwane (zero sieci), `ask` jako atrapa zwracająca listę słowników, zegar jako parametr `today`, baza `tmp_path`; fixture ~20 produktów Frisco w `tests/fixtures/frisco_catalog_small.json` (kształt odpowiedzi `/api/v1/catalog`).
- **DONE:** nowe testy zielone + pełny pytest zielony; ruff/mypy czyste; `PRAGMA user_version = 8` po migracji ze starej bazy v7 (test z bazą utworzoną starym schematem); ścisłe dopasowanie → `rule`, luźne bez `ask` → brak wpisu, luźne z `ask`=tak/nie/watpliwe → `ai`/`excluded`/`pending`; ręczne `approved` przeżywa ponowny sync; współczynnik: mediana ilorazu z liczbą par, `1.0/n=0` bez par; `ZakupyError` w `sync_catalog` → `catalog_sync_error` w meta, bez wyjątku; `fetch_catalog` bez tokenu / zły URL / HTTP 401 → `ZakupyError` bez tokena w komunikacie.
- **NIE RÓB:** nie zmieniaj `build_context`, `shopping.py`, `plan_service.py`, szablonów ani UI; nie dotykaj repo Zakupów; nie wołaj sieci ani LLM w testach; nie czytaj `~/.secrets`; nie commituj ani nie pushuj; nie edytuj istniejących wpisów `_MIGRATIONS`.
- **Weryfikator:** `verifier` z tym kontraktem, raportem, `git status --short` sprzed delegacji (BASELINE) i ścieżką worktree (`wt new`).

## Rollback
`git revert` commitu etapu w repo Diety; baza: tabele `catalog_*` tylko dodane (stary kod je ignoruje), awaryjnie przywrócić kopię `.pre-v8`. Produkcji nie ruszamy do wydania E6b.

## Ryzyka
Luźne reguły → szum (AI/kolejka go łapie, `rule` tylko ścisłe); brak kategorii w katalogu Zakupów (filtr tylko po nazwie); współczynnik z małej próbki raportowany z `n`; brak LLM → mniej zmapowanych, nic nie psuje.
