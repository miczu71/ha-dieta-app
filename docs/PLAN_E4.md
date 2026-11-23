# PLAN E4 — planer tygodnia (sesje gotowania, wymiana, zatwierdzenie)

Wywiad: 2026-11-23 (grill-me). Wydanie: brak (0.2.0 po E5). Commity etapów na `main`.

## Cel i miara sukcesu
- **Kto korzysta:** dwie osoby; plan tygodnia pon–niedz, wspólne dania, porcje skalowane do celu kcal każdej osoby (0,6–1,8 porcji).
- **Automatycznie:** propozycja tygodnia z 2–3 sesji gotowania na slot; sloty i udziały z opcji; pula = kcal ze źródła albo szacunek `auto`/`approved`; znajome (≥ `familiar_threshold`) mają pierwszeństwo.
- **Za zgodą:** „Zatwierdź” zamraża plan w `/data` (bez Cookidoo, bez kalendarza HA); „Wygeneruj od nowa” nie nadpisuje zatwierdzonego tygodnia bez potwierdzenia; przepisy `pending` poza planerem do czasu zatwierdzenia w kolejce.
- **Sukces:** testy algorytmu na zamrożonym katalogu (kcal dnia ±10%, średnia tygodnia ±5%, brak powtórek w 14 dni, brak odrzuconych, determinizm przy tym samym ziarnie); plan na żywo z realnego katalogu bez błędów i z ostrzeżeniem tylko przy realnym braku puli.

## Pomiar na starcie E4 (2026-11-23, na żywo, odczyt `dieta.db` w kontenerze)
Katalog 791, pula z kcal 693. Znajome w puli: ≥50% = **98** (obiad 22, kolacja/danie wieczorne 20, śniadanie 23, przekąski 14, desery 19; Respo 39, Lidl 41, Aniagotuje 14, Policzona 4), ≥60% = 36, ≥70% = 14. Wszystkie 98 mają `storage_days`. Pula całkowita: lunch 234, dinner 171, breakfast 99. Wniosek: na obiad+kolację jest ~42 znajomych przepisów — wystarcza na 2–3 sesje tygodniowo, ale dla suwaka „odkrywanie” i różnorodności algorytm musi umieć obniżyć próg znajomości (z ostrzeżeniem) zamiast stawać. Braki: `minutes` NULL w 43 przepisach puli (traktujemy jako „nieznany czas”, nie wykluczamy).

## Decyzje z wywiadu (wiążące)
1. Sesje wielodniowe od razu (przepis × N porcji rozdzielany na kolejne dni; N ≤ min(`storage_days`, `session_days_max`)).
2. Tydzień pon–niedz; sesja może przelać się na następny tydzień tylko jako informacja (nie zapisujemy porcji w nieistniejącym planie — sesja kończy się w niedzielę).
3. Sloty (opcje HA): śniadanie 25%, obiad 35%, kolacja 30% (II śniadanie i podwieczorek 0% = wyłączone); reszta do 100% = rezerwa na posiłki spoza planu. Udział 0 = slot wyłączony; suma > 100 → odrzucona konfiguracja (domyślne).
4. Mapowanie kategorii katalogu → slot: śniadanie ← `breakfast`; obiad ← `lunch`,`dinner`; kolacja ← `dinner`,`lunch` (kolacja woli `dinner`, obiad woli `lunch`: premia, nie twardy filtr); II śniadanie/podwieczorek ← `snack`,`dessert`.
5. Wspólna potrawa, porcje per osoba = (cel kcal osoby × udział slotu) / kcal na porcję; clamp 0,6–1,8; poza zakresem → kara w wyniku, a gdy nie da się utrzymać ±10% dnia: dobór innego przepisu.
6. Twarde: odrzucone (`dismissed`) nie wracają; ten sam przepis najwyżej raz na 14 dni (historia z zatwierdzonych planów + bieżący); kcal dnia ±10% na osobę, średnia tygodnia ±5%. Miękkie (kara): białko poniżej celu z profilu, tłuszcz/węglowodany poza szerokim pasem (±40% celu makro), max 2 sesje z tym samym głównym składnikiem w tygodniu (pierwszy niepodstawowy składnik przepisu), czas > `max_minutes_weekday` w dni robocze.
7. Dni gotowania (opcja `cook_days`, domyślnie `sat,sun,wed`): sesja zaczyna się tylko w dzień gotowania; `max_minutes_weekday` (domyślnie 45) dotyczy sesji zaczynających się w dni robocze.
8. Suwak odkrywania (`discovery`, 0–3, domyślnie 1): liczba sesji w tygodniu wybieranych spośród niekupowanych (znajomość < progu); reszta tylko ze znajomych; polubione ×premia.
9. Wymiana (🎲/♥): losowy znajomy o kcal ±15% (na porcję, w slocie), bez powtórki w tygodniu; ♥ z polubionych; wymiana **całej sesji** albo **jednego dnia**; przy wymianie dnia porcja zwolniona z sesji przechodzi na pierwszy wolny dzień tego samego slotu bezpośrednio po sesji, jeśli sesja nie przekroczy `storage_days` i niedzieli — inaczej jest porzucona z komunikatem; „Cofnij” = jeden krok.
10. Zatwierdzenie = zamrożenie planu (`status='approved'`); wymiana po zatwierdzeniu nadal możliwa (przelicza bilans), regeneracja całego tygodnia tylko po potwierdzeniu.
11. Trwałość: `storage_days`/`freezable` już są w katalogu (E3, reguły + AI); przepisy bez `storage_days` (2 w puli) → sesja 1-dniowa.

## Opcje HA (dodać do `config.yaml`, `translations/pl.yaml|en.yaml`, `Settings`)
`slot_breakfast_pct: 25`, `slot_lunch_pct: 35`, `slot_dinner_pct: 30`, `slot_snack1_pct: 0`, `slot_snack2_pct: 0` (int 0–100), `cook_days` (lista `mon..sun`, domyślnie `sat,sun,wed`), `max_minutes_weekday: 45` (int 10–240), `session_days_max: 4` (int 1–7), `discovery: 1` (int 0–3). Walidacja w `Settings` jak `_bounded`; zła suma slotów (>100) → wartości domyślne + WARNING.

## Model danych — migracja v5 (tylko dodaje; stary kod ją ignoruje; rollback = nowa wersja nie jest potrzebna, tabele można zostawić)
```
plan(id INTEGER PRIMARY KEY, week_start TEXT NOT NULL UNIQUE,            -- poniedziałek ISO
     status TEXT NOT NULL CHECK(status IN ('draft','approved')),
     seed INTEGER NOT NULL, params_json TEXT NOT NULL,                  -- migawka opcji + celów, do odtworzenia
     created_at TEXT NOT NULL, approved_at TEXT)
plan_session(id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
     slot TEXT NOT NULL CHECK(slot IN ('breakfast','lunch','dinner','snack1','snack2')),
     recipe_id INTEGER NOT NULL REFERENCES recipe(id),
     start_day INTEGER NOT NULL CHECK(start_day BETWEEN 0 AND 6), days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 7))
plan_meal(plan_id INTEGER NOT NULL REFERENCES plan(id) ON DELETE CASCADE,
     day INTEGER NOT NULL CHECK(day BETWEEN 0 AND 6), slot TEXT NOT NULL,
     session_id INTEGER NOT NULL REFERENCES plan_session(id) ON DELETE CASCADE,
     p1_portions REAL, p2_portions REAL, PRIMARY KEY (plan_id, day, slot))
plan_undo(plan_id INTEGER PRIMARY KEY REFERENCES plan(id) ON DELETE CASCADE, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL)
```
(Kolumny stanu „zjedzone/pominięte” dochodzą w E5 osobną migracją.)

## Architektura
- `planner.py` — **czysty** algorytm (bez store/sieci/zegara; ziarno `random.Random(seed)`): `generate(pool, targets, config, history, seed) -> PlanResult`, `swap_session(...)`, `swap_day(...)`, `evaluate(plan, ...) -> DayBalance[]` (kcal/makro na osobę i dzień, flagi tolerancji). Typy: `Candidate` (id, title, slot_affinity, kcal, protein, carbs, fat per porcję, minutes, storage_days, familiarity ratio, liked, main_ingredient), `Targets` (per osoba: kcal, białko g, fat g, carb g; osoba może być None), `PlanConfig`, `PlanResult` (sessions, meals, warnings).
- Algorytm: dla slotu rozkłada dni tygodnia na bloki (start w dzień gotowania, długość ≤ min(storage_days, session_days_max, dni do niedzieli)); wybór zachłanny po wyniku `score = w_fam·znajomość + w_like·polubione + fit_porcji − kary`; `discovery` sesji z puli niekupowanych; lokalna poprawa: jeśli któryś dzień poza ±10%, próbuje wymiany jednej sesji na kandydata poprawiającego sumę kar (max 3 przebiegi); brak kandydatów → obniża próg znajomości o 10 pp aż do 0 z ostrzeżeniem w `warnings`.
- `targets.py` — wyciągnięty z `web/app.py:build_card` kalkulator celów (`person_targets(store, settings, person_id) -> PersonTarget | None`); `build_card` korzysta z niego (zachowanie bez zmian).
- `plan_service.py` — klej: buduje pulę ze `Store.recipes` (filtry: `kcal_source='source'` lub `est_status in ('auto','approved')`, bez `dismissed`), historię z zatwierdzonych planów, wywołuje `planner`, zapisuje/odczytuje plan, undo (snapshot przed każdą wymianą/generacją), zatwierdzenie.
- `web/plan.py` + `templates/plan.html` — ekran „Plan” (nowa zakładka w `base.html`), styl Pojemniki: siatka dni × slotów, sesja jako rząd pojemników z wieczkiem w kolorze dnia, porcje „1,0 / 1,4”, bilans dnia na osobę z flagą poza ±10%, przyciski: Generuj / Zatwierdź / 🎲 / ♥ / wymień dzień / wymień sesję / Cofnij, ostrzeżenia, nawigacja tygodni. Mobilnie (390 px) lista dni zamiast siatki.

## Etapy i kolejność
- **E4.0 (ja):** ten plan + ROADMAP + commit docs.
- **W1 ‖ W2** (rozłączne pliki, bez wzajemnych zależności) → **W3** (po W1+W2) → verifier per paczka → Playwright/HTTP na danych demo → ROADMAP + `stage-end`.
- Wydanie: brak w E4 (0.2.0 po E5, wg ROADMAP); **ryzykowne** tylko W2 (migracja) → `sonnet-worker`; W1 (algorytm, niejasne przypadki brzegowe) → `sonnet-worker`; W3 → `haiku-worker` gotowe specyfikacje UI, a po REJECT `sonnet-worker`.

## Wykonawca

### W1 — algorytm planera (`sonnet-worker`, ryzyko: niejasny algorytm)
- Pliki: nowy `dieta/app/src/dieta/planner.py`, nowy `dieta/app/tests/test_planner.py` (+ fixtura `tests/fixtures/plan_pool.json`, ~40 kandydatów w 5 slotach, ręcznie spreparowana, bez danych osobowych). Bez importu `store`, `web`, sieci; tylko stdlib + `dieta.match`/typy własne.
- API i typy jak w sekcji Architektura (`generate`, `swap_session`, `swap_day`, `evaluate`); parametry z sekcji „Decyzje” 1–9 jako stałe/pola `PlanConfig` (wagi w jednym miejscu: `W_FAMILIARITY=3.0`, `W_LIKED=1.5`, `W_PORTION_FIT=2.0`, kary `P_REPEAT=∞ (twarde)`, `P_MAIN_DUP=1.0`, `P_MACRO=1.0`, `P_TIME=1.0`).
- Testy (zamrożona pula, `seed=1`): (a) kcal dnia ±10% dla obu osób przy celach np. 1900 i 2600; (b) średnia tygodnia ±5%; (c) brak powtórki przepisu w 14 dni z historią; (d) brak odrzuconych; (e) determinizm (ten sam wynik dla tego samego ziarna, inny dla innego); (f) `discovery=0` → tylko znajome, `discovery=2` → dokładnie 2 sesje niekupowane; (g) sesja zaczyna się tylko w dni gotowania, długość ≤ `storage_days` i ≤ niedziela; (h) wyłączony slot (0%) nie generuje posiłków; (i) jedna osoba (druga `None`) działa; (j) pusta pula slotu → ostrzeżenie, nie wyjątek; mała pula → obniżenie progu z ostrzeżeniem; (k) `swap_session` losowy: kcal ±15%, brak powtórki w tygodniu; ♥ tylko z polubionych (brak polubionych → komunikat, plan bez zmian); (l) `swap_day` przenosi zwolnioną porcję zgodnie z decyzją 9 albo ją porzuca z komunikatem; (m) porcje w 0,6–1,8; (n) białko poniżej celu daje karę (kandydat z większą ilością białka wygrywa przy równej reszcie).
- Test: `cd dieta/app && uv run pytest tests/test_planner.py && uv run ruff check src tests && uv run mypy src`.
- DONE: wszystkie testy (a)–(n) zielone, ruff/mypy czyste, brak importów `store`/`web`.
- NIE RÓB: `store.py`, `web/`, `settings.py`, `config.yaml`, migracji, sieci, LLM, zmian istniejących testów.

### W2 — opcje, cele, migracja v5, metody store (`sonnet-worker`, ryzyko: migracja)
- Pliki: `settings.py` (nowe pola + walidacja wg „Opcje HA”), `dieta/config.yaml` (`options`, `schema`: `cook_days` jako `list(mon|tue|wed|thu|fri|sat|sun)`), `translations/pl.yaml`, `translations/en.yaml`, nowy `targets.py` + `web/app.py` (`build_card` korzysta z `person_targets`; zachowanie identyczne), `store.py` (migracja v5 **na końcu** `_MIGRATIONS`, istniejących nie ruszamy; metody: `save_plan(week_start, status, seed, params_json, sessions, meals)`, `plan(week_start)`, `plan_sessions/meals`, `replace_plan_contents(plan_id, sessions, meals)`, `set_plan_status(plan_id, status)`, `save_undo(plan_id, snapshot_json)`, `pop_undo(plan_id)`, `approved_recipe_history(since: date) -> dict[int, date]`), testy: `tests/test_settings.py` (nowe opcje, suma slotów >100 → domyślne, zły dzień w `cook_days` pominięty), `tests/test_plan_store.py` (migracja z bazy v4 z wierszami, CRUD, kaskada usunięcia planu, undo, historia), `tests/test_targets.py` (równoważność z dotychczasowym `build_card`).
- Test: pełne `uv run pytest`, ruff, mypy. DONE: zielone + migracja v4→v5 na fixturze z danymi; liczba testów rośnie, żaden dotychczasowy nie zmieniony.
- NIE RÓB: `planner.py`, `web/plan.py`, szablonów, zmian w istniejących migracjach, sieci.

### W3 — serwis planu i ekran „Plan” (`haiku-worker`; po REJECT `sonnet-worker`) — po W1 i W2
- Pliki: nowy `plan_service.py`, nowy `web/plan.py` (router: `GET /plan?tydzien=`, `POST /plan/generuj`, `/plan/zatwierdz`, `/plan/wymien` (pola: `sesja|dzien`, `tryb=los|polubione`, `id`), `/plan/cofnij`), `templates/plan.html`, `base.html` (zakładka „Plan”, `section == 'plan'`), `static/app.css` (komponenty Pojemniki zgodnie z `DESIGN.md`: `dz-plan`, `dz-session`, `dz-meal`, flagi bilansu), rejestracja routera w `web/app.py` (jedna linia), testy: `tests/test_plan_service.py` (pula, historia, generacja→zapis→odczyt, undo, zatwierdzenie, brak regeneracji zatwierdzonego bez `potwierdz=1`), `tests/test_web_plan.py` (TestClient: ekran pusty bez profilu → komunikat, generacja, zatwierdzenie, wymiana, cofnij, 390/1280 px bez poziomego przewijania nie testujemy w pytest).
- Szczegóły: ścieżki względne z `{{ root }}` (Ingress), formularze POST + redirect 303, wszystkie wartości w Jinja autoescape, `tydzien` walidowany (ISO poniedziałek; inny dzień → poniedziałek tygodnia), brak profilu/wagi osoby → plan dla drugiej, brak obu → komunikat „Uzupełnij profile”; ostrzeżenia z `PlanResult.warnings` pokazane nad planem; ikony w SVG (bez emoji w UI: 🎲/♥ jako SVG).
- Test: pełne `uv run pytest`, ruff, mypy. DONE: zielone; ekran renderuje plan z danych demo (skrypt seed w teście), ostrzeżenia widoczne.
- NIE RÓB: `planner.py`, migracji, `store.py` (poza wywołaniami metod z W2), zmian opcji.

## Weryfikacja etapu (ja, po paczkach)
`verifier` dla W1, W2, W3 (z BASELINE `git status --short`); pełny `uv run pytest`, ruff, mypy; wygenerowanie planu na kopii prawdziwego `dieta.db` (odczyt z kontenera, poza repo): czas, ostrzeżenia, kcal dnia ±10%; Playwright 390/1280 px tylko jeśli ingress dostępny, inaczej HTTP w kontenerze (jak w E3c). Wynik → ROADMAP, potem `stage-end`.

## Rollback
Etapy to commity: `git revert` zakresu E4. Migracja v5 tylko dodaje tabele (stary kod je ignoruje) — baza zostaje poprawna. Brak zmian w istniejących opcjach i tabelach.

## Ryzyka
- Mała pula znajomych na obiad/kolację (~42): obniżanie progu z ostrzeżeniem; w razie potrzeby szerszy próg w opcjach.
- Kcal szacowane (`auto`, ~±30% dla części) mogą psuć bilans dnia → plakietka „szacunek” na karcie posiłku, a bilans liczony z tych wartości.
- Kategorie `lunch`/`dinner` z reguł mogą być nieostre — stąd premie zamiast twardego filtra.
