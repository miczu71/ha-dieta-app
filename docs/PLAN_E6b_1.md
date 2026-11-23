# E6b.1 — katalog cen w Zakupach (repo `/config/addons/ha-lidl-app`, bez wydania)

Źródło prawdy: `ROADMAP.md` (E6b), plan nadrzędny `PLAN_E6b.md` pkt 2, pomiar `POMIAR_ceny.md`.
Wartość etapu sama z siebie: Zakupy trzymają historię cen (paragony + Frisco) i wystawiają ją przez `/api/v1/catalog`; Dieta jeszcze nic nie zmienia.

## Stan sprawdzony 2026-11-23 (na żywo)
- Zakupy 1.11.0, `SCHEMA_VERSION = 15` (`history.py:58`), migracje wzorcem `_migrate_vN(path)` z kopią `.pre-*` (`history.py:920-960`).
- `shelf_prices` (store, ean) to jeden nadpisywany wiersz („dziś na półce”) — NIE jest historią, zostaje bez zmian.
- Log kontenera `app_f0987e0f_lidl` z 36 h: brak wpisów `shelf`/`error`/429 → limit Biedronki nie dotyka produkcji (429 tylko ze spike'u).
- `items.art_id` Biedronki = `b:{ean}` (`receipt_biedronka.py:42`), a gdy brak EAN `b:{nazwa}`; Lidl: kod Lidla (bez EAN).
- Frisco feed: `https://commerce.frisco.pl/api/v1/integration/feeds/public?language=pl`, JSON `{generatedAt, categories, products}`; produkt: `ean`, `id`, `name.pl`, `brand`, `grammage`, `unitOfMeasure`, `packSize`, `price.price`, `promotions`.
- `/api/v1/*` ma middleware Bearer (`web/app.py:274`), wzorzec endpointu: `api_purchases` (`web/app.py:333`).

## Zakres (decyzje z wywiadu — bez zmian)
1. **Schemat v16** (tabele z `_SCHEMA`, migracja tylko robi kopię `.pre-catalog`):
   - `catalog_products(id INTEGER PK, ean TEXT UNIQUE, name TEXT, brand TEXT, size REAL, size_unit TEXT, frisco_id TEXT UNIQUE, sap TEXT, lidl_art TEXT UNIQUE, aliases TEXT NOT NULL DEFAULT '[]')`;
   - `catalog_obs(id INTEGER PK, product_id → catalog_products, source TEXT ('receipt'|'frisco'|'biedronka'), store TEXT, price REAL, regular REAL, promo_from TEXT, promo_to TEXT, observed_at TEXT)` — tylko dopisywana; indeks `(product_id, source, observed_at)`.
2. **Ingest paragonów** (`catalog.py`, wołany po zapisie paragonu i jednorazowo z backfillem): produkt po EAN (`b:` Biedronki) lub `lidl_art`; obserwacja = cena jednostkowa po rabacie (jak `purchase_aggregates`) z datą paragonu; zapis tylko gdy cena ≠ ostatnia obserwacja tego źródła.
3. **Ingest Frisco** (`frisco_feed.py`): GET feedu (jeden, `User-Agent` generyczny), strumieniowo do pliku w `/data/catalog/` (poza repo), parsowanie, upsert produktu po EAN, obserwacja tylko przy zmianie ceny/promocji; tygodniowo nd 03:00 + `POST /api/v1/catalog/frisco/refresh` (przycisk dopiero w UI późniejszego etapu); blokada współbieżności; zdarzenie `log_event("catalog", ...)`; `NOTICE` w `THIRD_PARTY_NOTICES.md`.
4. **API**: `GET /api/v1/catalog?since=<ISO>&source=` → produkty z ostatnimi obserwacjami per źródło (+ opcjonalnie `history=1` dla promocji); `GET /api/v1/shelf` = widok katalogu dla `source=biedronka` (kontrakt zlecenia EAN→Biedronka wchodzi w E6b.3, tu tylko odczyt cache `shelf_prices` + katalog).
5. Poza zakresem: UI, odpytywanie Biedronki po EAN (429 — powtórka próby 1 zapytanie, potem partie ≤20 co ≥10 s, w E6b.3), mapowanie na składniki (E6b.2), wydanie.

## Kroki
1. Test-first: `tests/test_catalog.py` (schemat, migracja `.pre-catalog`, ingest paragonów, dedupe cen, Frisco z fixture ~20 produktów, API + 401 bez tokenu).
2. `history.py`: schemat, `_migrate_v15`, metody `catalog_*`; `catalog.py`, `frisco_feed.py`; hak w `web/app.py` (endpointy, pętla tygodniowa); `THIRD_PARTY_NOTICES.md`, `CHANGELOG.md` (wpis „niewydane”), `DOCS.md`.
3. `uv run --extra dev pytest -q` (~9 min), `ruff check`, `ruff format --check`, `mypy`.
4. Weryfikacja na kopii żywej bazy (kopia `history.db` z hosta, migracja + backfill + zapytanie `/api/v1/catalog`), bez wdrożenia.
5. Commit w repo Zakupów (bez bumpu wersji), ROADMAP Diety → `stage-end`.

## Wykonawca (kontrakt dla `sonnet-worker` — migracje = „ryzyko”; jedna paczka, kroki zależne)
- **Pliki:** `lidl/app/src/lidl/history.py`, `lidl/app/src/lidl/catalog.py` (nowy), `lidl/app/src/lidl/frisco_feed.py` (nowy), `lidl/app/src/lidl/web/app.py`, `lidl/app/tests/test_catalog.py` (nowy), `lidl/app/tests/fixtures/frisco_feed_small.json` (nowy, ~20 produktów z prawdziwego kształtu), `lidl/THIRD_PARTY_NOTICES.md`→ `THIRD_PARTY_NOTICES.md` w korzeniu repo, `lidl/CHANGELOG.md`, `lidl/DOCS.md`.
- **Zmiana:** jak „Zakres” pkt 1–4; migracja tylko tworzy kopię `.pre-catalog` (wzór `_migrate_v13`); `SCHEMA_VERSION = 16`; obserwacje bez UPDATE/DELETE; ceny w zł, jednostka w polach `size`/`size_unit` z feedu.
- **Komenda testu:** `cd /config/addons/ha-lidl-app/lidl/app && uv run --extra dev pytest -q tests/test_catalog.py tests/test_history.py tests/test_purchases_api.py` (szybkie), pełny `pytest -q` na końcu (nie równolegle z innymi).
- **Seamy testów:** HTTP Frisco wstrzykiwany (`fetch` jako parametr, fixture z pliku — zero sieci); zegar jako parametr `now`; baza `tmp_path`.
- **DONE:** nowe testy zielone + pełny `pytest` zielony; `ruff`/`mypy` czyste; `PRAGMA user_version = 16`; migracja z v15 tworzy `history.db.pre-catalog`; `GET /api/v1/catalog` bez tokenu → 401, z tokenem → produkty z `source`, `price`, `observed_at`; ponowny ingest tego samego feedu nie dodaje obserwacji.
- **NIE RÓB:** nie ruszaj `shelf_prices`/`shelf_prices.py`; nie wołaj API Biedronki ani sieci w testach; nie commituj danych feedu ani niczego z `~/.secrets`; nie zmieniaj `config.yaml` (wersja zostaje 1.11.0); nie commituj ani nie pushuj; nie dotykaj repo Diety.
- **Weryfikator:** `verifier` z tym kontraktem, raportem, `git status --short` sprzed delegacji (BASELINE) i ścieżką worktree (`wt new`).

## Rollback
`git revert` commitu etapu w repo Zakupów; baza: tabele `catalog_*` są tylko dodane (stary kod je ignoruje), awaryjnie przywrócić `history.db.pre-catalog`. Produkcji nie ruszamy do wydania E6b.

## Ryzyka
Feed 52 MB — strumieniowo, jedno pobranie/tydzień; zmiana warunków Frisco → ingest wyłączony flagą, reszta kaskady działa; duplikaty produktów (ten sam towar z EAN i bez) — scalanie dopiero w E6b.2.
