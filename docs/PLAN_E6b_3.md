# E6b.3 — Dieta: kaskada cen (repo `/config/addons/ha-dieta-app`, bez wydania)

Źródło prawdy: `ROADMAP.md` (E6b), plan nadrzędny `PLAN_E6b.md` pkt 1, 3, 6; poprzedni etap `PLAN_E6b_2.md`.
Wartość etapu sama z siebie: koszt planu i lista zakupów liczą cenę kaskadą (paragon świeży → Biedronka z cache → paragon starszy → Frisco ≈), każda cena niesie źródło i datę; tydzień-w-tydzień katalog Frisco odświeża się sam.

## Zmiana zakresu względem ROADMAP (2026-11-23, decyzja wykonawcza)
Sprawdzone w repo Zakupów (`web/app.py:380`): `/api/v1/shelf` jest dziś **tylko GET** (cache `shelf_prices`); zlecenia EAN („POST listy EAN → zlecenie w tle”) nie istnieją. Nocna kolejka EAN i powtórka próby Biedronki wymagają zmiany w **drugim repo** (Zakupy) → osobny etap:
- **E6b.3 (ten plan, tylko Dieta):** kaskada + odczyt cache Biedronki przez istniejący GET + harmonogram tygodniowy `sync_catalog` + tryb „taniej” na nowych cenach.
- **E6b.3b (następny, Zakupy + Dieta):** `POST /api/v1/shelf` (zlecenie w tle, blokada, 1 zapytanie próbne → partie ≤20 co ≥10 s), nocna kolejka EAN w Diecie, przycisk „Odśwież ceny planu”. Plan `PLAN_E6b_3b.md` przed startem.
Bez E6b.3b kaskada działa: gałąź Biedronki widzi to, co Zakupy już mają w `shelf_prices` (ręczna /polka).

## Stan sprawdzony 2026-11-23
- Dieta 0.3.0, migracje do v8 (`catalog_item`, `catalog_map`), `store.py` kopiuje `.pre-v8` w `Store.__init__` → nowa migracja = **v9** (tylko `CREATE TABLE`; kopia `.pre-v9` tym samym mechanizmem, uogólnić bez zmiany istniejącego zachowania).
- `shopping.build_context(store)` → `Context.prices: dict[str, IngredientPrice]` (`ingredient_prices(purchases, mapping, piece_by_name)`); konsumenci czytają `price.price100` i `price.pack_g` (`shopping.py:206–317`) — **nie zmieniać ich**; kaskada dokłada pola do `IngredientPrice` (domyślne wartości = zachowanie dzisiejsze).
- `Purchase`: `unit_price` (mediana płaconej), `last_unit_price`, `last` (data), `is_weight`, `code` (`b:{ean}` Biedronka). `receipt_to_frisco_factor` ma martwy parametr `ingredient_by_code` (NIEBLOKUJĄCE z E6b.2) → w tym etapie **użyć** go (mapa kod→składnik z `mapping`, już dostępna w `sync_catalog`).
- Zakupy `GET /api/v1/shelf` → `{generated_at, store, items:[{ean, price, regular, omnibus, promo_from, promo_to, is_promotion, card_only, fetched_at, stock, product:{name,brand,size,size_unit,…}|null}]}`.
- Nocna pętla: `web/app.py:_nightly` → `runner()` = `default_runner` → `sync` + `sync_catalog` (dziś **codziennie**, bo `sync_catalog` nie ma bramki czasu).

## Zakres
1. **Migracja v9:** `shelf_item(ean TEXT PRIMARY KEY, name TEXT, size REAL, size_unit TEXT, price REAL NOT NULL, regular REAL, promo_from TEXT, promo_to TEXT, is_promotion INTEGER NOT NULL, card_only INTEGER NOT NULL, fetched_at TEXT NOT NULL, store TEXT)` — snapshot cache Biedronki, nadpisywany upsertem. `Store.upsert_shelf_items`, `shelf_items()`.
2. **Klient:** `zakupy_client.fetch_shelf(base_url, token, *, opener=None) -> list[ShelfItem]` (`ShelfItem`: ean, name, size, size_unit, price, regular, promo_from, promo_to, is_promotion, card_only, fetched_at); te same błędy/walidacja co reszta (`_get_json`); wiersze bez ceny >0 pomijane; `product=null` → name/size `None`.
3. **Sync:** `sync_shelf(store, settings, today, *, fetch=fetch_shelf) -> int` (upsert, meta `shelf_sync_at` / `shelf_sync_error`, `ZakupyError` → meta bez wyjątku); wołany w `default_runner._run` po `sync_catalog` (bez bramki — to tani GET).
4. **Harmonogram tygodniowy:** `sync_catalog(..., force=False)`: gdy `force` fałsz i `meta.catalog_sync_at` ≤ 7 dni temu → zwraca 0 bez pobierania i bez zmiany `catalog_sync_error` (nocna pętla). Przycisk odświeżenia (`/kupujemy` refresh) przekazuje `force=True` (runner dostaje parametr `force: bool = False`; `SyncRunner` nadal wołany bez argumentów przez `_nightly`).
5. **Kaskada** `pricing.cascade_prices(purchases, mapping, piece_g_by_ingredient, *, frisco, shelf, today, week_start, week_end) -> dict[str, IngredientPrice]`; `IngredientPrice` dostaje `source: str = "receipt"` (`receipt`|`receipt_old`|`shelf`|`promo`|`frisco`), `as_of: date | None = None`, `promo_to: str | None = None`, `estimate: bool = False` (domyślne = stare zachowanie, `ingredient_prices` bez zmian). Dla składnika, w kolejności, pierwszy trafiony:
   a. **paragon ≤30 dni** — kody składnika z `last >= today-30`: cena z `last_unit_price` (przez `price_per_100g` na kopii z `unit_price=last_unit_price`); gdy `last_unit_price < 0.8 × unit_price` (promocja/kupon), użyj `unit_price` (mediana). Wiele kodów: mediana. `source="receipt"`, `as_of` = najnowsze `last`.
   b. **Biedronka z cache ≤7 dni** — `shelf_item.fetched_at >= today-7`, EAN → składnik przez (`catalog_map` rule/ai/approved) albo kod zakupu `b:{ean}` ∈ `mapping`; zł/100 g: `size_unit` Kilogram/Litre → `price/(size*1000)*100`, inaczej `parse_pack_g([name])`, brak gramatury → pomiń. Cena promocyjna (`price`), gdy `is_promotion` i `not card_only` i okno `[promo_from, promo_to]` przecina `[week_start, week_end]` → `source="promo"`, `promo_to`; inaczej `regular or price` → `source="shelf"`. Wiele EAN: najniższa. `as_of` = data z `fetched_at`.
   c. **paragon 31–180 dni** — jak (a), ale `unit_price` (mediana), `source="receipt_old"`.
   d. **Frisco ≈** — `frisco_prices(...)` (już po współczynniku), `source="frisco"`, `estimate=True`, `as_of` = `catalog_sync_at`; gdy brak Frisco, a jest paragon >180 dni → `receipt_old`.
   `pack_g` zachowane z paragonów (jak dziś), gdy znane.
6. **Użycie:** `shopping.build_context` woła `cascade_prices` zamiast `ingredient_prices`; `today`/tydzień z planu: `build_context(store, today=None, week=None)` — gdy brak planu, `week_start=today`, `week_end=today+6`. Frisco/shelf/współczynnik czytane ze `Store` (`catalog_items`, `catalog_map_rows`, `meta.frisco_factor_json`, `shelf_items`). **Cache per request** (dług z E6): wynik `build_context` memoizowany do `store.data_version` — jeśli `Store` nie ma licznika, zwykły słownik `(prices_version)`; wystarczy: nie liczyć kaskady dwa razy w jednym żądaniu (test: dwa wywołania → jedno wywołanie `cascade_prices`).
7. **Tryb „taniej”:** bez zmiany logiki planera — korzysta z `home_ratio`; cena wpływa przez `Context.prices` (kaskada obejmuje więcej składników). Test regresji: plan z `cheaper_mode=True` nie pada i `home_ratio` nadal liczone.
8. **Poza zakresem:** UI/plakietki/podział pokrycia i „—” zamiast 0,00 zł (E6b.4), komunikaty todo (E6b.4), `POST /api/v1/shelf`, nocna kolejka EAN, powtórka próby Biedronki (E6b.3b), wydanie, bump wersji.

## Kroki
1. Test-first (`tdd`): `tests/test_pricing_cascade.py` (każda gałąź a–d + kolejność + próg 0.8 + promocja w/poza tygodniem + card_only + brak gramatury), `test_store_shelf.py`, `test_zakupy_client.py` (`fetch_shelf`), `test_sync_catalog.py` (`force`, bramka 7 dni, `sync_shelf`), `test_shopping.py` (kaskada w `build_context`, cache per request), `test_web_kupujemy.py` (przycisk → `force=True`).
2. Implementacja jak wyżej; `DOCS.md`/`CHANGELOG.md` (wpis „niewydane”).
3. `uv run --extra dev pytest -q` (~3 min), `ruff check .`, `ruff format --check .`, `mypy`.
4. Pomiar na fixture (`tests/fixtures/frisco_catalog_small.json` + feed `~/dev/biedronka-spike/data/e6b/frisco_feed.json`): skrypt w scratchpad, tylko liczby — ile składników per `source`; do raportu, nie do repo.
5. Commit w repo Diety (bez bumpu), ROADMAP → `stage-end`.

## Wykonawca (kontrakt dla `sonnet-worker` — migracja + pieniądze = „ryzyko”; jedna paczka, kroki zależne)
- **Pliki:** `dieta/app/src/dieta/store.py`, `zakupy_client.py`, `pricing.py`, `sync.py`, `shopping.py`, `web/kupujemy.py`, `dieta/app/tests/test_pricing_cascade.py` (nowy), `test_store_shelf.py` (nowy), rozszerzenia `test_zakupy_client.py`, `test_sync_catalog.py`, `test_shopping.py`, `test_web_kupujemy.py`, `dieta/DOCS.md`, `dieta/CHANGELOG.md`.
- **Zmiana:** jak „Zakres” pkt 1–7; migracja v9 dopisana na końcu `_MIGRATIONS` (istniejących nie edytować); kopia `.pre-v9` bez zmiany zachowania `.pre-v8`; `config.yaml` bez zmian (0.3.0).
- **Komenda testu:** `cd <WORKTREE>/dieta/app && uv run --extra dev pytest -q tests/test_pricing_cascade.py tests/test_store_shelf.py tests/test_zakupy_client.py tests/test_sync_catalog.py tests/test_shopping.py tests/test_web_kupujemy.py tests/test_pricing.py`; na końcu pełny `uv run --extra dev pytest -q`, `ruff check .`, `ruff format --check .`, `mypy`.
- **Seamy testów:** `fetch`/`opener` wstrzykiwane (zero sieci), zegar jako parametr `today`, baza `tmp_path`, ceny jako małe literały `Purchase`/`CatalogItem`/`ShelfItem`; `cascade_prices` czysta funkcja (bez bazy).
- **DONE:** nowe testy zielone + pełny pytest zielony, ruff/mypy czyste; `PRAGMA user_version = 9` po migracji ze starej bazy v8 (test na bazie starego schematu, plik `.pre-v9` powstaje raz); kolejność kaskady a→b→c→d potwierdzona testem na jednym składniku z wszystkimi źródłami; próg 0.8 (ostatni 2,00 vs mediana 3,00 → 3,00); promocja tylko gdy okno przecina tydzień planu i nie `card_only`; cache Biedronki starszy niż 7 dni ignorowany; Frisco → `estimate=True`; `sync_catalog` w 7 dni po poprzednim: 0 i brak wywołania `fetch`, z `force=True`: pobiera; `ZakupyError` w `sync_shelf` → meta, bez wyjątku; `receipt_to_frisco_factor` używa `ingredient_by_code` (test: pakowany bez gramatury liczony z masy sztuki); `build_context` woła `cascade_prices` raz na żądanie; dotychczasowe testy `shopping`/`plan_service` bez zmian zielone (stare zachowanie przy braku Frisco/shelf).
- **NIE RÓB:** nie zmieniaj konsumentów `price.price100`/`pack_g` w `shopping.py:206–317`, szablonów ani UI; nie dodawaj `POST /api/v1/shelf` ani niczego w repo Zakupów; nie wołaj sieci ani LLM w testach; nie czytaj `~/.secrets`; nie używaj `pkill`; nie commituj ani nie pushuj; nie edytuj istniejących wpisów `_MIGRATIONS`.
- **Weryfikator:** `verifier` z tym kontraktem, raportem, `git status --short` sprzed delegacji (BASELINE) i ścieżką worktree (`wt new`).

## Rollback
`git revert` commitu etapu; baza: `shelf_item` tylko dodana (stary kod ją ignoruje), awaryjnie kopia `.pre-v9`. Produkcji nie ruszamy do wydania E6b.

## Ryzyka
Kaskada zmienia koszt planu (pieniądze) → testy kolejności i progów, plakietki dopiero w E6b.4 (do tego czasu koszt może się różnić od 0.3.0 — niewydane); `last_unit_price` to jedyna świeża cena (brak mediany 30 dni w agregacie) → próg 0.8 względem mediany rocznej; cache Biedronki pusty do E6b.3b → gałąź (b) w praktyce nieaktywna poza ręczną /polką.
