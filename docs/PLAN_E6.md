# PLAN E6 — koszt planu i lista zakupów

Wywiad: 2026-11-23 (grill-me, potwierdzony „Go”). Etapy to commity; wydanie po `E6-release`: **Zakupy 1.11.0 + Dieta 0.3.0** (Zakupy muszą wyjść razem, bo bez nowych pól cen Dieta nie policzy kosztu). E7 (kcal zakupów) → 0.4.0 / Zakupy 1.12.0. Promocje i kupony → osobny etap E6b.

## Cel i miara sukcesu
- **Kto korzysta:** dwie osoby w panelu Diety. Ekran Plan pokazuje koszt tygodnia dla domu, koszt sesji gotowania i pokrycie cen w %. Nowy ekran „Lista” pokazuje listę zakupów z zatwierdzonego planu.
- **Automatycznie:** koszt z mediany ceny płaconej za kg/sztukę (nowe pola w `/api/v1/purchases`), scalanie składników ze wszystkich sesji, odjęcie tego, co prawdopodobnie jest w domu (`IngredientStat.at_home`), pominięcie soli/przypraw/wody (`is_free`/`is_noise`).
- **Za zgodą (przyciski):** „Wyślij do todo HA” (opcja `todo_entity`, puste = przycisk ukryty), „Dodaj jednak” przy pozycjach z sekcji „Pewnie masz”, odhaczanie w sklepie. Tryb „taniej” w opcjach, domyślnie wyłączony.
- **Sukces:** (a) pokrycie ceny ≥ 70% masy składników wygenerowanego planu na żywej bazie; (b) koszt tygodnia w sensownej relacji do realnych wydatków (kontrolnie: ±35% średniego tygodniowego wydatku na żywność z `purchase.spend`/52, raport, bez bramki); (c) testy, ruff, mypy zielone; verifier APPROVE.

## Decyzje z wywiadu (wiążące)
1. Dwa kroki cen: E6 = ceny z paragonów (mediana ceny płaconej za jednostkę); E6b = ceny półkowe i promocje (Zakupy 1.12.0). Pola dodane **addytywnie**; stary Dieta działa ze starym i nowym Zakupami, nowy Dieta ze starym Zakupami (brak cen → pokrycie 0%, komunikat).
2. Lista: ekran w Diecie + przycisk do todo HA. Nic nie leci automatycznie po zatwierdzeniu planu.
3. Lista: ilości z przepisów scalone (g, przy znanej masie sztuki „≈ N szt.”), przy znanym opakowaniu obok „≈ N op. po X g”. Koszt = masa × cena/100 g (nie zaokrąglamy do opakowań).
4. Brakujące = skład planu minus „prawdopodobnie w domu” (`at_home`). Odjęte widoczne w sekcji „Pewnie masz” z „Dodaj jednak”.
5. Tryb „taniej”: opcja `cheaper_mode` (domyślnie `false`). Gdy włączona, ocena przepisu dostaje premię `W_HOME × home_ratio` (udział masy składników „w domu”). Przy `false` wynik planera **bez zmian** (test determinizmu).
6. Koszt: dla domu (jedno gotowanie = jeden zakup), nie na osobę. Pokrycie % zawsze obok kwoty; brak ceny = pozycja w „Bez ceny”, nie 0 zł.
7. Zakres listy: plan zatwierdzony (szkic → komunikat). Bieżący tydzień: tylko sesje o `start_day ≥ dziś` (już ugotowane pomijamy); przyszłe tygodnie całe; minione puste.

## Formuły
- **Cena składnika** (`pricing.py`): dla każdego kodu zakupu zmapowanego na składnik: `price100` = `unit_price/10` gdy `is_weight` (cena za kg); gdy pakowany i w nazwie jest gramatura (`400g`, `0,5 kg`, `1 l`/`500ml` → g przy gęstości 1) → `unit_price / pack_g × 100`; gdy pakowany bez gramatury a składnik ma `piece_g` (z `ingredient_nutrition`) → `unit_price / piece_g × 100`; inaczej brak ceny dla kodu. Cena składnika = mediana `price100` po kodach, ważona liczbą zakupów (kod z większą liczbą zakupów liczy się częściej). Typowe opakowanie (`pack_g`) = mediana gramatur z nazw, gdy ≥1.
- **Skala sesji:** `scale = Σ(p1+p2 porcji sesji) / servings_przepisu` (porcji w `plan_meal`; `servings` NULL → `est.servings` z `estimate_recipe`, jak w E3c). Masa linii = `to_grams(...)` (albo `grams_assumed`) × scale.
- **Pokrycie** = masa linii z ceną / masa wszystkich linii z masą (bez `is_free`/`is_noise`); linie bez masy liczone osobno jako „ilość nieznana” (na liście bez ilości, bez kosztu).
- **Koszt sesji** = Σ masa × price100 / 100 po liniach z ceną; **koszt na porcję** = koszt sesji / Σ porcji.

## Model danych — migracja v7 (tylko dodaje)
```
ALTER TABLE purchase ADD COLUMN unit_price REAL;        -- mediana ceny płaconej za jednostkę (zł/kg gdy is_weight, inaczej zł/szt.)
ALTER TABLE purchase ADD COLUMN is_weight INTEGER;      -- 0/1; NULL = stary Zakupy
ALTER TABLE purchase ADD COLUMN last_unit_price REAL;
CREATE TABLE shopping_state (
    week_start TEXT NOT NULL,
    ingredient_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('force','checked','sent')),
    PRIMARY KEY (week_start, ingredient_id, state)
);
```
`force` = „Dodaj jednak” (pozycja „w domu” wraca na listę), `checked` = odhaczone w sklepie, `sent` = już wysłane do todo HA (nie wysyłamy drugi raz). `replace_purchases` używa dziś `INSERT INTO purchase VALUES (?×11)` → zmienić na kolumny nazwane. Rollback: kolumny/tabela zostają (stary kod ignoruje); `git revert` zakresu E6.

## Opcje HA
`todo_entity: ""` (schema `match(^(todo\.[a-z0-9_]+)?$)?`), `cheaper_mode: false` (schema `bool`). `Settings.todo_entity: str = ""`, `Settings.cheaper_mode: bool = False`. Tłumaczenia pl/en. Bump wersji dopiero w `E6-release`.

## Architektura
- **Zakupy** (`ha-lidl-app`): `History.purchase_aggregates` zwraca dodatkowo `unit_price`, `is_weight`, `last_unit_price` (płacone za jednostkę = `(total + discount) / quantity`, `quantity > 0`; dla `is_weight` quantity = kg — potwierdzić w kodzie i teście).
- **Dieta** `zakupy_client.py` (`Purchase` +3 pola opcjonalne z domyślnym `None`), `store.py` (migracja v7, `replace_purchases`, `purchases`, `shopping_states`, `set_shopping_state`, `clear_shopping_state`), nowe: `pricing.py` (czyste funkcje), `shopping.py` (lista + koszt, bez HTTP), `todo.py` (wysyłka), `web/lista.py` + `lista.html`, zmiany `web/plan.py`/`plan.html` (pasek kosztu).
- **Todo HA:** `POST http://supervisor/core/api/services/todo/add_item` z `SUPERVISOR_TOKEN` (wzorzec jak w `push.py`: stdlib `urllib`, bez nowej zależności; `push.py` nie ruszamy). Pozycja: `item` = „Pierś z indyka — 900 g”, `description` = „Dieta, tydzień od 2026-11-23”. Wysyłane tylko niewysłane (`sent`) i niezaznaczone `checked`.

## Etapy i kolejność
- **E6.0 (ja):** ten plan + ROADMAP + commit docs.
- **W0** (Zakupy, `sonnet-worker`, ryzyko: pieniądze/kontrakt API) ‖ **W1** (Dieta: dane, `pricing.py`, `shopping.py`, opcje; `sonnet-worker`, ryzyko: migracja) — rozłączne repozytoria, W1 buduje na kontrakcie z tego planu (fixtury ręczne). → verifier per paczka → **W2** (web + todo, `haiku-worker`) ‖ **W3** (planer „taniej”, `sonnet-worker`) — rozłączne pliki, obie po W1 → verifier per paczka → pełne testy → odczyt na żywo (pokrycie na kopii prawdziwej `dieta.db`) → ROADMAP + `stage-end`.
- Worktree: `wt new <repo> E6-W0` itd. (`wt land` sekwencyjnie). Wydanie: osobny krok `E6-release` (Zakupy 1.11.0 najpierw, potem Dieta 0.3.0; simplify → code-review → release → weryfikacja na żywo, token Zakupów już ustawiony).

## Wykonawca
Konwencje: skill `addon-conventions`; komentarze/docstringi po polsku, identyfikatory po angielsku; ścieżki względne (Ingress); Jinja autoescape; zero emoji w UI (ikony SVG). Testy Dieta: katalog `<WORKTREE>/dieta/app`: `uv run --extra dev pytest -q`, `uv run --extra dev ruff check src tests`, `uv run --extra dev ruff format --check src tests`, `uv run --extra dev mypy`. Testy Zakupy: katalog `<WORKTREE>/lidl/app`, te same polecenia (extra `dev`).

### W0 — Zakupy: ceny jednostkowe w `/api/v1/purchases` (`sonnet-worker`, ryzyko: pieniądze/kontrakt)
- Repo: `ha-lidl-app`. Pliki: `lidl/app/src/lidl/history.py` (`purchase_aggregates`), `lidl/app/tests/test_purchases_api.py` (i test historii, jeśli istnieje dla `purchase_aggregates`), `lidl/DOCS.md` lub `lidl/CHANGELOG.md` (jedna linia o nowych polach; **bez** bumpu wersji).
- Zmiana: do `SELECT` dodać `i.quantity`, `i.is_weight`; per produkt zbierać listę cen płaconych za jednostkę `(total + discount) / quantity` dla `quantity > 0` (dla `is_weight` quantity to kg — sprawdź w `history.py` i fixturach, jeśli inaczej, zatrzymaj się i zgłoś). Wynik: `unit_price` = mediana (zaokrąglona do 2 miejsc), `last_unit_price` = cena z ostatniego paragonu, `is_weight` = flaga ostatniego paragonu; `None`, gdy brak pozycji z `quantity > 0`. Istniejące pola i kolejność bez zmian.
- Testy (najpierw czerwone): produkt wagowy (kg) → cena za kg z rabatem; pakowany ×2 szt. → cena za sztukę; rabat obniża cenę; mediana odporna na jedną promocję; `quantity = 0` pomijane; stare pola niezmienione.
- DONE: pełny zestaw testów Zakupów zielony (wynik podaj), ruff/format/mypy czyste, `/api/v1/purchases` w teście zawiera 3 nowe pola.
- NIE RÓB: bumpu wersji w `config.yaml`, zmian w innych endpointach, zmian schematu bazy Zakupów, zmian w `ha-dieta-app`.

### W1 — Dieta: dane, ceny, lista, opcje (`sonnet-worker`, ryzyko: migracja)
- Pliki: `src/dieta/zakupy_client.py` (`Purchase.unit_price/is_weight/last_unit_price` opcjonalne, parsowanie `row.get(...)`, `is_weight` → `bool | None`), `src/dieta/store.py` (migracja v7 **na końcu** `_MIGRATIONS`; `replace_purchases` na kolumny nazwane; `purchases()` czyta nowe kolumny; `shopping_states(week_start) -> dict[str, set[int]]`, `set_shopping_state(week_start, ingredient_id, state)`, `clear_shopping_state(...)`), `src/dieta/settings.py` (`todo_entity`, `cheaper_mode`), `dieta/config.yaml`, `dieta/translations/pl.yaml`, `dieta/translations/en.yaml`, nowe `src/dieta/pricing.py`, `src/dieta/shopping.py`, testy: `tests/test_pricing.py`, `tests/test_shopping.py`, uzupełnienia `tests/test_zakupy_client.py`, `tests/test_store_zakupy.py`, `tests/test_settings.py`, `tests/test_config.py`; w istniejących testach dopuszczalna **wyłącznie** zmiana asercji wersji schematu 6→7.
- `pricing.py`: `parse_pack_g(names: Sequence[str]) -> float | None`; `price_per_100g(purchase, piece_g) -> float | None` i `ingredient_prices(purchases, mapping, piece_g_by_ingredient) -> dict[str, IngredientPrice]` wg „Formuły” (`IngredientPrice(price100, pack_g, codes)`), czyste funkcje.
- `shopping.py`: typy `frozen dataclass`: `ShoppingLine(ingredient_id, name, grams, pieces, pack_g, packs, cost, priced, at_home)`, `ShoppingList(week_start, notice, to_buy, at_home, unpriced, cost_total, coverage)`, `SessionCost(session_id, recipe_id, title, cost, per_portion, coverage)`, `PlanCost(total, coverage, sessions)`; funkcje `shopping_list(store, settings, week_start, today) -> ShoppingList` i `plan_cost(store, settings, week_start) -> PlanCost`. Notice: `ok`, `no_plan`, `not_approved`, `past_week`, `no_prices`. Reguły z „Decyzje” 3–4, 7; stany `force` przenosi pozycję z `at_home` do `to_buy`; `checked` zostaje na liście (znacznik, nie usunięcie). Masa linii: `estimate.to_grams` + `grams_assumed` (importuj, nie kopiuj), `is_free`/`is_noise` pomijane.
- Testy (najpierw czerwone): parsowanie gramatur (`400g`, `0,5 kg`, `1 l`, brak); cena wagowa vs pakowana vs po `piece_g` vs brak; skala sesji (2 osoby × 3 dni); scalenie tego samego składnika z 2 sesji; `at_home` w „Pewnie masz” i `force`; pokrycie % z linią bez ceny; `no_prices` przy samych `None`; migracja v7 na bazie v6 (dane zostają); `replace_purchases` ze starą odpowiedzią Zakupów (pola `None`).
- DONE: komendy testu zielone (pełny zestaw, wynik podaj), migracja v7 na kopii bazy v6 przechodzi, nowe opcje w `config.yaml` + oba tłumaczenia.
- NIE RÓB: `web/*`, `planner.py`, `plan_service.py`, `push.py`, `todo.py`, bumpu wersji, `docs/*`.

### W2 — Dieta: ekran Lista, pasek kosztu, wysyłka do todo (`haiku-worker`, po W1)
- Pliki: nowe `src/dieta/todo.py`, `src/dieta/web/lista.py`, `src/dieta/web/templates/lista.html`; zmiany `src/dieta/web/app.py` (rejestracja routera `make_lista_router(settings, store, templates)`), `web/templates/base.html` (zakładka „Lista” → `lista`), `src/dieta/web/plan.py` + `web/templates/plan.html` (pasek: koszt tygodnia, pokrycie %, koszt sesji; przy `no_prices` jedna linia „Brak cen z Zakupów”), `web/static/app.css` (tylko style nowych elementów, tokeny istniejące); testy: `tests/test_web_lista.py`, `tests/test_todo.py`, uzupełnienie `tests/test_web_plan.py`.
- `todo.py`: `send_items(settings, items: Sequence[tuple[str, str]], opener=None) -> TodoResult` (`ok`/`failed`/`skipped`), POST `todo/add_item` per pozycja, token z `SUPERVISOR_TOKEN` (brak = `skipped`, log jak w `push.py`), błąd HTTP → `failed` z nazwą usługi w logu, **bez tokenu w komunikatach**; po sukcesie zapis `sent` w `shopping_state`.
- `web/lista.py`: `GET /lista?tydzien=` (lista, sekcje „Do kupienia” / „Pewnie masz” / „Bez ceny”, koszt i pokrycie), `POST /lista/jednak`, `POST /lista/odhacz`, `POST /lista/todo` (redirect 303, ścieżki względne; przycisk todo tylko przy niepustym `todo_entity`).
- Testy: renderowanie z danymi demo, każdy POST, brak `todo_entity` → brak przycisku, `todo` z fałszywym openerem (wysłane tylko niewysłane i nieodhaczone), notice dla szkicu.
- DONE: pełne testy + ruff/format/mypy zielone, HTTP 200 dla `/lista` i `/plan` na danych demo.
- NIE RÓB: `planner.py`, `plan_service.py`, `store.py`, `shopping.py`, `pricing.py`, `push.py`, opcji/`config.yaml`.

### W3 — Dieta: tryb „taniej” w planerze (`sonnet-worker`, po W1)
- Pliki: `src/dieta/planner.py` (`Candidate.home_ratio: float = 0.0`, `PlanConfig.cheaper: bool = False`, `W_HOME = 1.5`, składnik w `_score` tylko gdy `config.cheaper`), `src/dieta/plan_service.py` (`_candidate` wylicza `home_ratio` = udział masy linii składników `at_home` z `IngredientStat`, przy braku masy — udział liczby linii; `plan_config` przekazuje `settings.cheaper_mode`), testy: uzupełnienia `tests/test_planner.py`, `tests/test_plan_service.py`.
- Testy (najpierw czerwone): `cheaper=False` → wynik identyczny jak dotąd (ten sam plan dla tego samego ziarna); `cheaper=True` → przy dwóch równych przepisach wygrywa ten z większym `home_ratio`; `home_ratio` liczone poprawnie; wymiana (`swap_*`) też uwzględnia premię.
- DONE: pełne testy + ruff/format/mypy zielone; istniejące testy planera bez zmian asercji.
- NIE RÓB: `web/*`, `store.py`, `settings.py`, `config.yaml`, zmiany wag istniejących składników oceny, `P_REPEAT` i innych długów.

## Weryfikacja końcowa etapu
1. Pełne testy, ruff, format, mypy (Dieta i Zakupy). 2. Kopia żywej `dieta.db` (tylko kopia, profile syntetyczne): migracja v7, `plan_cost` i `shopping_list` dla wygenerowanego tygodnia, pokrycie % (kontrakt sukcesu a) — bez Zakupów 1.11.0 na żywo pokrycie będzie 0%, więc pomiar na żywo dopiero w `E6-release` po wydaniu Zakupów; w etapie sprawdzamy na fixturach cen). 3. Playwright 390/1280 px niewykonalny z kontenera (ingress) — raport jak w E4/E5.

## Ryzyka
- Gramatura pakowanych tylko z nazwy → część składników bez ceny; pokrycie % to uczciwie pokazuje. Cena wagowa w Lidlu: potwierdzić jednostkę (`quantity` w kg) w teście W0.
- Mediana płaconej ceny miesza promocje i regularne; E6b doda ceny półkowe.
- Wysyłka do todo jest zdarzeniem zewnętrznym (nie cofamy pozycji w HA) → tylko za kliknięciem, nigdy automatycznie.
