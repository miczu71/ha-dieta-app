# PLAN E3 — katalog zewnętrzny + dopasowanie (trzon, część 2) → wydanie Diety 0.1.0

Cel: Dieta ma lokalny katalog przepisów z kcal/makro na porcję, w którym każdy przepis ma `znajomość` względem tego, co kupujecie
(„5/6 składników kupujecie, brakuje: tahini”). Katalog sortowany po znajomości, polubione/„nie proponuj” wspólne dla domu.
Po E3: `simplify` → `code-review` → `release` 0.1.0 + instalacja (zasady wydań z CLAUDE.md). Planer to E4.
Rollback: `git revert` commitów E3 (migracja v3 tylko dodaje tabele; katalog znika, profile i słownik zostają).
Testy: `cd dieta/app && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy`.

## Decyzje z wywiadu (2026-11-22)
- Źródła 0.1.0: **Centrum Respo, przepisy.lidl.pl (JSON-LD), Policzona Szama (blog, parser HTML, sitemapa)**. Po 0.1.0 osobne konektory:
  Aniagotuje, Healthy Omnomnom, YouTube Policzonej (RSS/Data API), Biedronka, Cookidoo (opcja).
- Odświeżanie: nocny crawl (po syncu zakupów, godzina `refresh_time`) + przycisk „Odśwież teraz” per źródło. Grzeczne limity: ≤ 1 zapytanie/s,
  dzienny limit nowych przepisów na źródło (opcja `crawl_daily_limit`, domyślnie 150), nagłówek User-Agent bez danych usera, szanuj `robots.txt`,
  stan w `meta` (ostatni crawl/błąd na źródło). Przepis raz pobrany nie jest pobierany ponownie (odświeżenie tylko na żądanie).
- Metadane planera: kategoria posiłku, `przechowywanie_dni`, `mrozenie` **z reguł** (tagi/URL/nazwa źródła, słowa kluczowe: zupa, gulasz, curry,
  sałatka…); braki → AI `auto:text` w paczkach → wątpliwe do kolejki zatwierdzania (jak słownik).
- Brak kcal: przepis w katalogu z plakietką „brak kcal”, wykluczony z planera (flaga `has_nutrition`); kcal z OFF dopiero w E7.
- Składniki przepisów: ten sam słownik (reguły → AI w paczkach → kolejka `pending`); niezmapowany składnik = „nieznany” (liczy się do mianownika).
- `znajomość`: domyślnie sort po niej, filtr progu (domyślnie 70%, suwak w UI); planer E4 użyje progu z opcji. Składniki „podstawowe” poza licznikiem.
- Polubione/odrzucone: **wspólne dla domu**. Odrzucone znikają z domyślnego widoku (filtr „pokaż odrzucone”).
- Dane w `/data` (SQLite); sieć tylko stdlib `urllib` (jak `zakupy_client`), parsowanie HTML `html.parser` + `json` (bez nowych zależności).

## Interfejsy (stałe dla W1–W3)
```python
# dieta/sources/base.py
@dataclass(frozen=True) class RawIngredient: text: str; amount: float | None; unit: str | None; name: str
@dataclass(frozen=True) class Recipe:
    source: str            # "respo" | "lidl" | "policzona"
    source_id: str         # stabilny: URL kanoniczny
    title: str; url: str; image: str | None
    servings: float | None
    kcal: float | None; protein: float | None; carbs: float | None; fat: float | None   # NA PORCJĘ
    minutes: int | None
    tags: tuple[str, ...]; category_hint: str | None
    ingredients: tuple[RawIngredient, ...]
class RecipeSource(Protocol):
    name: str
    def list_urls(self, *, fetch: Fetch) -> list[str]            # sitemapa / listing; Fetch = Callable[[str], str] (seam)
    def parse(self, url: str, html: str) -> Recipe | None        # None gdy to nie przepis
# dieta/sources/{jsonld,respo,lidl,policzona}.py — konkretne źródła; wspólny parser JSON-LD `Recipe` (ISO 8601 czas, nutrition "kcal"/"g")
# dieta/catalog.py
def classify_meal(recipe: Recipe) -> tuple[str | None, int | None, bool | None]   # (kategoria: breakfast|lunch|dinner|snack|dessert|None, przechowywanie_dni, mrozenie) z reguł
def crawl(store, source: RecipeSource, *, fetch, today, limit: int, sleep) -> CrawlResult   # seamy fetch/sleep; błąd pojedynczego URL nie przerywa
# dieta/match.py
def familiarity(ingredients: list[str|None], stats: Mapping[str, IngredientStat], *, basics: frozenset[str]) -> Familiarity   # (known, total, missing[], at_home[])
# dieta/store.py — migracja v3 (NA KOŃCU _MIGRATIONS)
# recipe(id PK, source, source_id, UNIQUE(source, source_id), title, url, image, servings, kcal, protein, carbs, fat, minutes, tags_json,
#        meal_category, storage_days, freezable, has_nutrition, meta_status CHECK IN ('rule','ai','pending','approved'), fetched_at)
# recipe_ingredient(recipe_id, pos, text, amount, unit, name, ingredient_id NULL → ingredient.id, PRIMARY KEY(recipe_id, pos))
# recipe_flag(recipe_id PK, liked INTEGER, dismissed INTEGER)
```
Opcje (config.yaml + schema + translations pl/en + `settings.py`): `crawl_daily_limit` (int, 150), `familiar_threshold` (int %, 70), `sources` (lista włączonych, domyślnie wszystkie 3).

## Wykonawca

### W1 — konektory źródeł (haiku-worker; `wt new /config/addons/ha-dieta-app E3-W1`; równolegle z W2)
- Pliki: `dieta/app/src/dieta/sources/{__init__,base,jsonld,respo,lidl,policzona}.py` (nowe), `dieta/app/tests/test_sources.py` (nowy),
  `dieta/app/tests/fixtures/sources/*.html` (po 2–3 przepisy na źródło, zanonimizowane do niezbędnego minimum, skopiowane z prób E0:
  `~/dev/dieta-spike/data/` i ponownego pobrania pojedynczych stron).
- Zmiana: interfejsy wyżej. `jsonld.py`: wyciąg bloku `application/ld+json` z `@graph`/listy, `Recipe` → `Recipe`; `nutrition.calories` „350 kcal” → 350;
  `recipeYield` → porcje (liczba z tekstu, „4 porcje” → 4); składniki `ilość jednostka nazwa` (regex PL, ułamki „1/2”, „½”). `policzona.py`:
  sitemapa `sitemap_articles_1.xml`, blok `Na N porcji`, lista `nazwa – ilość`, wartości odżywcze na porcję (opisane w `~/dev/dieta-spike/scripts/e0_policzona.py`, port logiki).
- Test: `.venv/bin/pytest tests/test_sources.py -q`; TDD na fixturach (bez sieci). Przypadki: kcal/makro per porcja, porcje, składnik z ułamkiem, strona bez przepisu → `None`,
  Policzona bez kcal → `kcal=None`, uszkodzony JSON-LD → `None` bez wyjątku.
- DONE: testy zielone, ruff + mypy czyste, żadna zależność nie dodana, `list_urls` czysty (sieć tylko przez `fetch`).
- NIE RÓB: nie ruszaj `store.py`, `web/`, `config.yaml`; nie pobieraj sieci w testach; nie commituj.

### W2 — migracja v3, metadane, crawl, znajomość (**sonnet-worker, ryzyko: migracja**; `wt new /config/addons/ha-dieta-app E3-W2`; równolegle z W1)
- Pliki: `dieta/app/src/dieta/{catalog,match}.py` (nowe), `store.py` (migracja v3 + metody), `sync.py` (podpięcie mapowania składników przepisów do istniejącego potoku),
  `settings.py`, `dieta/config.yaml`, `dieta/translations/{pl,en}.yaml`, `tests/test_{catalog,match,store_recipes}.py`.
  W2 korzysta z `Recipe`/`RecipeSource` z kontraktu (jeśli W1 nie skończyło: lokalna kopia dataclass w `sources/base.py` zgodna z kontraktem — W1 ją nadpisze przy merge).
- Zmiana: `Store`: `upsert_recipe(Recipe, meta)`, `recipes(filter…)` z `familiarity`, `set_flag(recipe_id, liked|dismissed)`, `pending_recipe_meta()`, `approve_recipe_meta(id, category, storage_days, freezable)`.
  Składniki przepisu mapowane przez istniejące `classify_rules` → seed → AI (paczki 25) → `pending` (nieistniejące wpisy dodawane do `mapping` z `source_name`).
  `classify_meal`: reguły słów kluczowych; `has_nutrition = kcal is not None`. `crawl`: limit dzienny, `sleep(1)` między zapytaniami, wyjątki per URL do `meta.last_crawl_error:<źródło>`.
  Ręczne `approved` nigdy nie nadpisywane przy ponownym crawlu.
- Test: `.venv/bin/pytest -q` na `tmp_path`, fałszywe `fetch`/`ask`/`sleep`; TDD. Przypadki: migracja v2→v3 zachowuje dane; idempotentny crawl; limit dzienny; błąd jednego URL nie
  przerywa; `familiarity` na ręcznie policzonym przykładzie (5/6, brak tahini, składniki podstawowe pominięte, nieznany liczy się do mianownika); AI śmieci → `pending`.
- DONE: testy zielone, ruff + mypy czyste, `config.yaml` zgodny z `settings.py` (test konfiguracji).
- NIE RÓB: nie ruszaj `web/` ani `sources/`; nie loguj tokenów/kluczy; nie commituj.

### W3 — ekran Katalog + pętla crawla (po W1+W2; ja albo sonnet-worker)
- Pliki: `web/app.py`, `web/catalog.py` (nowy router), `web/templates/{base,katalog,przepis}.html`, `web/static/app.css`, `__main__.py` (nocny crawl po syncu), `tests/test_web_katalog.py`, `DESIGN.md`, `DOCS.md`, `CHANGELOG.md`.
- Zmiana: nawigacja Profile / Co kupujemy / Katalog; `GET /katalog` (karty: tytuł, źródło, kcal/białko/węgl./tłuszcz na porcję, czas, plakietka „brak kcal”, „znajomość 5/6 · brakuje: …”,
  sort po znajomości, filtry: próg %, kategoria posiłku, źródło, polubione, pokaż odrzucone), `POST /katalog/{id}/serce` i `/odrzuc`, `GET /katalog/{id}` (szczegóły + składniki z oznaczeniem znane/brak/w domu),
  `POST /katalog/odswiez?zrodlo=` (crawl w wątku), rozszerzona kolejka zatwierdzania o `pending` metadane przepisów. Styl Pojemniki, SVG, tokeny, ścieżki względne, 390/1280 px.
- Test: TestClient z fałszywym crawlem; Playwright 390/1280 px jasny/ciemny na danych demo.
- DONE: testy zielone, brak przewijania poziomego, serce/odrzuć/odśwież działają end-to-end na demo.
- NIE RÓB: emoji/PNG, kolorów literałem poza tokenami, edycji `store.py` poza drobną metodą zgłoszoną w raporcie.

## Kolejność i weryfikacja
1. `progress_tracker`: tytuł „dieta E3”, W1, W2, W3 i ich `verifier` jako osobne taski. Paczki W1 ‖ W2 (rozłączne pliki, każda ≥ 3 edycje), potem W3.
2. Po każdej paczce: `claimcheck` (repo publiczne) → `verifier` (BASELINE `git status --short`).
3. Pomiar na żywo po W2 (jednorazowo, wynik do raportu etapu): ile przepisów w katalogu ma kcal, odsetek ze znajomością ≥ 70% / 60% / 50% dla Policzonej Szamy (otwarte z E0).
4. Koniec: commity na `main`, `stage-end`; po E3 wydanie 0.1.0 (nie ma kolejnego etapu bez wydania — ROADMAP: E3 kończy grupę 0.1.0).
