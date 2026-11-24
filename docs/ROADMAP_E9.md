# E9 — domknięcie Diety: kcal, formularz wagi, Zepp, dług techniczny (Dieta 0.6.1 + Zakupy 1.12.2)

## Kontekst
E0–E8 zrobione, Dieta 0.6.0 działa. Zostały: kumulująca się propozycja kcal, prośba o zwinięty formularz wagi,
stare pomiary Zepp po zmianie konta, ~16 punktów długu z E6c/E8 oraz sprawdzenia na żywo. User (2026-11-24): „zrób wszystko”.

Decyzje z wywiadu (2026-11-24):
- propozycja kcal liczona od `auto_kcal`, nie od bieżącego celu (bez migracji);
- formularz ręcznej wagi zwinięty u osoby z połączonym Zepp, rozwinięty bez Zepp albo bez żadnej wagi;
- przy jawnym odłączeniu konta / zmianie członka kasujemy `body_measurement` i `weight` ze `source='zepp'` tej osoby
  (NIE przy wygasłej sesji / statusie `reconnect` z błędu — to by gubiło dane); limit 1/h dla „Synchronizuj teraz” zostaje;
- cały dług w 3 etapach, jedno wydanie na koniec.

Kto korzysta: 2 osoby w panelu Dieta (HA ingress). Automatycznie: etapy E9a→E9c, wydanie, weryfikacja na żywo.
Za zgodą / przez usera: wylogowanie aplikacji Zepp w telefonie, daty urodzenia w UI, ocena cen z paragonów.
Sukces: propozycja kcal nie zmienia się po „Zastosuj” przy tym samym trendzie; testy zielone; 0.6.1/1.12.2 na żywo bez błędów w logach.

Repo: Dieta `/config/addons/ha-dieta-app` (src `dieta/app/src/dieta`), Zakupy `/config/addons/ha-lidl-app` (src `lidl/app/src/lidl`), gałąź `main`.
Pierwszy krok: ten plan → `docs/ROADMAP_E9.md` w repo Diety; przy starcie każdego etapu `docs/PLAN_E9<x>.md` z sekcją Wykonawca.

## E9a — zachowanie dla usera (Dieta, TDD, haiku-worker)
1. `targets.py:67 kcal_suggestion` + `web/app.py:119`: baza = `card.auto_kcal`; propozycja `None`, gdy równa bieżącemu `kcal`.
   Test: po „Zastosuj” (override = propozycja) ten sam trend → `None`.
2. `profiles.html:41-43`: formularz wagi w `<details class="dz-details">`, `open` gdy brak Zepp `linked` albo brak wagi.
   `Card` (`web/app.py:69-88`) dostaje pole `zepp_linked` (z `zepp_views`/store). Test renderu obu stanów.
3. Zepp: `Store.purge_zepp(person_id)` (DELETE `body_measurement` + `weight WHERE source='zepp'`) wołane w
   `zepp_service` przy jawnym odłączeniu (`:292`) i zmianie członka (`:219`, przed syncem). Testy: ręczne wagi zostają.
Rollback: `git revert` commitów etapu (bez migracji).

## E9b — odporność i bezpieczeństwo (Dieta + Zakupy, „ryzyko” → sonnet-worker)
1. Limit bajtów odpowiedzi: `zakupy_client.py:131,231`, `zepp.py:177,181` — wspólny `read_limited(resp, max)` → błąd przy przekroczeniu.
2. Prompt LLM: `_prompt_safe` także dla `ingredient` (`catalog_map.py:126`) i nazw w `dictionary.py:185`.
3. `dictionary.py:199-201`: log z kodem HTTP i powodem (bez treści/tokenu), `HTTPError` osobno od reszty.
4. `zakupy_client.py:73-80`: zamiast `_NoRedirect` przekierowanie tylko na ten sam host (http→https dozwolone, Bearer nie wychodzi poza host) — wzór `fetcher.py:57-70 SameHostRedirect`.
5. `_sync_errors.html:1`: `role="alert"` dla błędów (spójnie z resztą).
6. `web/app.py:202-218`: `build_card`/`zepp_service.view` w `run_in_threadpool` (wzór `kupujemy.py:202`).
7. Zakupy `catalog.py:15-42`: GTIN-14 z wiodącym `0` → EAN-13; inne 14-cyfrowe liczone i logowane (koniec cichego pomijania).
Rollback: revert; brak zmian schematu.

## E9c — testy i refaktory (Dieta + Zakupy, paczki W1 Dieta / W2 Zakupy, haiku-worker)
W1 Dieta: jeden helper `parse_iso_date` (np. `dieta/dates.py`) zamiast `shopping.py:198`, `pricing.py:215`, `lista.py:74`, `catalog.py:105`, `shelf_queue.py:77`;
`kupujemy.py:141-148 queue_size` przez COUNT w store; `test_shopping.py:254-255` zamrożona data; brakujące testy
(STORE_RE 32/33 w Zakupach → W2, `refresh()` z przekroczonym limitem, `sync_errors` dla 3 kluczy, `_prompt_safe` pusta nazwa i granica 120).
W2 Zakupy: wspólna pętla harmonogramu (`daily.py:52 at_time_loop` + `frisco_feed.py:180 weekly_loop` → jedna z funkcją „sekund do”);
Frisco bez N+1 (`frisco_feed.py:94-135`: jeden SELECT istniejących EAN + `executemany`); `search_view.py:37`/`chart.py:61` wspólny parser dat (`leaflet.py:124` zostaje — inny format);
`tests/test_catalog.py` przez publiczne API `History` zamiast `h._db`; testy granic `store` (32/33, pusty, nie-string), EAN 8 cyfr.
Bez zmian (sprawdzone, nie defekt): `cascade_prices` (jedna definicja), `force` przez HTTP (na sztywno w `/kupujemy/odswiez`),
`data_version` per proces — do potwierdzenia, że add-on ma jeden worker uvicorn; jeśli tak, tylko komentarz.
Rollback: revert paczki.

## E9-release (bez pytania)
simplify → code-review (+ silent-failure-hunter, pr-test-analyzer, security general-purpose; repo + `<baza>..HEAD`) → pubguard/claimcheck
→ release Dieta 0.6.1 i Zakupy 1.12.2 (non-draft) → weryfikacja na żywo: wersje w Supervisorze, logi po starcie, `/` 200, Profile (formularz zwinięty u osoby 1, propozycja kcal stabilna).

## E9-live — sprawdzenia na żywo (2026-11-25, tylko odczyt)
- półka Zakupów niepusta po nocy (`shelf_item` > 0, 429 w logach?);
- push 07:00 z nazwami osób (historia `notify`/logi);
- 10 podejrzanych cen (seler, pomidory, jajka, filet…) vs feed/paragony → tabela do `docs/POMIAR_ceny.md`, decyzję o poprawkach podejmuje user;
- po stronie usera: daty urodzenia w Profilach + nowy plan; czy aplikacja Zepp w telefonie została wylogowana.

## Weryfikacja (każdy etap)
`cd <repo>/<addon>/app && uv run pytest -q && uv run ruff check . && uv run mypy src`; verifier po każdej paczce workera;
progress_tracker `title: "E9 E<x>"`; po etapie `stage-end` (poza ostatnim).

## Stan
E9a: **ZROBIONE 2026-11-24** (niewydane). Propozycja kcal liczona od `auto_kcal` i znika, gdy równa bieżącemu celowi; formularz wagi w `<details data-weight-form>` (zwinięty tylko przy Zepp `linked` + znanej wadze, decyzja w szablonie, bez pola w `Card`);
`Store.purge_zepp` (kasuje zepp-owe `body_measurement`/`weight`, zeruje `last_sync_ts`) wołane przy `disconnect`, zmianie członka (inny `fuid`) i ponownym logowaniu na inne konto. Wykonane bez workera (mała zmiana, TDD). pytest 808+1 poprawiony test (`test_names`), ruff/mypy czyste.
Następny: E9b.
