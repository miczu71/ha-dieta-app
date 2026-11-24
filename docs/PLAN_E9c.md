# PLAN E9c — testy i refaktory (Dieta W1 + Zakupy W2)

Źródło: `docs/ROADMAP_E9.md` § E9c. Rollback: `git revert` commitów paczek (bez migracji). Numery linii z ROADMAP są stare — szukaj po nazwie.
Repo: Dieta `/config/addons/ha-dieta-app` (`dieta/app`), Zakupy `/config/addons/ha-lidl-app` (`lidl/app`). Rozłączne pliki → W1 i W2 równolegle (haiku-worker, worktree `wt new <repo> e9c-W1|W2`).

## Wykonawca W1 — Dieta (haiku-worker)
Pliki (src `dieta/app/src/dieta`, testy `dieta/app/tests`):
1. Nowy `dates.py`: `def parse_iso_date(value: str | None) -> date | None` — bierze `value[:10]`, `None` dla pustego/niepoprawnego (`ValueError`, `TypeError`). Test `tests/test_dates.py` (ISO, ISO z czasem, `""`, `None`, śmieci).
2. Zamień lokalne parsery na `parse_iso_date`: `shopping.py:_iso_date`, `pricing.py:_day`, `shelf_queue.py:_fresh_eans` (pętla z try/except → `if (fetched := parse_iso_date(item.fetched_at)) is None: continue`),
   `web/lista.py:_short_date` (`d = parse_iso_date(v); return d.strftime("%d.%m") if d else None`), `catalog.py:_skip_active` (wejście `"<data>[ powód]"`: `parse_iso_date((value or "").split(" ", 1)[0])`, `None` → `False`).
   Zachowanie bez zmian (istniejące testy zielone).
3. `web/kupujemy.py:queue_size`: nowe `Store.count_pending_recipe_meta()`, `count_pending_ingredient_nutrition()`, `count_pending_catalog()` (`SELECT COUNT(*)` z tymi samymi warunkami co `pending_*`; wzór `count_pending_estimates`). `review_queue` ma filtr w Pythonie — zostaje `len(review_queue(store))`. Test: `queue_size` == suma długości list przy niepustych kolejkach.
4. `tests/test_shopping.py` ~l.254: zamiast `date.today()` stała data (`date(2026, 11, 24)`) przekazana jawnie do `plan_cost`/`shopping_list`; asercja `len(calls) == 1` bez zmian.
5. Nowe testy: (a) `_prompt_safe` — pusta nazwa → `""`, nazwa 120 znaków zostaje, 121 → 120 (plik testów `dictionary`); (b) `sync_errors` — wszystkie 3 klucze naraz dają 3 wpisy w kolejności `SYNC_ERROR_LABELS` (`tests/test_web_kupujemy.py`); (c) `refresh()` z przekroczonym limitem bajtów (`ResponseTooLarge` z `http_limit`) → błąd zapisany w `catalog_sync_error`, nie wyjątek (plik testów `sync` z istniejącym wzorcem fake fetch); (d) `zakupy_client._reason`: odpowiedź > 64 KB nie wywala i nie przecieka treści.
Seamy testów: istniejące fixtury `tmp_path` + `Store`, bez sieci; żadnych mocków `date` globalnych.
Komenda: `cd <wt>/dieta/app && UV_LINK_MODE=copy uv run --extra dev pytest -q tests/test_dates.py tests/test_shopping.py tests/test_pricing.py tests/test_web_kupujemy.py tests/test_web_lista.py tests/test_shelf_queue.py` (+ pliki z pkt 5) `&& uv run --extra dev ruff check . && uv run --extra dev mypy src`. Pełny pytest (~320 s) robi rodzic po `wt land`.
DONE: nowy helper użyty w 5 miejscach, brak `fromisoformat` w tych 5 funkcjach; `queue_size` bez materializacji 3 list; 5 grup testów dodanych; wskazane testy + ruff + mypy zielone.
NIE RÓB: `store.py` poza trzema metodami `count_*`; `_prompt_safe` zostaje prywatne; pozostałe `fromisoformat` (store, plan_service, web/app, dzis, sync, zakupy_client) nie ruszać; nie commituj; nic w repo Zakupów.

## Wykonawca W2 — Zakupy (haiku-worker)
Pliki (src `lidl/app/src/lidl`, testy `lidl/app/tests`):
1. Wspólna pętla harmonogramu w `daily.py`: `async def scheduled_loop(seconds_until: Callable[[datetime], float], job, *, now, sleep, label: str)` — `while True: sleep(seconds_until(now())); try job() except Exception: log.exception("... %s", label)`. `at_time_loop(at, job, ...)` = `scheduled_loop(lambda n: seconds_until(n, at), job, ..., label="Dzienny przebieg")`; `frisco_feed.weekly_loop` używa `scheduled_loop(seconds_until_weekly, lambda: refresh(...), label="Katalog cen: przebieg tygodniowy")`. Sygnatury publiczne i komunikaty logów zostają (testy w `test_daily.py`/`test_catalog.py` mogą na nie patrzeć).
2. `frisco_feed.ingest`/`_ingest_one`: bez N+1 — jeden SELECT istniejących EAN (nowa metoda `History.catalog_existing_eans(eans) -> set[str]` albo równoważna) i zapisy produktów/obserwacji w partii (`executemany`) wewnątrz `catalog_bulk()`. Zachowanie: te same `Result.products/observations/skipped`, zły produkt nadal pomijany i liczony. Jeśli `catalog_upsert_product` ma logikę `authoritative`, której nie da się odwzorować w `executemany` bez zmiany wyniku — zostaw upsert per produkt, a usuń tylko zbędne SELECT-y; opisz w raporcie.
3. `web/search_view.py:_day` → użyj `chart.parse_date` rozszerzonego o `value[:10]` (jeden parser: `chart.parse_date`; `search_view` importuje go). `leaflet.py` NIE ruszać.
4. `tests/test_catalog.py`: zamień odczyty `h._db.execute("SELECT ...")` danych katalogu na publiczne `History.catalog_products(...)`/inne publiczne metody tam, gdzie istnieją; asercje schematu (`PRAGMA user_version/table_info/index_list`) zostają. W raporcie wypisz, które `h._db` zostały i dlaczego.
5. Nowe testy granic: `shelf_prices.STORE_RE` (32 znaki OK, 33 nie, pusty nie, `"0001"` OK) przez publiczną ścieżkę walidacji `store` w `web/app.py:~432` (test klienta HTTP w `test_shelf_prices.py` lub `test_web.py`; nie-string `store` → odrzucony); `EAN_RE` 8 cyfr OK, 7 i 14 nie (`test_shelf_prices.py`).
Komenda: `cd <wt>/lidl/app && UV_LINK_MODE=copy uv run --extra dev pytest -q tests/test_daily.py tests/test_catalog.py tests/test_chart.py tests/test_search.py tests/test_shelf_prices.py tests/test_web.py && uv run --extra dev ruff check . && uv run --extra dev mypy src`.
DONE: jedna pętla harmonogramu użyta w 2 miejscach; ingest Frisco bez zbędnych zapytań per produkt (lub uzasadniony wyjątek); jeden parser dat dla search/chart; testy z pkt 4–5; wskazane testy + ruff + mypy zielone.
NIE RÓB: `leaflet.py`, migracje/schemat `history.py` (poza jedną nową metodą odczytu), `catalog._ean`, repo Diety; nie commituj.

## Po paczkach (rodzic)
`verifier` po każdej paczce (zadanie, raport, BASELINE `git status --short`, ścieżka worktree) → `wt land` → pełny pytest Dieta w tle (nohup + Monitor) i Zakupy `pytest -q` → komentarz o `data_version` (jeden worker uvicorn? sprawdź komendę startu add-onu; jeśli tak — tylko komentarz) → commity → `stage-end`.
