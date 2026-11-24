# E6c — domknięcie E6b + dług bezpieczeństwa i cichych błędów (Zakupy 1.12.1 + Dieta 0.4.1)

## Context
Dieta 0.4.0 i Zakupy 1.12.0 wydane 2026-11-23 (E6b). Z E6b-release zostały: niewykonany pomiar pokrycia cen
(cel ≥70% masy), widok „Lista” 390 px, sprawdzenie nocnej kolejki Biedronki (było 429) oraz dług z review
(`ha-dieta-app/docs/ROADMAP.md:395-397`). Decyzje usera (2026-11-24): domknięcie + dług **w jednym wydaniu**,
**oba repo**, zakres **bezpieczeństwo + ciche błędy** (refaktory jakościowe zostają w długu), plan tygodnia
**zatwierdza user w UI**.

Ustalone już: `/zatwierdz` 404 to nie błąd — trasa to `POST /plan/zatwierdz` (`dieta/app/src/dieta/web/plan.py:105`).

- Kto korzysta: domownicy (Lista/koszt planu), Dieta jako klient API Zakupów.
- Automatycznie: kod, testy, verifier, commity, wydania Zakupy→Dieta, weryfikacja na żywo (odczyty).
- Za zgodą/ręką usera: wygenerowanie i zatwierdzenie planu tygodnia w UI Diety.
- Sukces: wszystkie zmiany z testami (TDD), oba wydania non-draft zainstalowane, logi czyste; po zatwierdzeniu
  planu: pomiar pokrycia z liczbą, zrzut Lista 390 px, kolejka EAN bez `shelf_queue_error`.

## Etapy (wpis do `ha-dieta-app/docs/ROADMAP.md` + `docs/PLAN_E6c_<n>.md` z sekcją Wykonawca)

### E6c.1 — Zakupy (`/config/addons/ha-lidl-app`), paczka „ryzyko” (bezpieczeństwo) → `sonnet-worker`
1. `lidl/app/src/lidl/frisco_feed.py` `downloader`: limit bajtów pobierania (stała `MAX_FEED_BYTES`, np. 150 MB;
   sprawdzenie `Content-Length` i sumy chunków → `ValueError`, plik tmp usuwany przez istniejące `finally`).
2. `frisco_feed.refresh`: feed z 0 produktów → błąd w dzienniku (`catalog`/`error`), nie „ok”.
3. `web/app.py` `POST /api/v1/shelf`: walidacja `store` (regex na format ID sklepu Biedronki — sprawdzić format
   w `get_setting("biedronka_store")`/`chains.py`), zły → 400.
4. `catalog.py` `_ean`: 8–13 cyfr, wspólny `shelf_prices._EAN_RE` zamiast własnego warunku.
5. Testy: limit bajtów (Content-Length i strumień), pusty feed, zły `store` → 400, EAN 14 cyfr odrzucony.
- Test: `cd lidl/app && uv run pytest -q && uv run ruff check && uv run mypy src`.
- NIE RÓB: zmian schematu bazy, refaktorów N+1/SQL catalog_products, zmian wersji/CHANGELOG.
- Rollback: `git revert` commitu etapu.

### E6c.2 — Dieta (`/config/addons/ha-dieta-app`), dwie paczki rozłączne → `haiku-worker` (W1 bezpieczeństwo → `sonnet-worker`)
- **W1 (klient/sync, ryzyko):**
  1. `dieta/app/src/dieta/zakupy_client.py` `_open`: opener bez podążania za przekierowaniem (własny
     `HTTPRedirectHandler` zwracający błąd → `ZakupyError`), żeby Bearer nie szedł pod inny host.
  2. `sync.py` `sync_catalog`: pusta odpowiedź katalogu → `catalog_sync_error` = „pusty katalog”, bez
     nadpisywania `catalog_sync_at` (poprzedni snapshot zostaje).
  3. `catalog_map.py` `map_catalog`: nazwy z feedu do promptu LLM — usunięcie znaków sterujących, limit długości,
     podanie jako dane (lista JSON), nie tekst instrukcji.
  4. Testy każdego punktu (opener z fałszywym 302, pusty katalog, nazwa z „\n\nIgnore…” obcięta/escapowana).
- **W2 (UI):** `web/kupujemy.py`, `web/lista.py` (+ szablony w `web/templates/`): baner błędów
  `catalog_sync_error`, `shelf_sync_error`, `shelf_queue_error` (wzorzec z istniejącego `kupujemy.py:207`),
  test renderowania bannera; styl wg `DESIGN.md`.
- Test: `cd dieta/app && uv run pytest -q && uv run ruff check && uv run mypy src`; Playwright 390/1280 px.
- NIE RÓB: refaktorów z listy długu (parse_iso_date, cascade_prices, pętle, queue_size, data_version, force w HTTP).
- Po każdej paczce: `claimcheck` → `verifier` (BASELINE `git status --short`), REJECT → raz ten sam tier.

### E6c-release (ostatni etap, bez pytania)
`simplify` → `code-review` ‖ `silent-failure-hunter` ‖ `pr-test-analyzer` ‖ security (general-purpose, diff
`<baza>..HEAD` obu repo) → `release` Zakupy **1.12.1**, potem Dieta **0.4.1** (CHANGELOG, bump wersji w `config.yaml`)
→ weryfikacja na żywo: logi obu add-onów, strony Diety 200, baner bez błędów albo z prawdziwym komunikatem.

### E6c.3 — domknięcie E6b (po zatwierdzeniu planu przez usera w UI; może iść przed/po wydaniu)
1. Pomiar pokrycia cen na zatwierdzonym planie (udział masy z ceną, podział wg źródła) → `docs/POMIAR_ceny.md`.
2. Playwright: widok „Lista” 390 px jasny + 1280 px ciemny, bez przewijania poziomego.
3. Nocna kolejka EAN: `meta.shelf_queue_error` pusty, półka Zakupów niepusta (`GET /api/v1/shelf`).
4. Wpis w ROADMAP; pozostały dług (refaktory) zostaje jako lista na później.

## Weryfikacja
- Testy, ruff i mypy w obu repo; CI zielone po pushu.
- Na żywo: `ha_get_app(source="installed")` → wersje 1.12.1/0.4.1, logi bez wyjątków, `POST /api/v1/shelf`
  ze złym `store` → 400 (przez sieć Supervisora z tokenem przez `! secret-run`, bez wklejania tokenu).
- Pamięć: zaktualizować `project_dieta_addon.md` (stan po 0.4.0/0.4.1 — notatka kończy się na E3).
