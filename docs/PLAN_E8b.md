# PLAN E8b — przechowywanie i import pomiarów Zepp (migracja 12)

Roadmapa: `docs/ROADMAP_E8.md`. Fundament pod E8c (UI) i E8d (sync). Bez UI, bez sieci, bez zmian w `targets.py`.
Wartość etapu: dane Zepp mają gdzie wylądować, a dzienna waga trafia do `weight`, więc kcal i wykres działają bez zmian.

## Decyzje
- **Mięśnie:** Zepp daje `muscle_rate` (%), nie kg → kolumna `muscle_pct` (w roadmapie było `muscle_kg`; kg policzy E8e z wagi).
- **Ręczny wpis wygrywa:** `weight` dostaje kolumnę `source TEXT NOT NULL DEFAULT 'manual'`. `add_weight` ustawia `'manual'`
  (nadpisuje też wpis `zepp`). Import zapisuje `'zepp'` tylko gdy dnia nie ma albo wpis ma `source='zepp'`.
- **Dzień pomiaru:** `ts` (s, UTC) → data w lokalnej strefie procesu (`datetime.fromtimestamp(ts).date()`); wiele pomiarów dnia → ostatni z `weight_kg`.
- **Hasła nie ma w bazie.** `cookies_json` i `app_token` to jedyne sekrety; nie trafiają do `repr`, logów ani wyjątków.
- Import jest czysty względem sieci: przyjmuje listę `BodyRecord`, nie woła Zepp.

## Schemat (migracja 12, dopisana na końcu `_MIGRATIONS`; 12 w `BACKUP_BEFORE_VERSIONS` → `dieta.db.pre-v12`)
```sql
ALTER TABLE weight ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
CREATE TABLE zepp_account (
    person_id INTEGER PRIMARY KEY CHECK(person_id IN (1,2)),
    device_id TEXT NOT NULL, cookies_json TEXT NOT NULL DEFAULT '{}',
    user_id TEXT, app_token TEXT, fuid INTEGER,
    status TEXT NOT NULL CHECK(status IN ('pending_otp','linked','reconnect')),
    last_sync_ts INTEGER, last_error TEXT
);
CREATE TABLE body_measurement (
    person_id INTEGER NOT NULL CHECK(person_id IN (1,2)), ts INTEGER NOT NULL, source TEXT NOT NULL,
    weight_kg REAL, fat_pct REAL, muscle_pct REAL, water_pct REAL, bone_kg REAL, visceral REAL,
    bmr_kcal REAL, bmi REAL, impedance REAL, body_score REAL, metabolic_age REAL, raw_json TEXT,
    PRIMARY KEY (person_id, ts, source)
);
```

## Rollback
Tabele addytywne, kolumna z DEFAULT: `git revert` kodu + (gdyby trzeba było schodzić ze schematu) przywrócenie `dieta.db.pre-v12`.
Nic produkcyjnego w tym etapie (moduł niepodpięty do UI/pętli).

## Wykonawca
**Ryzyko → `sonnet-worker`** (migracja schematu). **WORKTREE:** podaje rodzic (`wt new dieta-e8b`). Katalog roboczy `dieta/app`.

**Pliki:**
1. `dieta/app/src/dieta/store.py` — migracja 12; `BACKUP_BEFORE_VERSIONS = (8, 9, 10, 11, 12)`; `add_weight` zapisuje `source='manual'`
   (`ON CONFLICT … DO UPDATE SET kg = excluded.kg, source = 'manual'`); nowe metody (poniżej).
2. `dieta/app/src/dieta/body.py` — nowy: `import_records(store, person_id, records) -> ImportResult(new, skipped, days)`.
3. `dieta/app/tests/test_store.py` — zaktualizować asercje `user_version == 11` → `12`.
4. `dieta/app/tests/test_body_store.py` — nowy (testy store + migracja z v11).
5. `dieta/app/tests/test_body_import.py` — nowy (testy importu).

**Metody `Store` (nowe):**
- `zepp_account(person_id) -> ZeppAccount | None` (frozen dataclass; `cookies_json`, `app_token` z `field(repr=False)`);
  `save_zepp_account(ZeppAccount)` (upsert); `delete_zepp_tokens(person_id)` (zeruje `cookies_json='{}'`, `app_token=NULL`, status `reconnect`, historia zostaje);
  `set_zepp_status(person_id, status, *, last_error=None)`; `set_zepp_last_sync(person_id, ts)`.
- `add_body_measurements(person_id, rows: Sequence[BodyRow]) -> int` (INSERT OR IGNORE po PK, zwraca liczbę nowych; `BodyRow` = pola kolumn bez `person_id`);
  `body_measurements(person_id, since_ts=None) -> list[BodyRow]` (rosnąco po `ts`); `latest_body(person_id) -> BodyRow | None`.
- `add_weight_if_not_manual(person_id, day, kg) -> bool` (zapis `source='zepp'` tylko gdy brak wiersza albo `source='zepp'`).

**`body.import_records`:** mapuje `zepp.BodyRecord` → `BodyRow` (`source='zepp'`, `muscle_pct=muscle_rate`, `raw_json=json.dumps(raw, sort_keys=True)`),
zapisuje `add_body_measurements`, potem dla każdego dnia ostatni pomiar z `weight_kg is not None` → `add_weight_if_not_manual`.
Idempotentny (drugi import tych samych rekordów: `new == 0`, `weight` bez zmian).

**Testy (najpierw czerwone):** migracja v11→12 (baza zbudowana skryptami 1–11 + dane, po otwarciu: tabele, `weight.source='manual'` dla starych wierszy, kopia `.pre-v12`);
import idempotentny; duplikat `(ts)`; dwa pomiary tego samego dnia → ostatni do `weight`; ręczny wpis z dnia pomiaru wygrywa; wcześniejszy wpis `zepp` jest nadpisywany nowszym pomiarem;
`add_weight` po imporcie przejmuje dzień (`source='manual'`); rekord bez `weight_kg` trafia do `body_measurement`, nie do `weight`; osoba 2 niezależna od osoby 1;
`repr(ZeppAccount)` nie zawiera tokenu ani ciasteczek; `delete_zepp_tokens` zachowuje historię.

**Komenda testu:** `cd dieta/app && PYTHONPATH=src .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest -q`
(pełny pytest ~200 s; w trakcie pracy celuj w `tests/test_body_*.py tests/test_store.py`).

**DONE:** nowe testy zielone; pełny pytest/ruff/format/mypy czyste; `store.py` zmienia tylko migrację, stałą backupu, `add_weight` i nowe metody; istniejące testy bez osłabiania asercji (poza `11→12`).

**NIE RÓB:** edycji `zepp.py`, `targets.py`, `web/*`, `config.yaml`, CHANGELOG; edycji istniejących wpisów `_MIGRATIONS`; commitów; sieci; zapisu haseł; logowania tokenów/ciasteczek.
