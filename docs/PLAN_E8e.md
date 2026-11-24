# PLAN E8e — dane z Zepp w diecie (Katch-McArdle, historia, korekta wg trendu)

Roadmapa: `docs/ROADMAP_E8.md`. Wartość: karta osoby liczy BMR z masy beztłuszczowej, pokazuje skład ciała i proponuje korektę kcal. Nic nie zmienia się automatycznie.
Bez zmian schematu DB i bez zmian w `zepp*.py`. Dane: `body_measurement` (`BodyRow`, `store.py:332`; pole `muscle_pct`, nie kg), `weight`.

## Decyzje
- `nutrition.bmr_katch(lbm_kg) = 370 + 21.6 * lbm_kg`. `lbm = waga * (1 - fat_pct/100)`, waga = `latest_weight` (zgodnie z obecnym kodem).
- `Store.latest_body(person_id) -> BodyRow | None`: najnowszy wiersz z `fat_pct IS NOT NULL` i `weight_kg IS NOT NULL`.
- `targets.person_targets`: pomiar z `fat_pct` ≤ 14 dni (względem `today`) → Katch-McArdle, inaczej Mifflin. `PersonTarget` dostaje `bmr_formula: Literal["katch","mifflin"]` (domyślnie `"mifflin"`) i `body_age_days: int | None`
  (pola z wartością domyślną na końcu — istniejące wywołania nie pękają). TDEE, makro, `kcal_override` bez zmian.
- Trend: `nutrition.trend_rate_kg_week(points, days=21)` — regresja liniowa wagi po dniach z ostatnich `days` dni; `None`, gdy < 4 punkty lub rozpiętość < 14 dni.
- Korekta: `targets.kcal_suggestion(goal, pace_kg_week, current_kcal, trend_kg_week) -> int | None`. Oczekiwane: lose `-pace`, gain `+pace`, maintain `0`.
  `delta = trend - oczekiwane`; `|delta| <= 0.25` → `None`; inaczej `round((current_kcal - delta * 7700 / 7) / 10) * 10`.
  Kierunek: trend wyższy niż oczekiwany (za mało chudnie) → mniej kcal.
- Karta: linia „BMR: Katch-McArdle” albo „BMR: Mifflin” (+ „pomiar składu sprzed N dni”), propozycja z przyciskiem „Zastosuj” → `POST /profile/{id}/kcal` (pole `kcal`, ustawia `kcal_override`,
  zakres 800–6000, poza zakresem → błąd jak w innych formularzach profilu, przekierowanie względne jak pozostałe trasy profilu — wzorzec poprawki 0.5.1).
- Wykresy (`web/chart.py`, SVG po stronie serwera, bez JS, klasy `wc-*`): `body_chart(rows)` — % tłuszczu i % mięśni, 90 dni, pod wykresem wagi; podpis mięśni w kg = `weight * muscle_pct / 100`.
  `<details>` „Historia pomiarów”: wykres wagi z całej historii (średnie tygodniowe) + tabela 30 ostatnich pomiarów (data, waga, tłuszcz %, mięśnie %, woda %, trzewny). Ikony wyłącznie Lucide SVG; styl „Pojemniki” (klasy `dz-*`).

## Testy
`test_nutrition.py`: `bmr_katch`, `trend_rate_kg_week` (malejący, rosnący, < 4 punkty, rozpiętość < 14 dni).
`test_targets.py`: brak składu → Mifflin; pomiar 14 dni → Katch; 15 dni → Mifflin; `kcal_suggestion` (lose za wolno, lose zgodnie, maintain, gain, trend `None`, granica 0,25).
`test_web.py`: karta pokazuje formułę i wiek pomiaru; `POST /profile/{id}/kcal` ustawia override, odrzuca 799 i 6001, 404 dla nieznanej osoby; brak sekcji historii bez pomiarów.

## Kroki
1. Testy czerwone. 2. `nutrition.py`, `store.py` (`latest_body`), `targets.py`. 3. `chart.py`, `web/app.py`, `profiles.html` (+ minimalny CSS). 4. ruff/format/mypy/pytest. 5. Commit.
6. (ja, po `wt land`) Playwright 390/1280 px na lokalnym serwerze dev z fałszywymi danymi.

## Rollback
`git revert`; dane zostają.

## Wykonawca
**`haiku-worker`** (obliczenia i UI według dokładnej specyfikacji; po REJECT → `sonnet-worker`). WORKTREE: `wt new /config/addons/ha-dieta-app dieta-e8e`. Katalog roboczy `dieta/app`; venv jak w PLAN_E8d.
**Pliki:** `dieta/app/src/dieta/{nutrition,targets,store,web/chart,web/app}.py`, `web/templates/profiles.html`, istniejący plik CSS, `tests/{test_nutrition,test_targets,test_web}.py`.
**Komenda testu:** `cd dieta/app && ruff check . && ruff format --check . && mypy && pytest -q`.
**Seamy testów:** `today` w `person_targets` jest już parametrem; pomiary przez `store.add_body_measurements`.
**DONE:** testy z sekcji „Testy” zielone, pełny pytest/ruff/format/mypy czyste.
**NIE RÓB:** migracji i zmian schematu, `zepp*.py`, `body.py`, nowych zależności, emoji/PNG, commitów.
