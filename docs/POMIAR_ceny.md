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

## Przebieg 2 (2026-11-23, wieczór) — próba Biedronki po EAN: NIEROZSTRZYGNIĘTA (HTTP 429)
- Próba 101 EAN (kanarek + po jednym EAN-ie Frisco na składnik, odstęp 4 s): **101/101 błędów**, łącznie z kanarkiem (znany dobry EAN). Diagnoza jednym zapytaniem na 3 sklepy (6308, 3889, 6715): wszystkie **HTTP 429**. Logowanie i `products/most-purchased` działają (token OK, refresh obrócony), limit dotyczy tylko `products/price`.
- Wniosek (nie pomiar): limit utrzymuje się ≥45 min po poprzednim przebiegu (1 zapytanie/s o 18:08) i nie zniknął przy 4 s odstępu. Kolejne próby w tym stanie nic nie dadzą i mogą go przedłużać → **nie ponawiam dziś**. Ponowna próba: jutro, najpierw 1 zapytanie, potem partie ≤20 co ≥10 s, i dopiero wtedy ocena, czy dzienny limit jest niższy od planowanych ~100 EAN/noc.
- Ryzyko dla Zakupów: jeśli konto jest to samo, poranny przebieg cen półkowych (06:30) też może dostawać 429 — sprawdzić w logu Zakupów (`shelf/error`) przed E6b.1.
- Bramka offline (≥50% przepisów z ≥70% masy z ceną) zaliczona bez Biedronki (75% z Frisco×0,75). Warstwa „Biedronka po EAN” kaskady zostaje, ale jej limity są nieznane → w E6b.3 domyślnie mały budżet (np. ≤30 EAN/noc), konfigurowalny.

## Przebieg 3 (2026-11-24) — zatwierdzony plan tygodnia 2026-11-23, Dieta 0.4.1 na żywo
- **Kontrakt:** udział masy składników planu z ceną po kaskadzie (paragony → półka → Frisco×wsp.); cel ≥70% masy. Dane: wyjście UI (`/lista`, `/plan` = `coverage_by_source`), plan wygenerowany i zatwierdzony 2026-11-24 po wpisaniu profili 2 osób. Tylko odczyt GET.
- **Wynik (z UI):** Plan — koszt tygodnia 496,85 zł, pokrycie cen **90%** (paragony 73% · szacunek 17%). Lista — do kupienia 312,75 zł, pokrycie **87%** (paragony 59% · szacunek 28%). Cel ≥70% zaliczony z zapasem (+17 pp na liście).
- Półka Biedronki w kaskadzie jeszcze nie pracuje: `shelf_item` = 0 wierszy (kolejka EAN ruszy po zatwierdzeniu planu, nocą), więc „szacunek” to Frisco×współczynnik.
- **Zastrzeżenie (obserwacja, nie pomiar precyzji):** „ma cenę” ≠ „cena poprawna”. Podejrzane pozycje listy, do sprawdzenia przy kolejnym pomiarze: Seler naciowy 594 g → 83,75 zł (≈14,8 szt.), Pomidory 1945 g → 81,93 zł, Jajka 214,5 g → 44,81 zł, Filet z kurczaka 1202,5 g → 44,47 zł (wszystko „paragon/stary paragon”, wygląda na niedopasowaną jednostkę/ilość opakowania); Oliwki 111 g → 11,74 zł, Brokuł 410 g → 11,89 zł (szacunek). Sumy kosztów mogą być zawyżone — ręczna ocena 10 największych pozycji NIE została zrobiona na paragonach.
