# PLAN E2 — most do Zakupów + słownik składników (trzon, część 1)

Cel: Dieta zna to, co kupujecie, w postaci składników ogólnych. Ekran „Co kupujemy” (top składniki, „prawdopodobnie w domu”)
i kolejka zatwierdzania wątpliwych mapowań. Bez wydania Diety (0.1.0 po E3); **osobne wydanie Zakupów 1.10.0 po E2** (zasady wydań z CLAUDE.md).
Rollback: Dieta — `git revert` commitów E2 (migracja v2 tylko dodaje tabele); Zakupy — usunąć trasy `/api/v1/*` i opcję `api_token`
(bez tokena API jest wyłączone, więc nieszkodliwe nawet bez cofnięcia).
Testy Diety: `cd dieta/app && .venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy`.
Testy Zakupów: ta sama komenda w `lidl/app` (`/config/addons/ha-lidl-app`).

## Decyzje z wywiadu (2026-11-22)
- API tylko zakupy (bez cen/promocji — E6). Kształt: **agregat per produkt**, bez paragonów i adresów.
- Auth: wspólny token w opcjach obu add-onów (`password`); bez tokena w Zakupach API wyłączone.
- Dieta trzyma snapshot w `/data`; odświeżanie nocne + przycisk „Odśwież teraz”.
- Mapowanie: seed `slownik.json` z E0 + reguły → AI `auto:text` w paczkach po 25 → wątpliwe do zatwierdzenia w UI.
- Trwałość: klasa składnika (świeże 4 / chłodnia 10 / suche 30 / mrożonki 60 dni, progi w opcjach) → „prawdopodobnie w domu”.
- Nie-jedzenie: wykluczone po `product_category` z Zakupów (`category != 'food'`) + klasa `non-food` w słowniku, widoczne w kolejce.
- UI: top składniki + kolejka zatwierdzania (bez scalania/dzielenia/aliasów).
- Hostname Zakupów w sieci Supervisora (zweryfikowane na żywo `ha_get_app(source="installed")`): `f0987e0f-lidl`, port 8099.
  URL domyślny w Diecie: `http://f0987e0f-lidl:8099`.

## Kontrakt API (stały dla W1–W3)
`GET /api/v1/purchases?months=12` z nagłówkiem `Authorization: Bearer <api_token>`; kody: 401 brak/zły token, 503 gdy `api_token` pusty.
```json
{"generated_at": "2026-11-22T10:00:00", "months": 12,
 "products": [{"code": "n:0001497", "chain": "lidl", "name": "Twaróg klinek półtł.", "names": ["Twaróg klinek półtł."],
               "purchases": 8, "spend": 49.91, "first": "2026-02-03", "last": "2026-11-20",
               "dates": ["2026-02-03", "..."], "category": "food"}]}
```
- `code` = `art_id` po `merges`/resolverze (`History._resolver`), `category` z `product_category` (brak wiersza → `"food"`),
  `chain` z `tickets.chain`, `dates` = rosnąco, unikalne dni zakupu z okna; tylko `items` (bez `foreign_items`).
- Jedna pozycja na `code`; `names` = unikalne nazwy z okna (najczęstsza w `name`); `spend` = suma `items.total`; `purchases` = liczba dni zakupu.

## Interfejsy Diety (stałe dla W2/W3)
```python
# dieta/zakupy_client.py  (stdlib urllib, bez nowych zależności; wywoływane w wątku)
@dataclass(frozen=True) class Purchase: code, chain, name, names: tuple[str,...], purchases: int, spend: float, first: date, last: date, dates: tuple[date,...], category: str
class ZakupyError(Exception)
def fetch_purchases(base_url: str, token: str, months: int = 12, *, opener=None) -> list[Purchase]   # opener = seam testów
# dieta/dictionary.py
Durability = Literal["fresh", "chilled", "dry", "frozen"]
def classify_rules(name: str) -> tuple[str | None, Durability | None]   # (składnik ogólny, trwałość) z reguł słów kluczowych; (None, None) gdy brak
def load_seed(path: Path) -> dict[str, SeedEntry]                        # dieta/data/seed_slownik.json (kopia z E0), klucz = znormalizowana nazwa produktu
def map_with_llm(names: list[str], *, ask: Callable[[list[str]], list[dict] | None]) -> list[LlmMapping]   # paczki po 25, jedna ponowna próba, seam `ask`
def make_llm_ask(url: str, key: str) -> Callable[[list[str]], list[dict] | None]   # POST {url}/v1/chat/completions, model auto:text, temp 0; bez logowania nagłówków/treści
# dieta/profile.py
@dataclass(frozen=True) class IngredientStat: ingredient, purchases, spend, last, weight_score: float, at_home: bool, durability
def build_stats(purchases, mapping, today: date, shelf_days: dict[Durability, int]) -> list[IngredientStat]   # weight_score = Σ 0.5**(wiek_dni/90) po datach zakupu
# dieta/store.py  — migracja v2 (dopisana NA KOŃCU _MIGRATIONS)
# purchase(code PK, chain, name, names_json, purchases, spend, first, last, dates_json, category, fetched_at)
# ingredient(id PK, name UNIQUE, durability)
# mapping(code PK, ingredient_id NULL → ingredient.id, status CHECK IN ('rule','seed','ai','pending','approved','excluded'), source_name)
# meta(key PK, value)  — m.in. last_sync_at, last_sync_error
```
Opcje Diety (config.yaml + schema + translations pl/en + `settings.py`): `zakupy_url` (str), `zakupy_token` (password?), `llm_url` (str?),
`llm_key` (password?), `refresh_time` (match HH:MM, domyślnie `03:30`), `shelf_fresh_days` 4, `shelf_chilled_days` 10, `shelf_dry_days` 30, `shelf_frozen_days` 60 (int).
Opcje Zakupów: `api_token` (password?, domyślnie puste → API wyłączone).

## Wykonawca

### W1 — API w Zakupach (**sonnet-worker, ryzyko: auth i middleware**; `wt new /config/addons/ha-lidl-app E2-W1`, równolegle z W2)
- Pliki: `lidl/app/src/lidl/history.py` (metoda `purchase_aggregates(months, today)`), `lidl/app/src/lidl/web/app.py` (middleware `guard` + trasa),
  `lidl/app/src/lidl/settings.py` (opcja `api_token`), `lidl/config.yaml` (opcja + schema `api_token: "password?"`),
  `lidl/translations/{pl,en}.yaml` (jeśli istnieją), `lidl/app/tests/test_purchases_api.py` (nowy), `lidl/DOCS.md` (sekcja API).
- Zmiana: agregacja wg kontraktu API wyżej, z użyciem istniejących `_resolver()`, `_categories()`, tabel `tickets`/`items`.
  Middleware: ścieżki `/api/v1/` omijają kontrolę peera INGRESS_PROXY, ale wymagają `Authorization: Bearer` porównywanego `hmac.compare_digest`;
  pusty `api_token` → 503; reszta ścieżek bez zmian (nadal tylko ingress). Odpowiedź `Cache-Control: no-store`. Token nigdy w logach.
- Test: `cd lidl/app && .venv/bin/pytest tests/test_purchases_api.py -q` + pełny zestaw; TDD. Seamy: baza `History` w `tmp_path` z kilkoma paragonami
  (lidl + biedronka, scalony kod, kategoria drugstore, poza oknem 12 mies.), TestClient z `settings.dev=False` i peerem ≠ ingress.
  Przypadki: 401 bez/zły token, 503 bez skonfigurowanego tokena, 200 z poprawnym, agregaty (purchases/spend/first/last/dates), wykluczenie `foreign_items`,
  `/produkty` spoza ingressu nadal 403.
- DONE: nowe + dotychczasowe testy zielone, ruff + mypy czyste; `config.yaml` ma opcję i schemę; wersja NIE zmieniona (bump robi skill `release`).
- NIE RÓB: nie zmieniaj schematu bazy ani wersji, nie dodawaj zależności, nie ruszaj tras istniejących poza middleware, nie commituj.

### W2 — klient, snapshot, słownik, profil w Diecie (haiku-worker, worktree `wt new /config/addons/ha-dieta-app E2-W2`, równolegle z W1)
- Pliki: `dieta/app/src/dieta/{zakupy_client,dictionary,profile}.py` (nowe), `dieta/app/src/dieta/store.py` (migracja v2 + metody),
  `dieta/app/src/dieta/settings.py`, `dieta/config.yaml`, `dieta/translations/{pl,en}.yaml`, `dieta/app/src/dieta/data/seed_slownik.json`
  (kopia `~/dev/dieta-spike/data/slownik.json`, bez zmian; dodaj `"dieta.data" = ["*.json"]` do package-data w `pyproject.toml`),
  testy `tests/test_{zakupy_client,dictionary,profile,store_zakupy}.py`.
- Zmiana: interfejsy wyżej. `Store`: `replace_purchases(list[Purchase])` (transakcja, kasuje stare), `purchases()`, `upsert_ingredient`, `set_mapping`,
  `mappings()`, `pending()`, `approve(code, ingredient_name, durability)`, `exclude(code)`, `meta`. Kolejność mapowania nowego `code`:
  `category != 'food'` → `excluded`; seed (po nazwie) → `seed`; reguły → `rule`; reszta → AI (`ai`) z flagą wątpliwości, gdy brak składnika albo brak trwałości → `pending`.
  Ręczne `approved`/`excluded` nigdy nie są nadpisywane przy kolejnym syncu. `at_home` = (today − last) ≤ próg dla trwałości składnika.
  Funkcja `sync(store, settings, today, *, fetch, ask)` składa całość (seamy `fetch`, `ask`); błąd sieci → `meta.last_sync_error`, stary snapshot zostaje.
- Test: `.venv/bin/pytest -q` na `tmp_path` z fałszywymi `fetch`/`ask` (bez sieci); TDD. Przypadki: zły token → `ZakupyError`; migracja v1→v2 z zachowaniem profili;
  idempotentny sync; `approved` przetrwa sync; AI zwraca śmieci → `pending`; `weight_score` na ręcznie policzonym przykładzie; granica progu `at_home`.
- DONE: testy zielone, ruff + mypy czyste, `config.yaml` zgodny z `settings.py` i testem istniejącym (`tests/test_health.py`/config).
- NIE RÓB: nie ruszaj `web/`, nie dodawaj zależności (tylko stdlib), nie wypisuj tokenów/kluczy w logach i wyjątkach, nie commituj.

### W3 — ekran „Co kupujemy” (po W2; ja albo sonnet-worker, ekran z decyzjami)
- Pliki: `web/app.py`, `web/templates/{base,kupujemy,zatwierdz}.html`, `web/static/app.css`, `__main__.py` (pętla nocnego sync wg `refresh_time` w `lifespan`),
  `tests/test_web_kupujemy.py`, `DESIGN.md`.
- Zmiana: nawigacja Profile / Co kupujemy; `GET /kupujemy` (top składniki: częstość, wydatek, ostatni zakup, znacznik „prawdopodobnie w domu”, stan ostatniego syncu),
  `POST /kupujemy/odswiez` (sync w wątku, przekierowanie z komunikatem), `GET /kupujemy/zatwierdz` (kolejka `pending`: zatwierdź / zmień składnik+trwałość / odrzuć jako nie-składnik),
  `POST /kupujemy/zatwierdz` (kod w polu formularza); brak tokena/URL → pusty stan z instrukcją opcji; wiersz kolejki jako „pojemnik” (styl Pojemniki), SVG, tokeny, ścieżki względne.
- Test: TestClient z fałszywym sync; Playwright 390/1280 px, jasny/ciemny, na danych demo.
- DONE: testy zielone, brak przewijania poziomego, kolejka działa end-to-end na demo.
- NIE RÓB: emoji/PNG, kolorów literałem poza tokenami, edycji `store.py` poza ewentualną drobną metodą zgłoszoną w raporcie.

## Kolejność i weryfikacja
1. `progress_tracker`: paczki W1, W2 jako osobne taski; `verifier` po każdej (BASELINE `git status --short`, `claimcheck`: repo publiczne).
2. W1 ‖ W2 (rozłączne repo, żadna nie potrzebuje wyniku drugiej, każda ≥3 edycje), potem W3, potem `verifier` W3.
3. Spójność kontraktu: test integracyjny w Diecie ładuje JSON wygenerowany przez W1 (fixture skopiowana z wyniku testu W1) przez `fetch_purchases`.
4. Na żywo (po wydaniu Zakupów 1.10.0): ustawić ten sam token w obu add-onach (robi user: `! ` / UI opcji), sprawdzić sync i ekran; do czasu wydania Dieta działa na danych demo.
5. Zakończenie: commity (Dieta `main`; Zakupy — osobny commit i wydanie `release` po `simplify` → `code-review`), `stage-end`.
