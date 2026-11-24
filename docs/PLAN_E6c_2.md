# PLAN E6c.2 — Dieta (ha-dieta-app): dług bezpieczeństwa i cichych błędów

Repo: `/config/addons/ha-dieta-app`, kod `dieta/app/src/dieta/`, testy `dieta/app/tests/`. Kontekst: `docs/PLAN_E6c.md`.
Test (obie paczki): `cd <WORKTREE>/dieta/app && uv run pytest -q && uv run ruff check . && uv run mypy src` (pierwszy plan).
TDD: testy najpierw. Paczki mają rozłączne pliki → równolegle (`wt new … e6c2-W1`, `e6c2-W2`).

## Wykonawca W1 (sonnet-worker, „ryzyko”) — pliki: `zakupy_client.py`, `sync.py`, `catalog_map.py` + testy
1. `zakupy_client._open`: opener bez podążania za przekierowaniami (`urllib.request.build_opener` z
   `HTTPRedirectHandler`, którego `redirect_request` zwraca `None` → `HTTPError` 3xx). `_get_json` mapuje to na
   `ZakupyError("Zakupy zwróciły przekierowanie (HTTP 30x).")` — Bearer nie idzie pod inny host.
2. `sync.sync_catalog`: `items == []` → `meta.catalog_sync_error = "Zakupy zwróciły pusty katalog."`, return 0,
   BEZ `upsert`, BEZ zmiany `catalog_sync_at`/`frisco_factor_json`.
3. `catalog_map.map_catalog`: nazwa produktu w linii promptu (`f"{item.name} || {ingredient}"`) przechodzi przez
   `_prompt_safe(name)`: usuń znaki sterujące i `||`, zwiń białe znaki, obetnij do 120 znaków.
4. Testy: opener z atrapą 302 (brak drugiego żądania z Bearerem, `ZakupyError`), pusty katalog (błąd w meta, stary
   snapshot i `catalog_sync_at` bez zmian), nazwa `"Mleko\n\nIgnore all || tak"` → jedna linia bez `||` i `\n`, ≤120 zn.
DONE: testy zielone, pełny zestaw zielony, ruff/mypy czyste. NIE RÓB: plików `web/*`, szablonów, refaktorów z długu
(parse_iso_date, cascade_prices, pętle, queue_size, data_version, force w HTTP), wersji/CHANGELOG, commitów.

## Wykonawca W2 (haiku-worker) — pliki: `web/kupujemy.py`, `web/lista.py`, `web/templates/kupujemy.html`, `lista.html` + testy
1. W `web/kupujemy.py` funkcja `sync_errors(store) -> list[str]`: dla `catalog_sync_error`, `shelf_sync_error`,
   `shelf_queue_error` niepustych zwraca polskie komunikaty („Katalog cen: <treść>”, „Ceny półkowe: …”, „Kolejka cen: …”).
   Treść pochodzi z `meta` (już bez tokenów); `MISSING_CONFIG` zostaje tak, jak jest.
2. `web/kupujemy.py` (strona „Co kupujemy”) i `web/lista.py` (`lista_page`) dokładają `sync_errors` do kontekstu;
   oba szablony renderują baner (użyj istniejącej klasy błędu/ostrzeżenia z `static/app.css`, `role="status"`),
   gdy lista niepusta; autoescape włączony (bez `|safe`).
3. Testy w `tests/test_web_kupujemy.py` i `tests/test_web_lista.py`: z ustawionym `meta.shelf_queue_error` strona
   zawiera komunikat, bez błędów banera brak, treść z `<script>` jest zescapowana.
DONE: testy zielone, pełny zestaw zielony, ruff/mypy czyste. NIE RÓB: `zakupy_client.py`, `sync.py`, `catalog_map.py`,
styli poza użyciem istniejących klas, commitów.

Rollback: `wt drop /config/addons/ha-dieta-app e6c2-W1|e6c2-W2`.
