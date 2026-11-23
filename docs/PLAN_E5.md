# PLAN E5 — realizacja planu (odhaczanie, bilans, poranny push)

Wywiad: 2026-11-23 (grill-me, potwierdzony „Go”). Wydanie: 0.2.0 po E5 (łączone wg ROADMAP). Commity etapów na `main`.

## Cel i miara sukcesu
- **Kto korzysta:** dwie osoby; każda odhacza własną porcję każdego posiłku z zatwierdzonego planu (ekran „Dziś”).
- **Automatycznie:** poranny push na `notify.family` (jedna wiadomość: plan dnia z porcjami i kcal obu osób + wczorajsze „bez odpowiedzi”), bilans dnia/tygodnia liczony z odhaczeń.
- **Za zgodą / poza E5:** przesuwanie porcji i zapis do kalendarza Cookidoo (E8+); nic nie jest zgadywane — brak odhaczenia = „bez odpowiedzi”.
- **Sukces:** po tygodniu widać realny bilans kcal+makro per osoba kontra cel bez ręcznego liczenia; push dociera o ustawionej godzinie raz dziennie; testy, ruff, mypy zielone; verifier APPROVE.

## Decyzje z wywiadu (wiążące)
1. Jednostka odhaczania: **osoba × dzień × slot**. Wspólny obiad może być zjedzony przez jedną osobę, pominięty przez drugą.
2. Stany: `eaten` (zjedzone wg planu), `skipped` (pominięte), `substitute` (zamiennik = wpis ręczny: nazwa + kcal, białko/tłuszcz/węgle opcjonalnie). Brak wiersza = „bez odpowiedzi”. „Cofnij odhaczenie” = usunięcie wiersza.
3. Bilans: kcal i makro per osoba, zjedzone vs cel dnia (`person_targets`). `skipped` = 0, `substitute` = wpisana wartość (brak makro → nie liczy się do makro), „bez odpowiedzi” nie liczy się wcale. Tydzień = suma dni; porównanie z celem tylko dla dni ≤ dziś.
4. Okno edycji: dziś i do 7 dni wstecz; przyszłość zablokowana. Odhaczać można tylko w **zatwierdzonym** planie (szkic nie).
5. Resztki: bez przenoszenia. W bilansie tygodnia licznik „zostało porcji”: suma zaplanowanych porcji osoby z posiłków `skipped`/`substitute` należących do sesji wielodniowych (`days > 1`) bieżącego planu.
6. Push: tylko poranny, jedna wiadomość, usługa z opcji `push_notify` (domyślnie `family`; puste = wyłączony), godzina `push_time` (domyślnie `07:00`); tylko gdy zatwierdzony plan ma posiłki dziś; raz dziennie (meta `push_last` = data ostatniej udanej wysyłki); bez nadrabiania po restarcie po godzinie push. Bez wieczornego przypomnienia.
7. Nawigacja: nowe zakładki „Dziś” (`/dzis`) i „Bilans” (`/bilans`) w `base.html`.

## Model danych — migracja v6 (tylko dodaje)
Dziennik jest **niezależny od `plan_meal`** (regeneracja/wymiana kasuje `plan_meal`/`plan_session`), więc przy odhaczeniu zapisujemy migawkę przepisu i wartości.
```
meal_log(day TEXT NOT NULL,                       -- ISO data posiłku
         person_id INTEGER NOT NULL CHECK(person_id IN (1,2)),
         slot TEXT NOT NULL CHECK(slot IN ('breakfast','lunch','dinner','snack1','snack2')),
         status TEXT NOT NULL CHECK(status IN ('eaten','skipped','substitute')),
         recipe_id INTEGER,                       -- migawka (NULL przy zamienniku bez przepisu)
         title TEXT NOT NULL,                     -- tytuł przepisu albo nazwa zamiennika
         portions REAL,                           -- zaplanowana porcja osoby w chwili odhaczenia
         kcal REAL NOT NULL DEFAULT 0, protein REAL, fat REAL, carbs REAL,
         updated_at TEXT NOT NULL,
         PRIMARY KEY (day, person_id, slot))
```
Wartości: `eaten` → `portions × kcal/makro przepisu na porcję` (z puli `plan_service.build_pool`, jak w ekranie Plan); `skipped` → `kcal = 0`, makro NULL, `portions` zaplanowane; `substitute` → wartości z formularza, `portions` zaplanowane, `recipe_id` zaplanowanego przepisu (do liczenia resztek).
Rollback: tabela zostaje (stary kod ją ignoruje); `git revert` zakresu E5.

## Opcje HA (config.yaml, translations pl/en, `Settings`)
`homeassistant_api: true` (config.yaml, poziom główny), `push_notify: "family"` (schema `match(^[a-z0-9_]*$)?`), `push_time: "07:00"` (schema jak `refresh_time`). `Settings.push_notify: str = ""`, `Settings.push_time: str = "07:00"`; `_clock(value, default)` dostaje parametr domyślny (dotychczasowe wywołanie `refresh_time` bez zmiany zachowania). Bump wersji dopiero przy wydaniu.

## Architektura
- `realization.py` (W1) — serwis bez HTTP: `day_log(store, settings, day, today) -> DayLog`, `mark(...) -> Outcome`, `clear(...) -> Outcome`, `week_balance(store, settings, week_start, today) -> WeekBalance`. Plan dnia bierze z zatwierdzonego planu tygodnia `monday_of(day)`; kcal/makro porcji z `plan_service.build_pool`.
- `store.py` (W1) — migracja v6 **na końcu** `_MIGRATIONS` + `MealLogEntry` + `set_meal_log(entry)`, `clear_meal_log(day, person_id, slot)`, `meal_log(day_from, day_to) -> list[MealLogEntry]`.
- `web/dzis.py` + `dzis.html` + `bilans.html` (W2) — ekrany i formularze POST (redirect 303, ścieżki względne).
- `push.py` (W3) — składanie wiadomości, wysyłka (stdlib `urllib`, bez nowej zależności), pętla poranna.
- `web/app.py` (W1, jednorazowo) — rejestracja pustego routera `make_dzis_router` i wywołanie `push.start(settings, store)` w `lifespan`; W2 i W3 **nie ruszają** `web/app.py`.

## Etapy i kolejność
- **E5.0 (ja):** ten plan + ROADMAP + commit docs.
- **W1** (sonnet, **ryzyko: migracja**) → verifier → **W2 ‖ W3** (haiku, rozłączne pliki, obie po W1) → verifier per paczka → pełne testy → HTTP na danych demo → ROADMAP + `stage-end`.
- Worktree: `wt new <repo> E5-W1` itd. (`wt land` sekwencyjnie). Wydanie 0.2.0 po E5 wg ROADMAP (spójna grupa E3c–E5): osobny krok `E5-release` = simplify → code-review → release → weryfikacja na żywo.

## Wykonawca
Katalog roboczy: `<WORKTREE>/dieta/app`. Test: `uv run --extra dev pytest -q`, `uv run --extra dev ruff check src tests`, `uv run --extra dev ruff format --check src tests`, `uv run --extra dev mypy`. Konwencje: skill `addon-conventions`; komentarze/docstringi po polsku, identyfikatory po angielsku; ścieżki względne (Ingress); Jinja autoescape; zero emoji w UI (ikony SVG).

### W1 — migracja v6, store, opcje, `realization.py`, szkielety (`sonnet-worker`, ryzyko: migracja)
- Pliki: `src/dieta/store.py` (migracja v6 + `MealLogEntry` + 3 metody), `src/dieta/settings.py` (`push_notify`, `push_time`, `_clock(value, default)`), `dieta/config.yaml` (`homeassistant_api: true`, `options`, `schema`), `dieta/translations/pl.yaml`, `dieta/translations/en.yaml`, nowy `src/dieta/realization.py`, nowe szkielety: `src/dieta/web/dzis.py` (`make_dzis_router(settings, store, templates) -> APIRouter` z pustym routerem), `src/dieta/push.py` (`def start(settings, store) -> asyncio.Task[None] | None: return None`), `src/dieta/web/app.py` (include_router + `push.start` w `lifespan`: jeśli zwróci task, anulować go w `finally` jak `task` nocnego; nie startować, gdy podano `sync_runner`/`crawl_runner` — tryb testów), testy: `tests/test_realization.py`, `tests/test_meal_log_store.py`, uzupełnienie `tests/test_settings.py`; w istniejących testach dopuszczalna **wyłącznie** zmiana asercji wersji schematu 5→6.
- `realization.py` — API (typy `frozen dataclass`):
  - `NOTICE_OK="ok"`, `NOTICE_NO_PLAN="no_plan"`, `NOTICE_NOT_APPROVED="not_approved"`, `NOTICE_OUT_OF_WINDOW="out_of_window"`, `NOTICE_NO_MEAL="no_meal"`, `NOTICE_BAD="bad"`; `Outcome(notice)`.
  - `WINDOW_DAYS = 7`.
  - `mark(store, settings, day: date, person_id: int, slot: str, status: str, today: date, *, name: str = "", kcal: float | None = None, protein: float | None = None, fat: float | None = None, carbs: float | None = None) -> Outcome`: walidacja (`status ∈ eaten|skipped|substitute`, `person_id ∈ PERSON_IDS`, `slot ∈ planner.SLOTS`; `substitute` wymaga niepustego `name` ≤ 80 znaków po `strip` i `0 ≤ kcal ≤ 5000`; makro, jeśli podane, `0 ≤ x ≤ 1000`) → `bad`; okno `today-7 ≤ day ≤ today` inaczej `out_of_window`; plan tygodnia `monday_of(day)` brak → `no_plan`, `draft` → `not_approved`; brak posiłku w tym slocie/dniu albo `p{n}_portions is None` → `no_meal`; zapis migawki wg „Model danych”. Ten sam `(day, person, slot)` zapisany ponownie nadpisuje (zmiana stanu).
  - `clear(store, settings, day, person_id, slot, today) -> Outcome`: to samo okno; usuwa wiersz (brak wiersza → `ok`).
  - `day_log(store, settings, day: date, today: date) -> DayLog`: `DayLog(day, editable: bool, plan_status: str | None, people: tuple[PersonDayLog | None, PersonDayLog | None], meals: tuple[MealLog, ...])`; `MealLog(slot, slot_label, title, recipe_id, session_days, per_person: tuple[MealPerson | None, MealPerson | None])`; `MealPerson(portions, planned_kcal, status: str | None, logged_kcal, sub_name)`; `PersonDayLog(label, target_kcal, eaten_kcal, eaten_protein, eaten_fat, eaten_carbs, unanswered: int)`. `editable` = plan zatwierdzony i dzień w oknie. Osoba bez celu (brak profilu) → `None`.
  - `week_balance(store, settings, week_start, today) -> WeekBalance`: `WeekBalance(week_start, prev_week, next_week, plan_status, people: tuple[PersonWeek | None, PersonWeek | None])`; `PersonWeek(label, days: tuple[PersonDayTotals, ...] (7), eaten_kcal, target_kcal (suma celów dni ≤ today), unanswered, leftover_portions: float)`; `PersonDayTotals(day, name, date, eaten_kcal, target_kcal, unanswered, future: bool)`. `leftover_portions` wg decyzji 5 (sesje `days>1` bieżącego planu; wiersze `skipped`/`substitute`, `portions` z wiersza).
  - Pomocniczo `unanswered_for(store, settings, day, today) -> dict[int, int]` (liczba zaplanowanych posiłków osoby bez wiersza) — używa go W3.
- Testy (zamrożony katalog z demo jak w `test_web_plan.py::_demo`, plan zatwierdzony przez `plan_service.generate`/`approve`): migracja v5→v6 na bazie z danymi; CRUD i nadpisanie stanu; `eaten` zapisuje migawkę (zmiana planu po odhaczeniu — wymiana sesji — nie zmienia bilansu); okno (wczoraj ok, 8 dni wstecz i jutro → `out_of_window`); szkic → `not_approved`; brak planu → `no_plan`; osoba z `portions=None` → `no_meal`; walidacja zamiennika; bilans dnia (`skipped`=0, `substitute`=wpis, brak odpowiedzi nie liczy się, `unanswered`); bilans tygodnia (cel tylko dni ≤ today, `leftover_portions` z sesji wielodniowej); `Settings` (`push_time` zła → `07:00`, `push_notify` domyślnie `""` bez opcji i `family` z opcji).
- DONE: pełny pytest, ruff, ruff format, mypy zielone; liczba testów rośnie (≥ 367 + nowe); `uv run python -c "import yaml,sys;yaml.safe_load(open('../config.yaml'))"` OK.
- NIE RÓB: `web/dzis.py` poza pustym szkieletem, `push.py` poza szkieletem, szablonów, `app.css`, `base.html`, `planner.py`, `plan_service.py`, istniejących migracji 1–5, bumpa wersji, CHANGELOG/DOCS.

### W2 — ekrany „Dziś” i „Bilans” (`haiku-worker`; po REJECT `sonnet-worker`) — po W1
- Pliki: `src/dieta/web/dzis.py` (router), nowe `src/dieta/web/templates/dzis.html`, `src/dieta/web/templates/bilans.html`, `src/dieta/web/templates/base.html` (zakładki „Dziś” `section == 'dzis'` i „Bilans” `section == 'bilans'`), `src/dieta/web/static/app.css` (komponenty w stylu Pojemniki: `dz-day`, `dz-meal-log`, `dz-state`, `dz-bar-kcal`; tokeny z istniejącego CSS, jasny i ciemny motyw), testy `tests/test_web_dzis.py`.
- Trasy: `GET /dzis?dzien=YYYY-MM-DD` (brak/zły format → dziś; nawigacja ←/→ dni i „Dziś”), `POST /dzis/odhacz` (pola: `dzien`, `osoba`, `slot`, `stan ∈ zjedzone|pominiete|zamiennik|reset`, `nazwa`, `kcal`, `bialko`, `tluszcz`, `wegle`) → wywołuje `realization.mark`/`clear` z `today=date.today()`, redirect 303 na `../dzis?dzien=…&notice=<kod>`; `GET /bilans?tydzien=…` (poniedziałek tygodnia; nawigacja tygodni). Zły `osoba`/`stan` albo liczby nie do sparsowania (przecinek dziesiętny akceptowany) → `notice=bad`, bez zapisu.
- Szczegóły UI: ekran „Dziś”: dla każdego posiłku karta z tytułem (link do `przepis`), a pod nią wiersz na osobę: porcja „1,4”, kcal planowane, aktualny stan jako czytelna plakietka tekstowa (nie sam kolor) — „bez odpowiedzi”/„zjedzone”/„pominięte”/„zamiennik: nazwa, kcal” — oraz przyciski Zjedzone / Pominięte / Zamiennik / Cofnij (tylko gdy `editable`; inaczej tylko odczyt z komunikatem „Zatwierdź plan tygodnia”, „Poza oknem edycji”). Zamiennik: `<details>` z mini-formularzem (nazwa, kcal, makro opcjonalnie). Nagłówek dnia: pasek postępu kcal zjedzone/cel na osobę + liczba „bez odpowiedzi”. Ekran „Bilans”: tabela 7 dni × osoba (zjedzone / cel, dni przyszłe wyszarzone), suma tygodnia z odchyleniem w %, „bez odpowiedzi”, „zostało porcji”. Komunikaty `notice` mapowane na polski tekst (jak `NOTICE_TEXT` w `web/plan.py`). Mobile 390 px bez poziomego przewijania; przyciski ≥ 44 px; ikony tylko SVG.
- Testy: TestClient jak `test_web_plan.py` (plan wygenerowany i zatwierdzony): render dziś z posiłkami; odhaczenie `zjedzone` → plakietka i bilans; `pominiete`; `zamiennik` z kcal; `reset`; szkic → przyciski nieobecne, komunikat; dzień poza oknem → odczyt; zły `stan`/`osoba`/kcal → `notice=bad` bez zapisu; XSS w `nazwa` escapowany; `bilans` renderuje się z tygodniem bez planu i z planem.
- DONE: pełny pytest, ruff, format, mypy zielone; render ekranów z danych demo bez wyjątku.
- NIE RÓB: `store.py`, `realization.py`, `web/app.py`, `push.py`, `planner.py`, `plan_service.py`, `settings.py`, migracji.

### W3 — poranny push (`haiku-worker`; po REJECT `sonnet-worker`) — po W1, równolegle z W2
- Pliki: `src/dieta/push.py` (zastępuje szkielet), `tests/test_push.py`.
- `compose(day: DayLog, yesterday_unanswered: dict[int, int], labels, panel_path) -> tuple[str, str]` — tytuł „Plan na dziś”, treść: linia na posiłek „Obiad: <tytuł> — <Imię1> 1,4 (520 kcal), <Imię2> 1,0 (370 kcal)” (osoba bez porcji pominięta), linia „Cel: <Imię1> 2000 kcal, <Imię2> 2400 kcal”, na końcu (tylko gdy > 0) „Wczoraj bez odpowiedzi: <Imię1> 2, <Imię2> 1”; wiadomość ≤ 1000 znaków (ucinane z „…”); dane (tytuły) wstawiane jako zwykły tekst.
- `send(service: str, title: str, message: str, panel_path: str) -> bool` — `urllib.request` POST `http://supervisor/core/api/services/notify/<service>` z nagłówkiem `Authorization: Bearer $SUPERVISOR_TOKEN`, JSON `{"title","message","data":{"clickAction","url","tag":"dieta-plan-dnia"}}`, timeout 15 s; bez `SUPERVISOR_TOKEN` (dev) → tylko log i `True`; wyjątek/HTTP ≥ 400 → log `type(exc).__name__`/kod (bez tokenu, bez treści wiadomości) i `False`. Nazwa usługi walidowana `^[a-z0-9_]+$`. `panel_path = "/app/" + os.environ.get("HOSTNAME", "b969c720-dieta").replace("-", "_")`.
- `run_once(settings, store, today, sender=send) -> str` — kody: `"disabled"` (`push_notify` puste), `"already_sent"` (meta `push_last == today.isoformat()`), `"no_plan"` (brak zatwierdzonego planu albo brak posiłków dziś), `"sent"` (po udanej wysyłce `store.set_meta("push_last", today.isoformat())`), `"failed"` (bez zapisu meta; kolejna próba dopiero następnego dnia). Wczorajsze „bez odpowiedzi” z `realization.unanswered_for(..., day=today-1)`; liczone tylko dla zatwierdzonego planu tamtego tygodnia.
- `start(settings, store) -> asyncio.Task[None] | None` — `None`, gdy `push_notify` puste; inaczej `asyncio.create_task(_loop(...))`: pętla `await asyncio.sleep(seconds_until(datetime.now(), settings.push_time))` (z `web/kupujemy.py`), `await asyncio.to_thread(run_once, …)`; wyjątek kroku logowany (typ) i pętla trwa; `CancelledError` przepuszczony.
- Testy: `compose` (formatowanie, brak drugiej osoby, ucięcie, brak „Wczoraj…” gdy 0); `run_once` z fałszywym `sender` (`sent` raz, drugi raz `already_sent`, `failed` nie zapisuje meta, `disabled`, `no_plan` dla szkicu i dla dnia bez posiłków); `send` z podmienionym `urllib` (nagłówek, JSON, błąd HTTP → `False`, brak tokenu → `True` bez sieci, zła nazwa usługi → `False`); log nie zawiera tokenu ani treści.
- DONE: pełny pytest, ruff, format, mypy zielone.
- NIE RÓB: `web/app.py`, `store.py`, `realization.py`, `settings.py`, szablonów, `config.yaml`, nowych zależności.

## Weryfikacja etapu (ja, po paczkach)
`verifier` dla W1, potem W2 i W3 (z BASELINE `git status --short`); `claimcheck` przed verifierem (repo PUBLIC); pełny `uv run --extra dev pytest -q`, ruff, format, mypy; skrypt na kopii prawdziwego `dieta.db` (poza repo): migracja v5→v6, plan zatwierdzony na kopii, odhaczenie/zamiennik/bilans tygodnia, `push.run_once` z fałszywym senderem (treść wiadomości do przejrzenia); HTTP na danych demo (Playwright 390/1280 px, jeśli ingress dostępny). Wynik → ROADMAP, potem `stage-end` (`nextStage: "E5-release"`); w kroku `E5-release`: `simplify` → `code-review` → `release` 0.2.0 → weryfikacja na żywo, potem E6.

## Rollback
Etapy to commity: `git revert` zakresu E5. Migracja v6 dodaje tylko tabelę `meal_log` (stary kod ją ignoruje). `push_notify` puste = push wyłączony bez zmiany kodu.

## Ryzyka
- `homeassistant_api: true` zmienia uprawnienia add-onu → zmiana w `config.yaml` wymaga przeładowania/aktualizacji add-onu przez Supervisora (wydanie).
- Migawka kcal przy `eaten` bazuje na szacunku kcal przepisu (plakietka „szacunek” w „Dziś” jak w „Plan”).
- Push bez nadrabiania: restart add-onu po `push_time` = brak wiadomości tego dnia (świadomie; prostota > pewność).
- Profile jeszcze niewpisane na żywo — przed wydaniem poprosić usera (bez nich ekran „Dziś” pokazuje „Uzupełnij profile”).
