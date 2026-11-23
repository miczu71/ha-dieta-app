# POMIAR — pokrycie cen składników po kaskadzie (E6b.0)

## Kontrakt (stały)
- **Pytanie:** jaki odsetek przepisów katalogu ma ≥70% masy składników z ceną po kaskadzie paragony → Frisco×współczynnik (Biedronka po EAN: osobny krok, niżej)?
- **Bramka (z wywiadu 2026-11-23):** ≥50% przepisów z ≥70% masy z ceną → GO; mniej → decyzja usera. Punkt wyjścia (Dieta 0.3.0 na żywo): 13%.
- **Dane (poza repo):** feed Frisco `generatedAt 2026-11-23T18:35` (15 189 produktów, 11 949 wagowych/objętościowych z ceną po filtrze kategorii); kopia bazy Diety `e3c_full_off.db` (791 przepisów, 957 składników); `history.db` z Zakupów (stan 2026-11-21).
- **Komendy:** `python3 -I ~/dev/biedronka-spike/scripts/e6b_frisco_map.py`, `…/e6b_coverage.py` (tylko odczyt).
- **Mapowanie Frisco→składnik w tym przebiegu:** same reguły (wszystkie rdzenie słów składnika w nazwie produktu, nazwa ≤ liczba słów składnika + 4, bez AI); cena składnika = mediana zł/100 g trafień. Pomijane: produkty „na sztuki” (jaja itp.), sól/woda/pieprz.
- **Ograniczenia znane z góry:** pokrycie liczone masą (g z ilości lub `grams_assumed`); „ma cenę” ≠ „cena poprawna”.

## Przebieg 1 (2026-11-23)
- Reguły zmapowały 606 z 957 składników (bez soli/wody/pieprzu).
- **Współczynnik cena z paragonu / cena Frisco** na parach tego samego EAN: **n = 367, mediana 0,75 (Q1 0,61, Q3 0,87)** (paragony w Biedronce/Lidlu są ~25% tańsze).
- Pokrycie masy przepisów: same paragony (dziś) ≥70% = **8%**, mediana 0,12; paragony + Frisco (reguły) ≥70% = **75%**, ≥50% = 87%, mediana 0,91.
- Ocena ręczna próbki (40 najczęstszych + 15 losowych): trafienia podstawowe poprawne (mąka, ryż, masło, miód, oliwa, makaron, boczek), ale reguły łapią też produkty złożone (`cebula`→krakersy, `jabłka`→soki, `nektarynka`→nektar, `wołowina`→nudle). Mediana po wielu trafieniach tłumi część błędów, nie wszystkie.

**Wniosek (nie pomiar):** 75% to górna granica, bo precyzja reguł nie jest zmierzona. Bramka ≥50% zaliczona z zapasem, więc nawet po odsianiu produktów złożonych przez AI wynik powinien zostać powyżej bramki. E6b.2 wymaga warstwy AI + wyboru „produktu podstawowego” (nie mediany wszystkich trafień).

## Otwarte do dokończenia E6b.0
- Próba Biedronki po EAN (EAN-y z Frisco ≤100, ponowienie 112 EAN z OFF, ≤100 „sąsiadów”): wymaga odświeżonego tokenu spike'u (`! python ~/dev/biedronka-spike/spike.py login osoba1`), odstęp ≥4 s.
- Precyzja mapowania na losowej próbce 30 pozycji po warstwie AI (nowy pomiar, bo zmiana wariantu).
