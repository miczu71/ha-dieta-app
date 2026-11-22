# PLAN E1 — fundament `ha-dieta-app`

Cel: profile 2 osób, cele kcal i makro, ręczny wpis wagi z wykresem. Bez wydania (0.1.0 po E3).
Rollback: `gh repo delete miczu71/ha-dieta-app` (robi user) + usunięcie `/config/addons/ha-dieta-app`.
Testy: w `dieta/app`: `.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy`.

## Interfejsy (stałe dla W1–W3)
```python
# dieta/nutrition.py
Sex = Literal["m", "f"]; Goal = Literal["lose", "maintain", "gain"]
PAL = {1: 1.2, 2: 1.375, 3: 1.55, 4: 1.725, 5: 1.9}
def bmr_mifflin(sex: Sex, age: int, height_cm: float, weight_kg: float) -> float
def tdee(bmr: float, pal_level: int) -> float
def target_kcal(tdee_kcal: float, goal: Goal, pace_kg_week: float) -> float   # lose: -pace*7700/7; gain: +; maintain: bez zmiany
@dataclass(frozen=True) class Macros: protein_g: int; fat_g: int; carb_g: int
def macros(kcal: float, weight_kg: float, goal: Goal, protein_g_kg: float | None = None, fat_pct: float = 0.30) -> Macros
def trend_7d(points: list[tuple[date, float]]) -> list[tuple[date, float]]   # średnia z wpisów w oknie 7 dni wstecz (łącznie z dniem)
# dieta/store.py
class Store: __init__(path: Path); profile(person_id) -> Profile | None; save_profile(Profile); add_weight(person_id, day, kg); weights(person_id, days=90) -> list[tuple[date, float]]; latest_weight(person_id) -> float | None
@dataclass Profile: person_id, sex, age, height_cm, pal_level, goal, pace_kg_week, kcal_override: int | None, protein_g_kg: float | None, fat_pct: float
```

## Wykonawca

### W1 — `dieta/nutrition.py` (haiku-worker, worktree `wt new ha-dieta-app E1-W1`)
- Pliki: `dieta/app/src/dieta/nutrition.py`, `dieta/app/tests/test_nutrition.py`.
- Zmiana: funkcje z interfejsu; Mifflin-St Jeor (m: 10w+6.25h-5a+5, f: ...-161); domyślne białko 1,8 g/kg przy `lose`, 1,6 pozostałe;
  tłuszcz = fat_pct·kcal/9; węglowodany = reszta/4 (≥0); zaokrąglenia do int dopiero w `Macros`; wiek ≥ 10, waga > 0 inaczej `ValueError`.
- Test: `cd dieta/app && .venv/bin/pytest tests/test_nutrition.py -q`; TDD, wartości policzone ręcznie
  (np. m,30,180,80 → BMR 1780; PAL3 → 2759; lose 0,5 kg/tydz. → -550).
- DONE: wszystkie testy zielone, ruff + mypy czyste dla tych 2 plików.
- NIE RÓB: nie ruszaj innych plików, nie dodawaj zależności, nie commituj.

### W2 — `dieta/store.py` (haiku-worker, worktree `E1-W2`)
- Pliki: `dieta/app/src/dieta/store.py`, `dieta/app/tests/test_store.py`.
- Zmiana: SQLite `sqlite3` stdlib; tabele `profile(person_id PK 1|2, sex, age, height_cm, pal_level, goal, pace_kg_week,
  kcal_override NULL, protein_g_kg NULL, fat_pct)` i `weight(person_id, day TEXT ISO, kg, PRIMARY KEY(person_id, day))`;
  `add_weight` robi upsert; `weights` zwraca ostatnie N dni rosnąco; migracje `PRAGMA user_version` (v1 = powyższe schematy),
  `person_id` spoza {1,2} → `ValueError`; katalog rodzica tworzony.
- Test: `.venv/bin/pytest tests/test_store.py -q` na `tmp_path`; TDD.
- DONE: testy (zapis/odczyt profilu, upsert wagi, okno dni, migracja z pustego pliku, ponowne otwarcie) zielone.
- NIE RÓB: inne pliki, ORM, commit.

### W3 — `dieta/web/` (po scaleniu W1+W2; sonnet-worker lub ja — ekran z wieloma decyzjami)
- Pliki: `web/app.py`, `web/templates/{base,profiles}.html`, `web/static/{app.css,app.js}`, `dieta/app/tests/test_web.py`, `DESIGN.md`.
- Zmiana: `GET /` ekran „Profile” (2 karty: cel kcal, makro, TDEE, BMR), `POST /profile/{id}`, `POST /weight/{id}`,
  wykres 90 dni jako SVG z serwera (waga + średnia 7 dni), komunikat „wartości szacunkowe, nie porada medyczna”,
  tokeny Pojemniki z `~/dev/dieta-spike/design-system/project/` (jasny/ciemny), ścieżki względne (ingress), `Cache-Control: no-store`,
  statyki `?v=<version>`, wersja w UI z `config.yaml`.
- Test: `.venv/bin/pytest -q` (TestClient, dane w `tmp_path`).
- DONE: testy zielone, ekran działa 390/1280 px w obu motywach.
- NIE RÓB: ikony poza SVG, kolory literałem poza tokenami, emoji.
