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

## Przebieg 2 (2026-11-23, po W4: nagłówki/szum, gramy dla jednostek bez przelicznika, sufity ilości, seed +aliasy)
| zbiór | n | mediana \|błędu\| | w ±30% |
|---|---|---|---|
| wszystkie z szacunkiem | 562 | 16,2% | 69% |
| tylko `auto` | 346 (62%) | 12,6% | 79% |
Wciąż NO-GO (cel: ≤15% / ≥80% / auto ≥80%). Poprawa niewielka (auto 59→62%).
Pozostałe `pending` (216): 149 z pokryciem < 0,85 (pokrycie 0,7–0,85: mediana błędu 34%, w ±30% 39% — realnie słabe), 67 z pokryciem ≥ 0,85 i **medianą błędu 8,6%**, zablokowane regułą „brak składnika pending”.
Hipoteza do sprawdzenia: zniesienie blokady „składnik pending” przy pokryciu ≥ 0,85 daje ok. +12 pp `auto` (≈74%); reszta wymaga lepszego pokrycia (OFF, seed) lub niższego progu.
