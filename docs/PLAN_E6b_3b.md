# E6b.3b — zlecenia cen Biedronki po EAN (Zakupy) + nocna kolejka EAN (Dieta), bez wydania

Źródło prawdy: `ROADMAP.md` (E6b), `PLAN_E6b.md` pkt 3–4, `PLAN_E6b_3.md` (zmiana zakresu). Dwa repo, dwie rozłączne paczki:
**W1** = Zakupy (`/config/addons/ha-lidl-app`), **W2** = Dieta (`/config/addons/ha-dieta-app`). Kontrakt API jest ustalony poniżej,
więc W2 testuje na atrapie HTTP i nie czeka na W1.
Wartość etapu: ceny półkowe Biedronki dla składników zatwierdzonego planu pojawiają się same (gałąź b kaskady przestaje być martwa).
Ryzyko: prywatne API Biedronki (429 w E6b.0) → łagodne tempo, jedno zapytanie próbne, przerwanie przy pierwszym 429.

## Kontrakt API (Zakupy ↔ Dieta)
`POST /api/v1/shelf` (token jak reszta `/api/v1/*`, `guard` już obejmuje), JSON `{"eans": ["5900…", …], "store": "<opcjonalnie>"}`:
- walidacja: `eans` lista 1–100 stringów `\d{8,13}` (duplikaty zdejmowane); inaczej **400**. Brak sklepu (`biedronka_store`) albo połączonego konta → **409** `{"detail": "...", "reason": "no_store"|"no_account"}`. `shelf_lock` zajęty → **409** `{"reason": "busy"}`.
- odpowiedź **202** `{"accepted": N, "skipped_fresh": F, "skipped_absent": A}`; zlecenie biegnie w tle (`asyncio.create_task`, pod `app.state.shelf_lock`, ten sam zamek co poranny odczyt i `/polka`).
- `skipped_fresh`: wiersz `shelf_prices` z `fetched_at` ≤7 dni; `skipped_absent`: EAN z HTTP 404 w ostatnich 30 dniach („brak w Biedronce”).
`GET /api/v1/shelf` dostaje dodatkowe pole `"absent": [ean, …]` (404 ≤30 dni dla sklepu); reszta odpowiedzi bez zmian.

## Tempo (obie strony nie łamią E6b.0)
Zakupy: najpierw **1 zapytanie próbne** (`product_price`, pierwszy EAN do odczytu); błąd inny niż 404 (w tym 429) → przerwanie całego zlecenia, `log_event("shelf","warn"/"error")`, nic więcej nie wysyłamy. Potem **partie ≤20 EAN**, w partii pauza `REQUEST_PAUSE_S` (1 s) między zapytaniami, **≥10 s między partiami**. Tylko `product_price` (bez `product_stock` — połowa zapytań). 429/`LidlPlusAuthError`/`LidlPlusCannotConnect` w trakcie → przerwanie, zapis tego, co odczytane.

---

## W1 — Zakupy (`ha-lidl-app`, ryzyko → `sonnet-worker`)
### Zakres
1. **Schemat (migracja v17):** tabela `shelf_absent(store TEXT NOT NULL, ean TEXT NOT NULL, seen_at TEXT NOT NULL, PRIMARY KEY (store, ean))` w `_SCHEMA` (`CREATE TABLE IF NOT EXISTS`) + `SCHEMA_VERSION` 17 + krok migracji z kopią `.pre-absent` — wzorzec migracji v12→v13 („shelf”, `history.py:1168`). `History.shelf_absent_mark(store, eans, now)`, `shelf_absent_list(store, since)`, `shelf_absent_clear(store, eans)` (EAN, który wrócił z ceną, wypada z listy).
2. **`shelf_prices.refresh_eans(history, api, store, eans, *, sleep=asyncio.sleep, now=None) -> RefreshResult`:** filtr świeżych (`shelf_list`, ≤7 d) i „brak” (≤30 d); próba 1 zapytaniem; partie ≤20, stałe `BATCH_SIZE=20`, `BATCH_PAUSE_S=10.0`; `parse_price` → `shelf_replace`; 404 → `shelf_absent_mark`; reszta błędów jak w `refresh` (przerwanie przy auth/connect/429, zapis tego co odczytane w `finally`); `log_event("shelf", …)` jak w `refresh`. Nie ruszać `refresh`/`refresh_morning` i ich zachowania (poza wspólnym helperem, jeśli czysty).
3. **Endpoint:** `POST /api/v1/shelf` jak w kontrakcie (`web/app.py` przy `api_shelf`); zadanie w tle trzyma referencję (`app.state.shelf_tasks` set) i łapie wyjątki (log bez tokenów); `GET /api/v1/shelf` + `absent`.
4. `DOCS.md`/`CHANGELOG.md` Zakupów: wpis „niewydane” (E6b.3b).

### Wykonawca
- **Pliki:** `lidl/app/src/lidl/history.py`, `shelf_prices.py`, `web/app.py`; testy nowe/rozszerzone w `lidl/app/tests/` (`test_shelf_refresh_eans.py` nowy, `test_api_shelf.py` albo istniejący plik testów `/api/v1/shelf` — znajdź grepem; `test_history*` dla migracji); `lidl/DOCS.md`, `lidl/CHANGELOG.md`.
- **Komenda testu:** `cd <WORKTREE>/lidl/app && uv run --extra dev pytest -q tests/test_shelf_refresh_eans.py <plik testów API shelf> <plik testów migracji>`; na końcu pełny `uv run --extra dev pytest -q` (12–19 min, **nie równolegle** z testami W2), `ruff check .`, `ruff format --check .`, `mypy`.
- **Seamy testów:** `api` to atrapa z `product_price`; `sleep` wstrzykiwany i zapisywany (asercje: pauzy 1 s w partii, 10 s między partiami, brak pauzy po ostatniej); `now` jako parametr; baza `tmp_path`; endpoint przez `TestClient`/istniejące fixture z tokenem; zadanie w tle w teście awaitowane przez `app.state.shelf_tasks`.
- **DONE:** testy zielone + pełny pytest, ruff/mypy czyste; `PRAGMA user_version = 17` po migracji ze starej bazy (plik `.pre-absent` raz); 1 zapytanie próbne z błędem 429 → zero dalszych zapytań i `RefreshResult.failed`; 45 EAN → partie 20+20+5 z 2 pauzami 10 s; EAN świeży (≤7 d) i „brak” (≤30 d) pominięty bez zapytania, „brak” starszy niż 30 d odpytany ponownie; 404 → `shelf_absent`, a EAN z ceną usuwany z `shelf_absent`; POST: 400 (zły EAN/pusta/>100), 401 bez tokenu, 409 `no_store`/`no_account`/`busy`, 202 z licznikami; `GET` ma `absent`; istniejące testy `refresh`/`/polka`/`/api/v1/shelf` bez zmian zielone.
- **NIE RÓB:** nie zmieniaj `refresh`, `refresh_morning`, `daily.py`, szablonów, `/polka`; nie wołaj sieci w testach; nie czytaj `~/.secrets`; bez `pkill`; bez commitu/pusha; bez bumpu wersji; nie edytuj istniejących kroków migracji.

---

## W2 — Dieta (`ha-dieta-app`, `haiku-worker`)
### Zakres
1. **Klient:** `zakupy_client.post_shelf_request(base_url, token, eans, *, opener=None) -> ShelfRequestResult(accepted, skipped_fresh, skipped_absent)`; 409 `busy` → `ShelfRequestResult` z `busy=True` (bez wyjątku); inne błędy → `ZakupyError` jak reszta klienta. `fetch_shelf` zwraca dodatkowo `absent` (lista EAN) — **bez łamania istniejącej sygnatury**: nowa funkcja `fetch_shelf_absent(...)` albo `fetch_shelf_full` zwracająca `(items, absent)`; `sync_shelf` zapisuje `absent` do `meta.shelf_absent_json`.
2. **Kolejka:** `shelf_queue.py`: `plan_eans(store, today) -> list[str]` (EAN-y składników zatwierdzonego planu bieżącego i następnego tygodnia, przez `catalog_map` approved/rule/ai oraz kody `b:{ean}` z `mapping`; znajdź w `shopping.py` funkcję liczącą składniki planu i ją użyj); `catalog_eans(store) -> list[str]` (EAN-y zmapowane na składniki używane w przepisach, reszta katalogu); `build_queue(store, today, *, limit=100, plan_only=False)` = plan, potem katalog (chyba że `plan_only`), bez duplikatów, bez EAN z `shelf_item.fetched_at` ≤7 d i bez `shelf_absent_json`, obcięte do `limit`, kolejność stabilna (posortowana).
3. **Sync:** `sync_shelf_queue(store, settings, today, *, post=post_shelf_request, plan_only=False) -> int` (liczba `accepted`); `meta.shelf_queue_at`, `shelf_queue_error` (`ZakupyError`/brak konfiguracji → meta, bez wyjątku; `busy` → meta `busy`, brak błędu). Wołany w `default_runner._run` **przed** `sync_shelf`, tylko gdy `settings.zakupy_url`; w nocnej pętli (nie w `force` ręcznym odświeżeniu katalogu).
4. **Przycisk „Odśwież ceny planu”:** `POST /kupujemy/ceny` (`web/kupujemy.py` + przycisk w szablonie ekranu „Co kupujemy”, `back(..., notice="ceny")`): `sync_shelf_queue(plan_only=True)` w `run_in_threadpool`; komunikat „Zlecono odczyt N cen — pojawią się za kilka minut.” / „Odczyt już trwa.” / „Nie udało się zlecić (szczegóły w logu)”. Nie robi `sync_shelf` (wynik przyjdzie nocą albo przy następnym odświeżeniu).
5. `DOCS.md`/`CHANGELOG.md`: wpis „niewydane”.

### Wykonawca
- **Pliki:** `dieta/app/src/dieta/zakupy_client.py`, `shelf_queue.py` (nowy), `sync.py`, `web/kupujemy.py`, szablon ekranu „Co kupujemy” (znajdź w `dieta/app/src/dieta/web/templates/`), `dieta/app/tests/test_shelf_queue.py` (nowy), rozszerzenia `test_zakupy_client.py`, `test_sync_catalog.py`, `test_web_kupujemy.py`, `dieta/DOCS.md`, `dieta/CHANGELOG.md`.
- **Komenda testu:** `cd <WORKTREE>/dieta/app && uv run --extra dev pytest -q tests/test_shelf_queue.py tests/test_zakupy_client.py tests/test_sync_catalog.py tests/test_web_kupujemy.py`; na końcu pełny `uv run --extra dev pytest -q` (~3 min), `ruff check .`, `ruff format --check .`, `mypy`.
- **Seamy testów:** `post`/`fetch`/`opener` wstrzykiwane (zero sieci), `today` jako parametr, baza `tmp_path`, małe literały `CatalogItem`/`ShelfItem`/plan; kolejka to czysta funkcja nad `Store`.
- **DONE:** testy zielone + pełny pytest, ruff/mypy czyste; kolejka: składniki planu przed katalogiem, limit 100, bez duplikatów, bez świeżych (≤7 d) i „brak” (z `shelf_absent_json`), `plan_only` pomija katalog; `post_shelf_request` parsuje 202, 409 busy → `busy=True`, 400/401/5xx → `ZakupyError` (test z atrapą opener); `sync_shelf_queue` przy `ZakupyError` zapisuje meta bez wyjątku, bez konfiguracji nic nie wysyła; nocny runner woła kolejkę przed `sync_shelf`, ręczne odświeżenie `/kupujemy/odswiez` jej nie woła; `POST /kupujemy/ceny` → komunikat zależny od wyniku (3 testy); istniejące testy bez zmian zielone.
- **NIE RÓB:** nie zmieniaj `pricing.py`/`shopping.py` (poza odczytem funkcji z planem), migracji `store.py`, `config.yaml` (0.3.0); nic w repo Zakupów; nie wołaj sieci/LLM w testach; nie czytaj `~/.secrets`; bez `pkill`; bez commitu/pusha.

## Kolejność i weryfikacja
1. `wt new` dla obu repo; W1 (`sonnet-worker`) i W2 (`haiku-worker`) równolegle w tle; pełne pytest-y **sekwencyjnie** (Zakupy ciężkie).
2. Po każdej paczce `verifier` (kontrakt paczki, raport, `git status --short` BASELINE, ścieżka worktree); `claimcheck` przed verifierem dla `~/dev/*` — te repo to add-ony, pomijam.
3. `wt land` obu, commity w obu repo (bez bumpu), ROADMAP → `stage-end`.
4. Test integracyjny na żywo **odroczony do E6b-release** (POST na żywych Zakupach = zapytania do prywatnego API; robimy po wydaniu, 1 EAN próbny).

## Rollback
`git revert` commitów etapu w obu repo. Zakupy: `shelf_absent` tylko dodana (kopia `.pre-absent`), stary kod ją ignoruje. Dieta: bez migracji; nocna kolejka wyłączalna brakiem `zakupy_url`. Produkcji nie ruszamy do wydania.

## Ryzyka
Prywatne API (429) → próba + przerwanie, nocą ≤100 EAN (≈5 partii, ≥40 s przerw); `absent` dodane do GET (kompatybilne wstecz, stara Dieta je ignoruje); stan wydania: Zakupy 1.12.0 musi wyjść **przed** Dietą 0.4.0 (Dieta bez POST dostaje 405 → `ZakupyError` w meta, nic się nie psuje).
