# PLAN E7b — wiek z daty urodzenia

Repo: `/config/addons/ha-dieta-app`. Kontekst: `docs/ROADMAP_E7.md`, `CONTEXT.md`. Wymaga E7a na `main` (migracja 10 zajęta).

## Wykonawca (sonnet-worker, jedna paczka, „ryzyko”: migracja danych + obliczenia kcal)
Test: `cd <WORKTREE>/dieta/app && uv run pytest -q && uv run ruff check . && uv run mypy src` (pierwszy plan, bez tła).
TDD: testy czerwone → kod. Skill `addon-conventions`.

1. `store.py` — migracja 11 na koniec `_MIGRATIONS`: `ALTER TABLE profile ADD COLUMN birth_date TEXT;`
   `ALTER TABLE profile ADD COLUMN birth_date_approx INTEGER NOT NULL DEFAULT 0;`
   `UPDATE profile SET birth_date = (CAST(strftime('%Y','now') AS INTEGER) - age) || '-07-01', birth_date_approx = 1;`
   `BACKUP_BEFORE_VERSIONS = (8, 9, 10, 11)`. Kolumna `age` (NOT NULL) zostaje.
2. `store.py` — `Profile`: pole `age: int` zastąpione przez `birth_date: date` i `birth_date_approx: bool = False`;
   metoda `age_on(day: date) -> int` (pełne lata; urodziny w dniu `day` już liczą; 29 lutego → w latach nieprzestępnych 1 marca).
   `_PROFILE_COLUMNS`, odczyt i `save_profile`: zapis `birth_date` (ISO) i `birth_date_approx`; kolumna `age` zapisywana jako `age_on(date.today())`.
   Zgłoś, jeśli `_PROFILE_COLUMNS` jest używane gdzie indziej niż w `profile()` i `save_profile()`.
3. `targets.py` — `person_targets(store, settings, person_id, today: date | None = None)`: `today = today or date.today()`,
   `nutrition.bmr_mifflin(profile.sex, profile.age_on(today), …)`. `plan_service.build_targets` bez zmian (plan = dzień generowania,
   cele zamrożone w `params_json`).
4. `web/app.py` — `POST /profile/{id}`: pole `birth_date: str = Form(...)` zamiast `age`; `date.fromisoformat` (błąd → `_back("profil")`);
   niepoprawna, gdy w przyszłości lub `age_on(dziś)` poza 10–120; zapis z `birth_date_approx=False`. `web/templates/profiles.html`:
   `<input type="date" name="birth_date" max="{{ today }}" required>`, obok „Wiek: N lat” (`p.age_on(today)`), baner „Data przybliżona — wpisz prawdziwą”,
   gdy `p.birth_date_approx`.
5. Pozostałe użycia `profile.age` / `Profile(age=…)` w kodzie i testach (`grep -rn "age=" tests`, ~26 miejsc w 7 plikach testów)
   przepisane na `birth_date=`; wartości dobrane tak, by wiek na `TODAY` testu dawał dotychczasową liczbę.
6. Testy: migracja 11 na bazie user_version 10 z profilem `age=30` → `birth_date` = `<rok>-07-01`, `birth_date_approx=1`, cel kcal
   identyczny jak przed migracją; `age_on` (urodziny dziś/jutro/wczoraj, 29 lutego, granice 10 i 120 lat); walidacja trasy (przyszłość → `?error=profil`,
   9 lat, 121 lat, zły format); zapis zdejmuje flagę przybliżenia; baner widoczny tylko przy fladze; `person_targets(today=…)`
   zmienia BMR dokładnie w dniu urodzin; plan wygenerowany przed urodzinami zachowuje `params_json`.

DONE: nowe testy zielone, pełny `pytest` zielony, ruff i mypy czyste, `git status --short` zawiera tylko `store.py`, `targets.py`,
`web/app.py`, `web/templates/profiles.html` i pliki testów.
NIE RÓB: usuwania kolumny `age`, zmian planera i `params_json`, wersji/CHANGELOG/`config.yaml`, commitów, pushy, refaktorów poza punktami 1–6.

## Przed wydaniem (krok produkcyjny, tylko odczyt)
Zapisać cele kcal obu osób z żywej karty profilu (`GET /`) do `docs/ROADMAP_E7.md`, żeby porównać po migracji.

## Rollback
`git revert`; kolumny `birth_date`/`birth_date_approx` zostają nieużywane, a `age` jest nadal wypełniana, więc poprzednia wersja działa.
Pełny powrót bazy: kopia `dieta.db.pre-v11` (przy aktywnym add-onie — stop add-onu, podmiana pliku, start; robi to user).
