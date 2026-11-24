# ROADMAP E7 — nazwy osób w UI i wiek z daty urodzenia (Dieta 0.5.0)

Wywiad `grill-with-docs` z 2026-11-24, potwierdzony przez usera. Słownik: `CONTEXT.md`. Repo: `/config/addons/ha-dieta-app`,
kod `dieta/app/src/dieta/`, testy `dieta/app/tests/`. Po E7a i E7b jedno wydanie Dieta **0.5.0**; Zakupów nie ruszamy.

## Cel i sukces
1. Użytkownicy: Wy dwoje. Nazwy i daty urodzenia wpisujecie w UI Profili, bez zaglądania do opcji add-onu.
2. Automatycznie: wiek liczy się z daty urodzenia; nazwa zmienia się wstecz na ekranach Plan, Dziś, Bilans, Profile i w powiadomieniu push.
3. Za zgodą (user klika): wpisanie prawdziwych dat urodzenia w miejsce przybliżonych z migracji; generowanie nowego planu po zmianie.
4. Sukces: nazwy widać na każdym ekranie i w push; po wpisaniu daty profil pokazuje obliczony wiek i kcal zgodne z wzorem Mifflina;
   zatwierdzony plan nie zmienia się po cichu; po migracji istniejące profile dają te same cele kcal co przed nią.

## Decyzje z wywiadu
- Nazwa: pole w UI Profili, zapis w bazie; opcje add-onu `person_1_name`/`person_2_name` zostają jako wartość pierwotna (łańcuch: baza → opcja → „Osoba N”).
  1–30 znaków po przycięciu spacji, obie nazwy różne (bez rozróżniania wielkości liter), puste pole = wraca do domyślnej.
- Data urodzenia: jedyne źródło wieku. Migracja starych profili: 1 lipca roku „dziś − wiek”, oznaczona jako przybliżona; UI prosi o poprawkę.
- Wiek do BMR: plan liczy z dnia generowania (cele już siedzą w `params_json`, więc zamrożone); „Dziś”, „Bilans” i karta profilu z dzisiejszej daty.
- Walidacja daty: nie z przyszłości, obliczony wiek 10–120 lat (jak dziś pole `wiek`).
- Dane osobowe zostają w `/data` add-onu: nie trafiają do repo, pamięci ani logów.
- Etapy: E7a nazwy → E7b data urodzenia → E7-release (jedno wydanie).

## E7a — nazwy osób w UI (`docs/PLAN_E7a.md`)
Fundament: tabela `person` + łańcuch nazw + pole w formularzu. Wartość sama z siebie: zmieniacie nazwy bez restartu add-onu.
Kroki: (1) testy czerwone: store, walidacja, łańcuch nazw; (2) migracja 10 (`person`) + `BACKUP_BEFORE_VERSIONS`; (3) `Store.person_names/set_person_name`;
(4) `names.person_name(store, settings, pid)`; (5) 6 wywołań `settings.person_name` → nowa funkcja; (6) trasa `POST /person/{id}/name` i pole w `profiles.html`;
(7) pytest + ruff + mypy, commit.
Rollback: migracja 10 jest addytywna (nowa tabela); `git revert` commita kodu; baza zachowuje nieużywaną tabelę `person`, a stara wersja ją ignoruje.
Kopia bazy: `dieta.db.pre-v10` tworzona przy pierwszym starcie na nowej wersji.

## E7b — data urodzenia (`docs/PLAN_E7b.md`)
Wartość: wiek zawsze aktualny, bez ręcznych poprawek co rok. Zakres: migracja 11 (kolumny `birth_date`, `birth_date_approx`, backfill), `Profile.birth_date` +
`age_on(day)`, `person_targets(..., today)`, formularz `input type=date`, walidacja, baner „data przybliżona”, testy granic (urodziny dziś, 29 lutego, jutro, rok 10/120 lat).
Rollback: migracja 11 dodaje kolumny; stare `age` zostaje zapisywane jako obliczony wiek (kolumna NOT NULL), więc stara wersja dalej działa po `git revert`;
kopia `dieta.db.pre-v11`.

## E7-release — Dieta 0.5.0 (ostatni etap, bez pytania)
`simplify` → `code-review` ‖ `silent-failure-hunter` ‖ `pr-test-analyzer` ‖ security (general-purpose, `git diff <baza>..HEAD`) → `release` (CHANGELOG,
`config.yaml` 0.5.0, tag non-draft) → weryfikacja na żywo: add-on startuje, logi bez wyjątków, istnieją `dieta.db.pre-v10` i `dieta.db.pre-v11`,
strona Profile 200 z polem daty i nazwą, cele kcal obu osób identyczne jak przed migracją (zapisać liczby przed wydaniem), push z nazwami (jeśli `push_notify`).
Krok produkcyjny: aktualizacja add-onu przez Supervisor; migracja baz startuje sama, rollback = poprzednia wersja add-onu + kopie `.pre-vN`.

## Ryzyka znane z kodu
- `web/dzis.py:69` liczy etykiety osób raz przy starcie routera — E7a przenosi to do żądania (bez tego rename wymaga restartu).
- `Profile.age` jest użyte w ok. 26 miejscach testów (7 plików) — E7b przepisuje je na `birth_date`.
- Migracja 11 używa `strftime('%Y','now')` (zegar kontenera); przybliżona data zależy od dnia migracji, stąd flaga `birth_date_approx`.

## Poza zakresem (zostaje na później)
- Ręczna ocena podejrzanych cen (seler, pomidory, jajka) i półka Zakupów po nocy — otwarte z E6c.3 w `docs/ROADMAP.md`.
- Usuwanie opcji `person_N_name` z `config.yaml` (zmiana schematu opcji u wszystkich instalacji).
- Dług techniczny z E6c.

## Stan
E7a: **ZROBIONE 2026-11-24** (commit `e2f56fb` na `main`, zapushowany, niewydane). Migracja 10 (`person`), `names.py` (łańcuch baza → opcja → „Osoba N”, `validate_name`), `POST /person/{id}/name`, pole „Nazwa” (`value` = tylko zapis z bazy, `placeholder` = domyślna), etykiety `/dzis` liczone w handlerze.
Weryfikacja: pytest 596 passed (na `main` po `wt land`), ruff i mypy czyste; verifier: 1. REJECT (pole „Nazwa” wstępnie wypełnione domyślną nazwą → zapis wpisywał ją do bazy), po poprawce ACCEPT.
Komenda testu działa jako `uv run --extra dev pytest -q` (z `uv run pytest` brak zależności dev); pełny pytest ~3 min, wołać z `timeout 580`.
NIEBLOKUJĄCE: `validate_name` porównuje z drugą osobą po nazwie wyświetlanej, więc „Osoba 2” dla osoby 1 jest odrzucane, gdy druga osoba nie ma własnej nazwy; 8 istniejących testów dostało tylko podbicie wersji schematu 9→10 (kontrakt mówił „bez zmian oczekiwań”) — w E7b podbić do 11 tak samo.
Następny: **E7b** (`docs/PLAN_E7b.md`, migracja 11 — liczba w teście `len(_MIGRATIONS)` ma być 11), potem E7-release (0.5.0).
