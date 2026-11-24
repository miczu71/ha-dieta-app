# PLAN E9b — odporność i bezpieczeństwo (Dieta; Zakupy pkt 7 robi rodzic)

Źródło: `docs/ROADMAP_E9.md` sekcja E9b. Rollback: `git revert` commitów etapu (bez zmian schematu).
Podział: jedna paczka W1 (Dieta, pkt 1–6, `sonnet-worker`, „ryzyko”: sieć/tokeny) + pkt 7 (Zakupy, 1 edycja, robi rodzic z TDD) —
osobna paczka W2 nie ma ≥3 edycji, więc bez workera.

## Wykonawca W1 (Dieta, `sonnet-worker`, repo `/config/addons/ha-dieta-app`, src `dieta/app/src/dieta`, testy `dieta/app/tests`)
Testy: `cd <WORKTREE>/dieta/app && uv run pytest -q <dotknięte pliki testów> && uv run ruff check . && uv run mypy src`
(pełny pytest ~250 s — NIE uruchamiaj; rodzic zrobi po landzie). TDD: test czerwony → zmiana → zielony.

1. **Limit bajtów**: nowy `dieta/http_limit.py` z `read_limited(resp, max_bytes: int) -> bytes` (czyta `max_bytes+1`,
   przy przekroczeniu `raise ResponseTooLarge`, podklasa `ValueError`). Użyj w `zakupy_client.py` (`resp.read()` ~l.132 → limit 5_000_000;
   `_reason` `exc.read()` ~l.231 → limit 64_000), w `zepp.py` (`resp.read()` ~l.177 i `exc.read()` ~l.181 → limit 5_000_000).
   W `zakupy_client._call` przekroczenie → `ZakupyError("Odpowiedź Zakupów za duża.")`; w `zepp` → `ZeppError("odpowiedź za duża")`
   (zepp: `ValueError` jest już łapany w `except … ValueError` → sprawdź, że komunikat jest sensowny; dla gałęzi `exc.read()` łap też `ValueError`).
   `dictionary.py:196` (`json.loads(resp.read())`) → limit 1_000_000 (błąd łapany przez istniejący `except Exception`).
   Testy: odpowiedź > limitu → odpowiednio `ZakupyError`/`ZeppError`; `read_limited` granica (= max OK, max+1 błąd).
2. **Prompt LLM**: `catalog_map.py:126` — `ingredient` też przez `_prompt_safe`. `dictionary.py` ~l.185 (`"\n".join(f"{i}. {n}" …)`) —
   `n` przez `_prompt_safe` (przenieś `_prompt_safe` do wspólnego miejsca bez cyklu importów, np. `dieta/names.py`; `catalog_map` importuje stamtąd; zachowaj nazwę).
   Testy: nazwa ze znakami sterującymi/`||`/długa → bezpieczna linia w `ask`.
3. **Log HTTP**: `dictionary.py` ~l.199-201: osobny `except urllib.error.HTTPError as exc` → `_log.warning("LLM: błąd HTTP %s (%s)", exc.code, exc.reason)`
   (bez treści/nagłówków/tokenu; `reason` przez `str(exc.reason)[:80]`), reszta jak dotąd (`type(exc).__name__`). Test z `caplog`: kod w logu, brak tokenu.
4. **Przekierowania Zakupów**: `zakupy_client.py:73-80` zamiast `_NoRedirect` → `_SameHostRedirect` (wzór `fetcher.py SameHostRedirect`, bez robots):
   przekierowanie dozwolone tylko gdy host (`urlsplit(...).hostname`) i port celu = hosta źródła, schemat http/https (http→https OK);
   inaczej `return None` (HTTPError 3xx → istniejący komunikat). Nagłówek `Authorization` przy przekierowaniu na ten sam host zostaje.
   Testy: ten sam host http→https → idzie; inny host → `ZakupyError` o przekierowaniu i brak drugiego żądania.
5. **`web/templates/_sync_errors.html:1`**: `role="status"` → `role="alert"`. Test (istniejący test szukający `role="status"` w błędach popraw).
6. **Threadpool**: `web/app.py:~202-218` `index`: `build_card` i `zepp_service.view` w `await run_in_threadpool(...)`
   (`from starlette.concurrency import run_in_threadpool`, wzór `web/kupujemy.py`). Jedno wywołanie funkcji pomocniczej zwracającej `(cards, zviews)`.
   Test: istniejące testy `/` zielone (zachowanie bez zmian).

`DONE:` pkt 1–6 zaimplementowane z testami, dotknięte pliki testów + ruff + mypy czyste, raport z `git status --short` i listą plików.
`NIE RÓB:` commitów; zmian w `/config/addons/ha-lidl-app`; zmian schematu/store/migracji; zmian poza wymienionymi plikami
(poza nowym `http_limit.py` i ewent. `names.py`); odpalania pełnego pytest; dotykania `docs/`; zmiany limitu 1/h Zepp ani logiki purge z E9a.

## Pkt 7 (rodzic, Zakupy `/config/addons/ha-lidl-app`, TDD)
`lidl/app/src/lidl/catalog.py:15-18` `_ean`: 14-cyfrowy kod z wiodącym `0` → 13-cyfrowy EAN; inny 14-cyfrowy (i inny niepasujący numer `b:`) →
`None` + licznik pominiętych zwracany/logowany (`_log.info("katalog: pominięto %d kodów b: …")` bez treści). Test w `tests/test_catalog.py`.
Wydanie Zakupów 1.12.2 dopiero po E9c.
