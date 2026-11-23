# E6b — kaskada cen składników (Dieta 0.4.0 + Zakupy 1.12.0)

## Context
Dieta 0.3.0 liczy koszt planu z mediany cen z paragonów; pokrycie słabe (13% przepisów katalogu ma ≥70% masy z ceną).
Spike E6b.0 zawieszony: Biedronka search nie daje EAN, OFF z tagiem sklepu za wąskie, drugi przebieg niemiarodajny (limit/token).
Kandydat: publiczny feed Frisco (15 189 produktów, wszystkie z EAN i ceną, `robots.txt` zaprasza do użytku osobistego).
Propozycja usera (2026-11-23): najświeższe ceny z paragonów → starsze sprawdzane w API Biedronki po EAN → reszta z Frisco
(aktualizacja okresowa) → szukać EAN-ów Biedronki, gdzie się da, by sprawdzać je na żywo.
Źródło prawdy: `/config/addons/ha-dieta-app/docs/ROADMAP.md` (sekcje E6b.0); Zakupy `/config/addons/ha-lidl-app`.

## Decyzje z wywiadu (2026-11-23)
1. **Kaskada ceny składnika (zł/100 g):**
   a. paragon ≤30 dni → `last_unit_price`; gdy ostatni zakup wyraźnie poniżej mediany 30 dni (promocja/kupon) → mediana 30 dni;
   b. EAN znany → cena Biedronki z cache (≤7 dni); promocyjna, gdy `promotion_valid_period` obejmuje tydzień planu, inaczej regularna;
   c. paragon 31–180 dni (Lidl bez EAN itd.) z plakietką „sprzed N dni”;
   d. Frisco × współczynnik („≈ szacunek”); gdy Frisco nie ma składnika, a jest paragon >180 dni → stary paragon.
2. **Architektura (zmiana 2026-11-23, po decyzji usera o wspólnym katalogu cen):** katalog cen w Zakupach (tabela produktów: EAN, kody Lidla, SAP Biedronki, id Frisco, aliasy, gramatura; tabela obserwacji tylko dopisywana: produkt, źródło, sklep, cena, cena regularna, promocja od–do, data). Frisco wczytywane w Zakupach (feed raz w tygodniu nd 03:00 + przycisk, zapis tylko zmian cen, dane w `/data` poza repo, NOTICE); Dieta czyta katalog przez `/api/v1/*`, robi mapowanie składników i kaskadę. Dawniej: Frisco w Diecie;
   Biedronka przez Zakupy — nowy endpoint `/api/v1/shelf` (Bearer jak `/api/v1/purchases`), reuse `shelf_prices.py`
   (`product_price`, `parse_price`, `REQUEST_PAUSE_S`, `shelf_lock`, sklep główny z `/polka`).
3. **Rytm Biedronki:** nocny przebieg w Diecie: najpierw składniki zatwierdzonego planu, potem katalog; ≤~100 EAN/noc,
   odstęp ≥4 s, cache 7 dni, „brak w Biedronce” pamiętany 30 dni; przycisk „Odśwież ceny planu”.
4. **Źródła EAN Biedronki:** EAN-y z paragonów (obie osoby), EAN-y produktów Frisco zmapowanych na składnik, eksport OFF z tagiem Biedronka.
   Ślepy scrape przestrzeni EAN odrzucony (dni zapytań, ryzyko blokady konta); w spike'u mała próba „sąsiadów” (±N od znanych EAN, ≤100 zapytań; <10% trafień → zamykamy).
5. **Frisco:** mapowanie produkt→składnik: filtr kategorią/nazwą → AI `auto:text` (freellmapi) w paczkach → wątpliwe do istniejącej kolejki zatwierdzania;
   cena = mediana zł/100 g wybranych × współczynnik (per kategoria z par EAN Frisco↔paragon/Biedronka, przy małej próbce globalny);
   mapowanie raz, potem co tydzień tylko nowe produkty.
6. **UI:** plakietka źródła + data per składnik („paragon 12.11”, „Biedronka dziś”, „PROMO do 30.11”, „Frisco ≈”);
   pasek kosztu: podział pokrycia X% paragony / Y% Biedronka / Z% szacunek.
7. **Kolejność i wydanie:** najpierw pomiar (E6b.0, bramka), potem kod; etapy = commity, jedno wydanie na koniec: Zakupy 1.12.0 + Dieta 0.4.0 bez pytania.
   Długi z E6 wchodzą do E6b: cache `build_context` per request, „—” zamiast 0,00 zł, komunikaty todo „wysłano X z Y”.

## Etapy
- **E6b.0 pomiar (poza repo, `~/dev/biedronka-spike` + `~/dev/dieta-spike`, tylko odczyt):**
  1. kopia tego planu do `ha-dieta-app/docs/PLAN_E6b.md` + wpis w ROADMAP (decyzje wyżej), commit docs;
  2. pobranie feedu Frisco (1 GET) do `data/e6b/` → mapowanie na składniki katalogu (reguły + `auto:text` przez `secret-run freellmapi`, tylko metryki na wyjściu);
  3. współczynnik Frisco: pary EAN Frisco ↔ paragony (`history.db` kopia) — mediana ilorazu, liczba par per kategoria;
  4. Biedronka: token spike'u odświeża user (`! python ~/dev/biedronka-spike/spike.py login osoba1`); próba ≤100 EAN z Frisco (zmapowanych) + ponowienie 112 EAN z OFF + ≤100 „sąsiadów”, odstęp ≥4 s;
  5. pokrycie masy katalogu po kaskadzie (skrypt jak `cov.py` z E6-release, na kopii bazy Diety) vs 13%;
  6. wynik w `docs/POMIAR_ceny.md` (kontrakt skillem `pomiar`). **Bramka:** ≥50% przepisów z ≥70% masy z ceną → GO; mniej → decyzja usera.
- **E6b.1 Zakupy (katalog cen + `/api/v1/shelf` jako jego widok, bez UI, migracja schematu z kopią `.pre-*`; ingest paragonów/Biedronki/Frisco):** `/api/v1/shelf` (POST listy EAN → zlecenie w tle z blokadą, GET wyników z cache `shelf_prices`, statusy ok/brak/błąd), testy, bez migracji jeśli tabela wystarcza.
- **E6b.2 Dieta — mapowanie Frisco→składnik:** (feed w katalogu Zakupów), mapowanie (reguły + AI + kolejka), współczynnik; migracja v8.
- **E6b.3 Dieta — kaskada:** `pricing.py` (źródło + data per cena), nocna kolejka EAN do Zakupów, cache 7/30 dni, promocje w tygodniu planu, tryb „taniej”.
- **E6b.4 UI + długi E6:** plakietki, podział pokrycia, „—”, cache `build_context`, komunikaty todo.
- **E6b-release:** simplify → code-review + silent-failure-hunter + pr-test-analyzer + security → release Zakupy 1.12.0, Dieta 0.4.0 → pomiar pokrycia na żywo.
Każdy etap kodu: `docs/PLAN_E6b_<n>.md` z sekcją Wykonawca, `tdd`, `addon-conventions`, worker w worktree (`wt new`) + verifier, `stage-end`.

## Rollback
E6b.0: tylko pliki w `~/dev/*/data/e6b/` i docs (git revert). Kod: `git revert` commitów etapu; migracja v8 tylko dodaje tabele;
wydanie: poprzednie tagi Dieta v0.3.0 / Zakupy v1.11.0.

## Weryfikacja
E6b.0: liczby w `POMIAR_ceny.md` (pokrycie, współczynnik z liczbą par, trafienia Biedronki per źródło EAN).
Kod: `uv run --extra dev pytest -q` (Dieta, Zakupy osobno), ruff/mypy; na żywo: pokrycie po sync, strony `/plan /lista` 200, log bez błędów, plakietki w Playwright 390/1280.

## Ryzyka
Prywatne API Biedronki (limit/blokada konta) → limity, cache, odstępy; Frisco może zmienić warunki feedu → cena „≈” znika, kaskada działa dalej;
współczynnik z małej próbki — raportowany z liczbą par.
