# POMIAR — kcal i makro ze składników (E3c)

## Kontrakt
- Pytanie: czy kaskada tabela → OFF → LLM daje szacunek kcal/porcję dość dokładny i dość pokrywający katalog?
- GO (z PLAN_E3c): mediana |błędu| ≤ 15%; ≥ 80% przepisów w ±30%; ≥ 80% przepisów bez kcal dostaje szacunek `auto` (wchodzi do planera).
- Dane: kopia bazy add-onu z hosta HA (2026-11-23, wersja 0.1.4, 791 przepisów); 562 przepisów z kcal ze źródła traktowane jak bez kcal, wzorzec = kcal ze źródła.
- Skrypt: scratchpad `e3c_measure.py` (pełny `estimate_nutrition` na kopii, LLM `auto:text`, **bez OFF** — wariant zachowawczy).

## Wynik (przebieg 1, 2026-11-23)
| zbiór | n | mediana \|błędu\| | w ±30% |
|---|---|---|---|
| wszystkie z szacunkiem | 562 | 16,6% | 68% |
| tylko `auto` | 329 (59%) | 11,8% | 81% |
| lidl | 220 | 17,9% | 68% |
| respo | 300 | 14,1% | 73% |
| policzona | 42 | 46,8% | 33% |

Statystyki kroku: 791 przepisów, auto 428, pending 363, składniki: tabela 522, LLM 307, 0 błędów; linie assumed w 133/562 przepisów (24%).
Tylko tabela (bez LLM): pokrycie ≥ 0,85 w 46% przepisów, mediana 21,9%.

## Werdykt: NO-GO wg kontraktu
- Mediana 16,6% > 15%; ±30%: 68% < 80%; do planera wchodzi 59% < 80%.
- Przepisy `auto` są dokładne (11,8%, 81%) — próg pokrycia działa; problemem jest odsetek pending (41%) i źródło „policzona” (46,8%, prawdopodobnie porcje/ilości).
- Nie objęte: OFF (może poprawić składniki), przeliczenie po zatwierdzeniu kolejki.
