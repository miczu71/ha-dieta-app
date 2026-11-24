# PLAN E6c.1 — Zakupy (ha-lidl-app): dług bezpieczeństwa i cichych błędów

Repo: `/config/addons/ha-lidl-app`, kod `lidl/app/src/lidl/`, testy `lidl/app/tests/`. Kontekst: `docs/PLAN_E6c.md`.

## Wykonawca (sonnet-worker, jedna paczka, „ryzyko”)
Test: `cd <WORKTREE>/lidl/app && uv run pytest -q && uv run ruff check . && uv run mypy src` (pierwszy plan, bez tła).
TDD: najpierw testy (czerwone), potem kod.

1. `frisco_feed.py` — `downloader.fetch`: stała `MAX_FEED_BYTES = 150 * 1024 * 1024`. Jeśli `resp.content_length`
   > limit → `ValueError("feed za duży")` przed zapisem; w pętli chunków licz sumę i przy przekroczeniu `ValueError`.
   Plik tmp sprząta istniejące `finally` w `refresh`.
2. `frisco_feed.py` — `refresh`: po `_load`, jeśli `products` puste → `raise ValueError("feed bez produktów")`
   (trafia do istniejącego `except`: dziennik `catalog`/`error`, zwrot `Result(error=…)`).
3. `web/app.py` — `api_shelf_request`: `raw_store` poprawny tylko gdy `None` albo `re.fullmatch(r"[\w-]{1,32}", s)`
   (kody sklepów to np. `0001`); inaczej istniejąca odpowiedź 400. Dodaj `import re`, jeśli brak.
4. `catalog.py` — `_ean`: `shelf_prices._EAN_RE.fullmatch(code)` zamiast `isdigit` i 8–14 (import `from . import shelf_prices`
   albo przenieś regex do `catalog.py` i niech `shelf_prices` go importuje — wybierz wariant bez cyklu importów).
5. Testy (w `tests/test_catalog.py`, `tests/test_shelf_prices.py` lub nowym `tests/test_e6c_hardening.py`):
   limit bajtów (Content-Length > limit oraz strumień > limit, atrapa sesji aiohttp), pusty feed → `Result.error`
   i wpis dziennika `error`, `POST /api/v1/shelf` ze `store` = `"a b"`/`"x"*40`/`"../x"` → 400, ze `store` = `"0001"` → nie 400,
   `_ean("b:12345678901234")` → `None`, `_ean("b:5900000000000")` → EAN.

DONE: nowe testy zielone, pełny zestaw `pytest` zielony, ruff i mypy czyste, `git status` zawiera tylko pliki z listy.
NIE RÓB: zmian schematu bazy, N+1/SQL `catalog_products`, wersji/CHANGELOG/`config.yaml`, commitów, pushy,
refaktorów poza punktami 1–5.

Rollback: `wt drop /config/addons/ha-lidl-app e6c1` albo `git revert` commitu.
