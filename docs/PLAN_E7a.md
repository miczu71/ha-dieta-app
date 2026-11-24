# PLAN E7a — nazwy osób w UI

Repo: `/config/addons/ha-dieta-app`, kod `dieta/app/src/dieta/`, testy `dieta/app/tests/`. Kontekst: `docs/ROADMAP_E7.md`, `CONTEXT.md`.

## Wykonawca (haiku-worker, jedna paczka; schemat bazy = „ryzyko” → **sonnet-worker**)
Test: `cd <WORKTREE>/dieta/app && uv run pytest -q && uv run ruff check . && uv run mypy src` (pierwszy plan, bez tła).
TDD: najpierw testy (czerwone), potem kod. Przy edycji kodu add-onu: skill `addon-conventions`.

1. `store.py` — migracja 10 dopisana na koniec `_MIGRATIONS` (nie edytować istniejących wpisów):
   `CREATE TABLE person (person_id INTEGER PRIMARY KEY CHECK(person_id IN (1,2)), name TEXT NOT NULL);`.
   `BACKUP_BEFORE_VERSIONS = (8, 9, 10)`.
2. `store.py` — `Store.person_names() -> dict[int, str]` (tylko wpisane) i `Store.set_person_name(person_id, name)`:
   `_check_person`, `INSERT … ON CONFLICT(person_id) DO UPDATE`, pusta nazwa (`""`) → `DELETE FROM person WHERE person_id = ?`.
3. Nowy `names.py` — `person_name(store, settings, person_id) -> str`: nazwa z bazy, inaczej `settings.person_name(person_id)`
   (opcja add-onu albo „Osoba N”, istniejące zachowanie). `validate_name(raw, other) -> str | None`: przycięcie spacji,
   długość ≤30, różna od drugiej (`casefold`); `None` = niepoprawna; pusty ciąg = poprawny (kasuje nazwę).
4. Zamiana 6 wywołań `settings.person_name(...)` na `person_name(store, settings, ...)`: `push.py:120`, `plan_service.py:451`,
   `web/app.py:79`, `realization.py:268` i `:328`, `web/dzis.py:69`. Jeśli w którymś miejscu nie ma `store` w zasięgu — nie zmieniaj
   sygnatur więcej niż o parametr `store`; zgłoś w raporcie.
   **Uwaga:** `web/dzis.py:69` liczy `labels` raz przy tworzeniu routera (`make_dzis_router`) — zmieniona nazwa nie byłaby widoczna do restartu.
   Przenieś wyliczenie `labels` do wnętrza handlerów (przy każdym żądaniu); dodaj test: zmiana nazwy po utworzeniu aplikacji jest widoczna w `/dzis`.
   Sprawdź tak samo `web/app.py` i `web/plan.py` pod kątem etykiet liczonych przy starcie (`grep -n "labels"`).
5. `web/app.py` — `POST /person/{person_id}/name` (pole `name`): `_person(person_id)`, `names.validate_name`, błąd → `_back("nazwa")`,
   sukces → `store.set_person_name` i `_back()`. `web/templates/profiles.html` — pole „Nazwa” (`maxlength=30`) nad formularzem profilu
   w karcie osoby; komunikat błędu `?error=nazwa` analogicznie do `?error=profil`.
6. Testy: migracja 10 na bazie user_version 9 (tabela `person` jest, stare dane nietknięte, kopia `.pre-v10`);
   `set_person_name`/`person_names`/kasowanie; `validate_name` (31 znaków, same spacje, duplikat „Ola”/„ola”, 30 znaków OK);
   łańcuch nazw (baza > opcja > „Osoba N”); trasa `POST /person/1/name` (OK, duplikat → `?error=nazwa`); nazwa widoczna w `/plan`,
   `/dzis`, `/bilans`, `/` oraz w tekście `push` (istniejące testy `test_web_dzis.py`, `test_push.py`, `test_realization.py`
   dalej zielone bez zmian oczekiwań).

DONE: nowe testy zielone, pełny `pytest` zielony, ruff i mypy czyste, `git status --short` zawiera tylko `store.py`, `names.py`,
`push.py`, `plan_service.py`, `realization.py`, `web/app.py`, `web/dzis.py`, `web/templates/profiles.html` i pliki testów.
NIE RÓB: zmiany `Profile`/`age` (to E7b), usuwania opcji `person_N_name` z `config.yaml`, wersji/CHANGELOG, commitów, pushy, refaktorów poza punktami 1–6.

Seamy testów: `Store(tmp_path / "d.db")`, klient FastAPI jak w `tests/test_web_dzis.py`; bez sieci i bez LLM.

## Weryfikacja po paczce
`claimcheck` → `verifier` (BASELINE `git status --short` sprzed delegacji, ścieżka worktree); REJECT → raz ten sam tier, potem eskalacja.
Po zielonych testach: commit w worktree, `wt land`, pytest na `main`.

## Rollback
`git revert` commita; tabela `person` zostaje w bazie, niczym nie czytana. Kopia `dieta.db.pre-v10` na hoście add-onu.
