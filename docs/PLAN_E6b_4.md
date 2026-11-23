# E6b.4 — UI cen: plakietki źródła i „≈”, podział pokrycia, „—”, komunikaty todo „X z Y”, kolejka `pending_catalog` (bez wydania)

Źródło prawdy: `ROADMAP.md` (E6b), `PLAN_E6b.md`. Wartość etapu: user widzi, SKĄD jest cena (paragon / Biedronka / promocja / szacunek Frisco),
ile kosztu to szacunek, i może zatwierdzić mapowania EAN, które automat zostawił „wątpliwe”. Bez migracji, bez zmian `pricing.py`.
Decyzja projektowa bez wariantów: zmiany są małe i mieszczą się w istniejących komponentach `.dz-chip`/`.dz-chip--plain`/`.dz-pending`
(DESIGN.md), więc `prototype` pomijam; styl tylko tokenami, bez emoji i bez literałów kolorów.

## Zakres
1. **`shopping.py`:** `ShoppingLine` dostaje `source: str | None`, `estimate: bool`, `as_of: date | None`, `promo_to: str | None` (z `IngredientPrice`; domyślnie `None/False`).
   `ShoppingList` i `PlanCost` dostają `coverage_by_source: dict[str, float]` = udział masy (linie z masą, jak `coverage`) per źródło
   (`receipt`, `receipt_old`, `shelf`, `promo`, `frisco`); suma = `coverage`. Istniejące pola bez zmian.
2. **Etykiety (jedno miejsce, `web/lista.py`):** `SOURCE_LABEL = {receipt: "paragon", receipt_old: "stary paragon", shelf: "Biedronka", promo: "promocja", frisco: "szacunek"}`.
   Wiersz listy: plakietka `.dz-chip--plain` z etykietą (dla `promo` z „do <promo_to>”), koszt estymowany z prefiksem „≈ ”, brak ceny → „—” (zamiast „bez ceny”).
3. **Pasek kosztu (`lista.html`, `plan.html`):** pod „pokrycie cen N%” linia „w tym: paragony 40% · Biedronka 20% · szacunek 15%” (tylko niezerowe źródła; `receipt`+`receipt_old` razem jako „paragony”).
   `plan.html`: chip sesji z `coverage == 0` pokazuje „—” zamiast „0,00 zł”.
4. **Komunikaty todo:** `send_todo` zlicza wysłane pozycje; notice `todo_ok-<X>-<Y>` / `todo_partial-<X>-<Y>` („Wysłano X z Y pozycji do listy todo.” / „Wysłano X z Y pozycji, reszta się nie udała — szczegóły w logu.”).
   Stare kody (`todo_ok`, `todo_failed`, `todo_skipped`, `todo_empty`) zostają.
5. **Kolejka `pending_catalog` (`web/kupujemy.py`, `zatwierdz.html`):** `catalog_queue(store)` = `store.pending_catalog()`; wliczona do `queue_size`;
   sekcja w `zatwierdz.html` (karta `.dz-pending`: nazwa produktu, EAN małą czcionką, pole „Składnik” z kandydatem, przyciski „Zatwierdź” / „To nie ten składnik”);
   `POST /kupujemy/zatwierdz-katalog` (`ean`, `ingredient`, `action=approve|exclude`) → `store.approve_catalog` / `store.exclude_catalog`; walidacja: EAN musi być w kolejce, nazwa składnika niepusta ≤80 znaków (inaczej `error=dane`, jak reszta ekranu).
6. `DOCS.md` / `CHANGELOG.md` / `DESIGN.md` (sekcja „E6b.4”): wpis „niewydane”.

## Wykonawca
Dwie paczki o rozłącznych plikach, obie `haiku-worker`; bez „ryzyka” (UI, brak pieniędzy w logice — `cost` liczony jak dotąd).
Testy pełne uruchamia rodzic sekwencyjnie po obu (`cd dieta/app && uv run --extra dev pytest -q`, ~6 min), `ruff check .`, `ruff format --check .`, `mypy`.

### W1 — koszt i lista (pliki: `shopping.py`, `web/lista.py`, `web/templates/lista.html`, `web/templates/plan.html`, `web/static/app.css`, `tests/test_shopping.py`, `tests/test_web_lista.py`, `tests/test_web_plan.py`)
- Zmiany: pkt 1–4 powyżej. CSS tylko jeśli potrzebny odstęp dla linii „w tym:” (istniejące tokeny `--space-*`, `--muted`).
- Komenda testu: `cd <WORKTREE>/dieta/app && uv run --extra dev pytest -q tests/test_shopping.py tests/test_web_lista.py tests/test_web_plan.py`.
- Seamy testów: `build_context` na `Store` w `tmp_path` z wstawionymi wierszami cen (jak istniejące testy `test_shopping.py`); `TestClient` jak w `test_web_lista.py`; brak sieci.
- DONE: `ShoppingLine.source/estimate/as_of/promo_to` wypełnione z kaskady (test na liniach z paragonu, Biedronki, promocji i Frisco); `coverage_by_source` sumuje się do `coverage` (±1e-9) i zawiera tylko źródła z masą;
  HTML listy: plakietka źródła w każdym wierszu z ceną, „≈ ” przed kosztem szacunku, „—” w wierszach bez ceny, „do <data>” przy promocji; pasek z linią „w tym:”; plan: chip sesji z zerowym pokryciem to „—”;
  komunikaty: `todo_ok-3-3` → „Wysłano 3 z 3”, `todo_partial-2-3` → „Wysłano 2 z 3” (test POST `/lista/todo` z atrapą `send_items` dającą częściową porażkę po 2 z 3); istniejące testy zielone.
- NIE RÓB: nie zmieniaj `pricing.py`, `store.py`, `web/kupujemy.py`, `zatwierdz.html`, `config.yaml`; nie zmieniaj liczenia `cost`/`coverage`; bez emoji i literałów kolorów; bez commitu/pusha; bez `pkill`; bez sieci/LLM.

### W2 — kolejka katalogu (pliki: `web/kupujemy.py`, `web/templates/zatwierdz.html`, `tests/test_web_kupujemy.py`)
- Zmiany: pkt 5 powyżej. `queue_size` zwiększa się o `len(catalog_queue)` (chip w `.dz-nav` korzysta z niego bez zmian); `review` przekazuje `catalog_queue` do szablonu; warunek „Nic do zatwierdzenia” uwzględnia nową kolejkę.
- Komenda testu: `cd <WORKTREE>/dieta/app && uv run --extra dev pytest -q tests/test_web_kupujemy.py`.
- Seamy testów: `Store` w `tmp_path`, `catalog_item` + `catalog_map(status='pending')` przez `store.set_catalog_map` i istniejące upserty katalogu (znajdź w testach `test_catalog_map.py`), `TestClient`.
- DONE: GET `/kupujemy/zatwierdz` pokazuje kartę dla wpisu `pending` (nazwa, EAN, kandydat w polu); POST approve → `catalog_map.status == "approved"` z nazwą składnika i wpis znika z kolejki; POST exclude → `excluded`; EAN spoza kolejki albo pusty składnik → przekierowanie z `error=dane`/„pozycji nie ma już w kolejce” bez zmian w bazie; `queue_size` liczy wpisy katalogu; istniejące testy zielone.
- NIE RÓB: nie zmieniaj `store.py`, `catalog_map.py`, `shopping.py`, `lista.*`, `plan.*`, `app.css`; bez commitu/pusha; bez `pkill`; bez sieci/LLM.

## Kolejność i weryfikacja
1. `wt new` (jedno worktree, paczki W1 i W2 równolegle, pliki rozłączne) → po obu: pełny pytest/ruff/mypy w worktree → `verifier` (kontrakt, raport, BASELINE, ścieżka) → `wt land` → commit → ROADMAP → `stage-end`.
2. Test wizualny na 390 px (Playwright, dane przykładowe) wchodzi do E6b-release (ekran „Lista” z plakietkami).

## Rollback
`git revert` commitu etapu; brak migracji i zmian danych.

## Ryzyka
`coverage_by_source` musi używać tej samej definicji masy co `coverage` (linie z masą) — stąd test sumy; kody notice w URL (`todo_ok-3-3`) parsowane ściśle (`\d+`), inne wartości ignorowane.
