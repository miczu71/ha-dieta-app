# Dieta + przepisy (Cookidoo) połączone z add-onem Zakupy

## Context
Pomysł usera (2026-11-21): przepisy wspierające dietę, połączone z add-onem Zakupy (Lidl + Biedronka). Wywiad:
- **dwie osoby, różne cele** (wspólne posiłki, inne porcje i kcal),
- przepisy: **Cookidoo + własne**,
- **osobny add-on** (Zakupy udostępnia mu ceny/promocje przez wewnętrzne API),
- **propozycja tygodnia + zatwierdzenie** (dopiero potem zapis do kalendarza Cookidoo i lista zakupów),
- **plan + odhaczanie posiłków** (bez pełnego dziennika jak Fitatu),
- cel kcal **liczony z profilu** (Mifflin-St Jeor × PAL ± deficyt; w HA nie ma wagi łazienkowej → wpis ręczny),
- **algorytm układa plan, AI tylko mapuje składniki na produkty i pisze uzasadnienia**,
- związek z Zakupami: lista zakupów z planu, koszt planu i promocje, kcal kupowanych produktów.

Doprecyzowanie usera (runda 3):
- **źródłem są propozycje zewnętrzne**; własne przepisy to tylko opcja (późny etap),
- **w opcjach wybór posiłków, które add-on planuje** (np. tylko obiad i kolacja); posiłki spoza planu zjadają stały budżet kcal,
- **wymiana przepisu jednym kliknięciem**: losowy pasujący albo z polubionych.

Doprecyzowanie usera (runda 4) — **trzon:** add-on analizuje paragony (Zakupy), wie, co kupujemy, i **przede wszystkim
proponuje dania z tych składników**; inne przepisy też się pojawiają, ale z niższym priorytetem.

**Jak to działa (projekt trzonu):**
- **Słownik składników:** każdy produkt z paragonów (np. „Filet z indyka XXL”, „Pomid.kroj.400g”) → składnik ogólny
  („pierś z indyka”, „pomidory krojone”). Najpierw reguły i nazwy, potem AI `auto:text` w paczkach, wątpliwe do zatwierdzenia
  w UI; mapowanie zapamiętane. Ten sam słownik dla składników przepisów („400 g pomidorów z puszki” → „pomidory krojone”).
- **Profil zakupowy:** dla składnika częstość zakupów, wydatek, ostatni zakup (12 mies., ważone świeżością), wspólny dla domu.
  Z ostatnich dni: „prawdopodobnie w domu” (krótko dla świeżych, dłużej dla suchych/puszek).
- **Dopasowanie przepisu:** `znajomość` = udział składników przepisu (bez soli/przypraw/wody), które kupujecie regularnie;
  premia za „w domu” i „w promocji”. Na karcie: „5/6 składników kupujecie, brakuje: tahini”.
- **Planer:** w każdym slocie domyślnie przepisy ze `znajomość` ≥ 70%; suwak „odkrywanie” w opcjach (np. 1–2 nowe dania
  tygodniowo); wymiana 🎲 losuje też najpierw spośród „znajomych”.

## Propozycja funkcji (do akceptacji przed kodem)
0. **Trzon: słownik składników + profil zakupowy z paragonów + dopasowanie przepisów** (opis wyżej).
**Rdzeń (MVP, E1–E4):**
1. Profile 2 osób: cel (redukcja/utrzymanie/masa), tempo, aktywność → kcal i makro na dzień; ręczny wpis wagi i wykres.
2. **Sloty posiłków w opcjach:** śniadanie / II śniadanie / obiad / podwieczorek / kolacja — każdy włącz/wyłącz, udział
   w kcal dnia (np. obiad 35%), wspólny dla obojga albo osobny; wyłączone sloty = rezerwa kcal odejmowana od celu.
3. **Katalog z zewnętrznych źródeł:** odświeżany w tle (Cookidoo, Kuchnia Lidla, portale), kcal/makro na porcję, czas,
   trudność, kategoria posiłku → tylko przepisy pasujące do slotu.
4. **Plan tygodnia:** propozycja na włączone sloty, wspólne posiłki ze skalowaniem porcji per osoba, zatwierdzenie.
5. **Wymiana jednym kliknięciem** na karcie posiłku: 🎲 „losowy” (ten sam slot, kcal ±15%, bez powtórki w tygodniu)
   i ♥ „z polubionych”; wymiana od razu przelicza bilans dnia i listę zakupów; „cofnij” po wymianie.
6. **Polubione / odrzucone:** serduszko i „nie proponuj więcej”; polubione mają wagę w doborze, odrzucone nie wracają.
7. Odhaczanie zjedzone/pominięte, bilans dnia i tygodnia na osobę, poranny push z planem dnia.

**Wartość z Zakupów (E5–E6):**
8. Lista zakupów z zatwierdzonego planu (scalona, minus kupione w ostatnich dniach) → todo HA / Cookidoo.
9. Koszt tygodnia z cen Lidl/Biedronka (z pokryciem %), tryb „taniej”: preferuj przepisy ze składnikami w promocji/kuponie.
10. Znacznik na karcie przepisu: „3 składniki w promocji w tym tygodniu”.

**Rozwój (później, każdy osobno do decyzji):**
11. Filtry diety: wykluczone składniki/alergeny, wege dni, wysokie białko, maks. czas przygotowania w dni robocze.
12. Gotowanie na 2 dni (obiad z wczoraj jako dzisiejszy lunch) i resztki składników → przepisy je zużywające.
13. Tryb Thermomix: wysyłka przepisu na urządzenie przez kalendarz Cookidoo „na dziś”.
14. Własne przepisy i import z linku (opcja).
15. Kcal kupowanych produktów z Open Food Facts i statystyka koszyka (w Zakupach).
16. Trend wagi vs plan: korekta celu kcal co 2 tygodnie, gdy waga stoi.
17. Waga/aktywność z Health Connect/Garmin, spiżarnia z Grocy.
18. Asystent głosowy HA: „co dziś na obiad?”, „wymień kolację”.

## Odpowiedzi na pytania (stan wiedzy, do potwierdzenia w E0)
- **Cookidoo:** brak oficjalnego API. Jest nieoficjalna biblioteka `cookidoo-api` (miaucl, 0.19.0, aiohttp, login kontem):
  `search_recipes` (filtry: kategorie, składniki, czas, porcje), `get_recipe_details`, kolekcje (własne/zarządzane),
  kalendarz tygodnia (`add_recipes_to_calendar`, `get_recipes_in_calendar_week`), lista zakupów (składniki przepisów +
  dodatkowe pozycje), własne przepisy. Integracja HA `cookidoo` (od 2025.1) daje tylko 2 listy todo, kalendarz i przycisk
  czyszczenia — w HA nie jest zainstalowana (sprawdzone `ha_search`). Ryzyko: API nieoficjalne, może się zmienić.
- **Kcal przepisów:** strony przepisów Cookidoo pokazują wartości odżywcze na porcję (kcal, białko, węgl., tłuszcz) —
  biblioteka ich nie nazywa; E0 sprawdza, czy są w JSON `get_recipe_details` albo w JSON-LD strony. Własne przepisy:
  kcal ze składników (Open Food Facts po EAN / nazwie, gramatura) albo wpis ręczny.
- **Kcal produktów:** Open Food Facts po EAN (spike E32 w Zakupach: rekord OFF dla 77% wydatków Lidla z EAN-em, 56%
  Biedronki); paragony Lidla od 03.2026 nie mają EAN (602 produkty) → dopasowanie po nazwie/AI z zatwierdzeniem albo brak.
  Licencja ODbL → atrybucja w NOTICE.
- **Układanie planu:** cel dzienny na osobę → dobór przepisów na tydzień (problem plecakowy z tolerancją ±10% kcal,
  makro w przedziałach, różnorodność: bez powtórki w N dniach, koszt z cen Zakupów, bonus za składniki w promocji/kuponie);
  wspólny obiad = jedna potrawa, porcje skalowane do celu każdej osoby (np. 1,0 i 1,4 porcji); śniadania/kolacje osobno.
  Algorytm deterministyczny (zachłanny + lokalne poprawki), testowalny.
- **Jak działają podobne:** Eat This Much (generator planu z kcal/makro — najbliższy wzorzec), Fitatu/Yazio/MyFitnessPal
  (dziennik + baza produktów + skaner EAN), Mealime/Cookidoo „Mój tydzień” (planer + lista zakupów, bez celu kcal),
  Mealie/Tandoor (self-hosted przepisy, planer, wartości odżywcze, integracja HA), Grocy (stan spiżarni → „co jest w domu”).
- **Dane w tle:** Cookidoo (nowe przepisy z kolekcji, kalendarz, lista zakupów), Zakupy (ceny, promocje od dziś, kupony,
  paragony = co kupiono), OFF (wartości po EAN, cache lokalny), freellmapi `auto:text` (mapowanie składnik→produkt),
  HA (powiadomienia `notify.family`, todo). Później opcjonalnie: waga/aktywność z Health Connect/Garmin, Grocy.

## Źródła przepisów i danych — lista (prośba usera 2026-11-21: „nie tylko Cookidoo”)
Status: ✔ potwierdzone w sieci, ? do sprawdzenia w E0. Dostęp = jak add-on pobierze dane.

| Źródło | Rodzaj | PL | Kcal/makro | Dostęp | Lista zakupów / plan | Uwagi |
|---|---|---|---|---|---|---|
| **Cookidoo** | przepisy Thermomix | ✔ | ? na porcję (strona) | `cookidoo-api` (nieoficj.) ✔ | ✔ lista + kalendarz | subskrypcja; główne źródło |
| **Kuchnia Lidla** (kuchnialidla.pl) | przepisy sieci | ✔ | ? | strona/JSON-LD ? | — | składniki = produkty Lidla → łatwe mapowanie na ceny Zakupów |
| **Przepisy Biedronki** | przepisy sieci | ? | ? | strona ? | — | j.w. dla Biedronki |
| **Kwestia Smaku, Kuchnia Domowa** | portale | ✔ | zwykle brak | `recipe-scrapers` ✔ (obsługiwane) | — | kcal liczone ze składników |
| **Policzona Szama** | blog fit, przepisy z kcal | ✔ | ✔ zwykle (do potwierdzenia) | `recipe-scrapers`/JSON-LD ? | — | link usera to skrót Google `share.google/J8RJswGS8zY2sdmJC` (nie rozwiązuje się z CLI) — domenę ustalić w E0; priorytet: fit, kcal na porcję |
| **Centrum Respo** (centrumrespo.pl/przepisy/) | przepisy dietetyczne | ✔ | ✔ zwykle (do potwierdzenia) | strona/JSON-LD ? | — | dodane 2026-11-22 |
| **Healthy Omnomnom** (healthyomnomnom.pl/przepisy/) | blog fit | ✔ | ✔ zwykle (do potwierdzenia) | strona/JSON-LD ? | — | dodane 2026-11-22 |
| **Codziennie Fit** (codzienniefit.pl/category/dieta/przepisy) | blog fit | ✔ | ✔ zwykle (do potwierdzenia) | strona/JSON-LD ? | — | dodane 2026-11-22 |
| **Dowolna strona z przepisem** | import URL | ✔ | jeśli JSON-LD `nutrition` | `recipe-scrapers` (tryb ogólny schema.org) | — | „wklej link” → własny przepis |
| **Mealie / Tandoor** | self-hosted menedżer przepisów | ✔ UI | ✔ pola nutrition | REST API, integracja HA (Mealie) | ✔ planer + listy | alternatywa: magazyn przepisów zamiast własnej bazy |
| **Fitatu** | dziennik + przepisy | ✔ | ✔ duża PL baza | brak publicznego API | ✔ (app) | wzorzec UX, nie źródło |
| **Edamam** | API przepisów/żywienia + Meal Planner | ang. | ✔ | API, darmowy plan dev (limity ?) | ✔ generator planu | dane anglojęzyczne |
| **Spoonacular** | API przepisów, plan, lista | ang. | ✔ | API, darmowy limit ~150/dzień (?) | ✔ | j.w. |
| **TheMealDB** | otwarta baza przepisów | ang. | ✗ | darmowe API | — | mała, bez kcal — słaba |
| **Open Food Facts** | baza produktów | ✔ | ✔ /100 g | API po EAN, ODbL ✔ | — | kcal produktów z paragonów (77% wydatków Lidla z EAN) |
| **FatSecret Platform** | baza żywności | ✔ (strona PL) | ✔ | API, darmowy plan z atrybucją (zbiór USA) | — | uzupełnienie OFF, raczej nie |
| **Tabele IŻŻ / NIZP** | polskie tabele składu | ✔ | ✔ produkty podstawowe | brak API (książka/PDF) | — | ręcznie wybrane wartości dla warzyw/mięsa bez EAN |
| **Grocy** | spiżarnia | ✔ UI | ✔ per produkt | REST, integracja HA | ✔ plan + lista | „co jest w domu” — opcja na później |

Rekomendacja do wywiadu E2: Cookidoo + Kuchnia Lidla + import dowolnego URL (`recipe-scrapers`) + własne; kcal brakujące
liczone ze składników przez OFF + mała tabela produktów podstawowych. API anglojęzyczne (Edamam/Spoonacular) i Mealie —
tylko jeśli E0 pokaże, że polskie źródła nie dają kcal.

## Roadmapa (zapis do `docs/ROADMAP.md` nowego repo przy starcie E1; E0 w `~/dev/dieta-spike`)
Każdy etap: wywiad szczegółów → `docs/PLAN_E<n>.md` (z sekcją Wykonawca) → TDD → verifier → commit. Wydania: 0.1.0 po E3,
0.2.0 po E5, 0.3.0 po E7 (łączone wg zasad); Zakupy — osobne wydanie po E2.

- **E0 spike (bez kodu add-onu, bramka):** login `cookidoo-api` kontem usera (dane logowania wpisuje user do `~/.secrets/cookidoo.env`,
  skrypt uruchamia `! secret-run cookidoo -- …`); sprawdzić: search PL, szczegóły przepisu (kcal/makro na porcję? liczba
  porcji, składniki z ilościami), kalendarz i listę zakupów (tylko odczyt + 1 testowy wpis za zgodą), stabilność tokenu.
  To samo dla Kuchni Lidla, przepisów Biedronki i 3 portali przez `recipe-scrapers` (ile ma kcal w JSON-LD, jakość składników).
  **Trzon:** na kopii `history.db` z Zakupów (tylko odczyt, poza repo) — ile produktów z 12 mies. AI mapuje na składnik
  ogólny (próbka 100 najczęstszych, ocena ręczna) i ile przepisów z próbki Cookidoo ma `znajomość` ≥ 70%.
  **Bramka:** brak kcal w API i stronie → plan B: kcal ze składników przez OFF (gorsza jakość) — decyzja usera;
  `znajomość` ≥ 70% dla < 10% przepisów → obniżyć próg / poszerzyć źródła — decyzja usera.
  Rollback: usunąć `~/dev/dieta-spike`.
- **E1 fundament:** nowe repo `ha-dieta-app` (wzorzec struktury i CI z `ha-lidl-app`: `lidl/app/pyproject.toml`, testy,
  design `app.css`/DESIGN.md), profile 2 osób (dane w `/data`, imiona tylko w opcjach — hook prywatności), kalkulator
  BMR/TDEE/cel + makro, ręczny wpis wagi z wykresem. Wartość: cele kcal dla obojga.
- **E2 most do Zakupów + słownik składników (trzon, część 1):** w Zakupach wewnętrzne API tylko-odczyt w sieci Supervisora
  (produkty z zakupami: kod, nazwy, częstość, wydatek, ostatni zakup; ceny; promocje/kupony aktywne) — osobne wydanie Zakupów;
  w Diecie: słownik składników (reguły → AI `auto:text` w paczkach → zatwierdzanie wątpliwych), profil zakupowy domu,
  ekran „Co kupujemy” (top składniki, „prawdopodobnie w domu”).
- **E3 katalog zewnętrzny + dopasowanie (trzon, część 2; funkcje 3, 6):** konektory źródeł (domyślnie Cookidoo + Kuchnia
  Lidla), katalog z kcal/makro na porcję i kategorią posiłku, składniki przepisów przez ten sam słownik, `znajomość` na
  karcie („5/6 kupujecie”), sortowanie domyślnie po znajomości, polubione/odrzucone. Wydanie 0.1.0.
- **E4 planer (funkcje 2, 4, 5):** sloty posiłków w opcjach, algorytm tygodnia (najpierw znajome przepisy, suwak
  „odkrywanie”, wspólne posiłki ze skalowaniem porcji, tolerancje kcal, różnorodność, waga polubionych), wymiana jednym
  kliknięciem (losowy znajomy/polubiony, cofnij), zatwierdzenie → kalendarz Cookidoo.
- **E5 realizacja:** odhaczanie zjedzone/pominięte/zamiennik, bilans dnia i tygodnia na osobę, poranny push z planem dnia.
  Wydanie 0.2.0.
- **E6 koszt i lista:** koszt planu z cen Zakupów, premia za promocje/„w domu”, lista zakupów z zatwierdzonego planu
  (tylko brakujące składniki) → todo HA + dodatkowe pozycje Cookidoo.
- **E7 kcal zakupów (w Zakupach):** wartości z OFF dla produktów z EAN (cache, ODbL), statystyka „kcal i makro koszyka”.
  Wydanie 0.3.0 Diety + wydanie Zakupów.
- **E8+ rozwój:** funkcje 11–18 jako osobne etapy, kolejność do wyboru po E7 (własne przepisy/import linku = opcja).

## Ryzyka
- Nieoficjalne API Cookidoo (zmiany, blokada konta przy nadużyciu) → limity zapytań, cache, mała częstotliwość.
- Kcal = wartości szacunkowe; aplikacja nie jest poradą medyczną (komunikat w UI).
- Mapowanie składników na produkty Lidla bez EAN — niepełne; koszt planu jako szacunek z pokryciem %.

## Weryfikacja
- E0: raport spike'u z liczbami (ile przepisów ma kcal, czy kalendarz działa) w `docs/PLAN_E0.md`.
- Każdy etap: `uv run pytest`, ruff, mypy; Playwright 390/1280 px na danych demo; na żywo po wydaniu (panel ingress, log add-onu).
- Algorytm: testy jednostkowe na zamrożonym katalogu (suma kcal w tolerancji dla obu osób, brak powtórek, determinizm).

## Pierwszy krok po akceptacji
E0 spike: przygotuję skrypt sondy w `~/dev/dieta-spike` i instrukcję dodania loginu Cookidoo do `~/.secrets/cookidoo.env`
(robi user); sonda tylko do odczytu.

## Decyzje usera 2026-11-21 (runda 5) — NADPISUJĄ zapisy wyżej
- **Cookidoo NIE jest źródłem głównym.** Thermomix jest, ale gotujecie głównie tradycyjnie → Cookidoo to jeden z wielu
  konektorów (opcjonalny), nie warunek bramki E0. Kalendarz/lista Cookidoo jako cel zapisu planu = opcja późna.
- **Architektura źródeł = wiele agregatorów za jednym interfejsem** (`RecipeSource`: lista/wyszukiwanie → szczegóły:
  nazwa, porcje, składniki `ilość jednostka nazwa`, kcal jeśli są, kategoria, czas). Konektory: przepisy.lidl.pl (potwierdzony,
  JSON-LD, bez kcal), portale PL przez `recipe-scrapers` (Kwestia Smaku, Kuchnia Domowa — do sprawdzenia, Aniagotuje/Smaker/
  Cookpad/Przepisy.pl — do sprawdzenia, czy mają JSON-LD i kcal), Biedronka (parser HTML), Cookidoo (opcja), Mealie/Tandoor
  jako zewnętrzny magazyn (opcja, REST). Kcal brakujące liczone ze składników (OFF + tabele podstawowych produktów).
  Spike ma sprawdzić każdy portal: ile przepisów w próbce ma JSON-LD `Recipe`, kcal, ustrukturyzowane składniki.
- **Cel funkcjonalny: posiłki wielodniowe (meal prep / gotowanie na kilka dni).** Plan nie jest „1 przepis = 1 posiłek”,
  tylko **sesje gotowania**: przepis gotowany raz na N porcji (2–5 dni), rozdzielany na kolejne dni/sloty; porcje skalowane do
  kcal każdej osoby; zamrażanie/odgrzewanie (flaga „nadaje się do przechowywania”, trwałość w dniach); lista zakupów z sesji;
  suwak „ile dni z jednego gotowania”. Planer (E4) układa tydzień jako 2–3 sesje + świeże dodatki, a wymiana jednym
  kliknięciem wymienia całą sesję lub jeden dzień (reszta porcji idzie na inny dzień).
- Zmiana kolejności: bramka E0 dotyczy teraz portali PL (kcal/składniki), nie Cookidoo; sonda Cookidoo przesunięta na koniec
  i opcjonalna. Przepisy „na kilka dni” wymagają w katalogu pól: `porcje`, `przechowywanie_dni`, `mrozenie` (z kategorii/AI).

## Dodatek 2026-11-22 — 4 portale fit od usera
Policzona Szama (domena do ustalenia ze skrótu `share.google/J8RJswGS8zY2sdmJC`), centrumrespo.pl/przepisy/,
healthyomnomnom.pl/przepisy/, codzienniefit.pl/category/dieta/przepisy — konektory `RecipeSource` obok Lidla i portali PL.
Są to strony dietetyczne, więc spodziewamy się kcal/makro na porcję → mogą zdjąć potrzebę planu B (kcal z OFF). Sonda E0
sprawdza je jako pierwsze (po 10–20 przepisów: JSON-LD `Recipe`, `nutrition`, porcje, składniki, nadawanie się do przechowywania).

## Decyzja o stylu 2026-11-22
Wybrany kierunek **Pojemniki** (jasny i ciemny motyw): sesja gotowania = rząd pojemników, wieczko w kolorze dnia; Outfit + DM Mono, akcent brokuł.
Własna tożsamość, niezależna od Zakupów. Porównanie: https://claude.ai/artifact/CzdmceqxDKUAysZnmMLiMX, Design System: https://claude.ai/artifact/J9s68oHE5MCCNn8JrXPGCA
(źródła w `design-system/project/`; po E1 do przeniesienia do repo `ha-dieta-app`). Kod add-onu będzie źródłem prawdy.

## E0 ZAKOŃCZONY 2026-11-22 (bramka: GO)
Raport: `docs/PLAN_E0.md` (RAPORT KOŃCOWY), pomiar `docs/POMIAR_znajomosc.md`. Źródła na E3: Centrum Respo, przepisy.lidl.pl, Aniagotuje (JSON-LD),
Policzona Szama i Healthy Omnomnom (parser HTML), YT Policzonej Szamy przez RSS (pełna historia = Data API, później). Kwestia Smaku/Smaker/Kuchnia
Domowa tylko składniki. Przepisy.pl, Cookpad, Codziennie Fit pominięte. Cookidoo opcjonalne (sonda czeka na login). Następny: E1 (wywiad zakończony 2026-11-22, plan w `~/.claude/plans/zaczynajmy-z-roadmapa-diety-*.md`).

## E1 ZAKOŃCZONY 2026-11-22
Commity: `c33a6c3` szkielet, `4d42329` styl, `67fff5a` W1, `6dc000b` W2, `3a66b3f` W3. 38 testów, ruff/mypy czyste, docker build + smoke OK, CI zielone, Playwright 390/1280 px (oba motywy). Bez wydania (0.1.0 po E3). Następny: E2.

## E2 KOD ZAKOŃCZONY 2026-11-22 (wydanie Zakupów 1.10.0 przed nami)
Commity: Dieta `ba930ef` plan, `d30a009` W2 (klient, snapshot, słownik, profil), `f879da4` W3 (ekran Co kupujemy + kolejka, nocny sync); Zakupy `df9f1b6` W1 (`GET /api/v1/purchases`, token Bearer).
Weryfikacja: Dieta 88 testów, ruff/mypy czyste; Zakupy 862 testy (pełny zestaw), ruff/mypy czyste; verifier W1/W3 APPROVE, W2 po REJECT (seed ignorowany przez .gitignore → seed w `dieta/seed/` i bez wydatków/dat); Playwright 390 px jasny i 1280 px ciemny bez przewijania poziomego.
Do zrobienia przed E3: wydanie Zakupów 1.10.0 (`simplify` → `code-review` → `release`), potem user ustawia ten sam token w `api_token` (Zakupy) i `zakupy_token` + `zakupy_url=http://f0987e0f-lidl:8099` (Dieta) i sprawdzamy sync na żywo. Dieta bez wydania (0.1.0 po E3).
Otwarte (nieblokujące): seed bez trwałości → składnik trafia do kolejki „bez trwałości”; lista reguł `dictionary.py` (~24 słów) do przejrzenia; brak testu `_nightly`; build obrazu Dockera z `dieta/seed/*.json` niesprawdzony (CI przy pushu).

## Wydanie Zakupów 1.10.0 — 2026-11-22
Wydane (non-draft, tag `v1.10.0`, commit `e720540`), zainstalowane przez Supervisor, kontener działa. Przed wydaniem: simplify, code-review, silent-failure-hunter, pr-test-analyzer, security review (bez blokujących; poprawione: `spend` po rabacie, strip `api_token`, log odrzuconych prób, testy granic okna). Pełny zestaw: 865 testów. Pubguard: 0 flag.
Na żywo (przez proxy Ingress, `api_token` puste): `/api/v1/purchases` → 503 „API wyłączone”, inna trasa spoza Ingressu → 403 — zgodnie z projektem.
Czeka na usera: ten sam długi losowy token (≥32 znaki) w `api_token` (Zakupy) i `zakupy_token` w Diecie, `zakupy_url=http://f0987e0f-lidl:8099`; potem test 200 i sync. Dalej: E3 (wywiad `grill-me`).

## E3 PLAN GOTOWY 2026-11-22
Wywiad zakończony, plan: `docs/PLAN_E3.md`. Źródła 0.1.0: Respo, Lidl, Policzona Szama (blog); nocny crawl + „Odśwież teraz”; metadane z reguł + AI; brak kcal = plakietka i poza planerem;
sort po znajomości, próg 70% w UI; polubione wspólne. Paczki: W1 konektory ‖ W2 migracja v3/crawl/znajomość (sonnet, ryzyko), potem W3 ekran Katalog. Po E3: simplify → code-review → release 0.1.0 + instalacja.

## E3 KOD ZAKOŃCZONY 2026-11-22 (przed wydaniem 0.1.0)
Commity: `1967692` W1 konektory (Respo, Lidl, Policzona), `bb09e19` W2 (migracja v3, katalog, crawl, znajomość), `3bcbef4` W3 (ekran Katalog, fetcher, nocny crawl, kolejka metadanych).
Weryfikacja: 137 testów, ruff/mypy czyste; verifier W1 po REJECT (kcal partii Policzonej → podział przez porcje / None), W2 i W3 APPROVE; Playwright 390 px jasny i 1280 px ciemny bez przewijania poziomego.
Nieblokujące do raportu wydania: fetcher bez allowlisty hostów po redirectach, `HTTPException` nie łapany jako `FetchError`, brak testów locka runnera / `_nightly` / limitu 5 MB; Policzona rozpoznaje tylko frazę partii „z N g … będzie mieć N kcal”.
Pomiar znajomości (Policzona, odsetek ≥70/60/50%) — wymaga żywego crawla z tokenem Zakupów, po instalacji 0.1.0. Dalej: simplify → code-review → release 0.1.0.

## Wydanie 0.1.0 — 2026-11-22
Tag `v0.1.0` (non-draft), CI zielone (test + docker amd64/arm64), 159 testów, ruff/mypy czyste. Przed wydaniem: simplify, code-review, silent-failure-hunter, pr-test-analyzer, security review; blokujące naprawione (awaria LLM → pending, skip bez TTL, pusta sitemapa, testy _nightly/lock/5 MB/redirect). pubguard: flagi tylko w fixturach HTML stron źródłowych; usunięty numer telefonu firmy z 2 fixtur.
Dług (nieblokujące): `_map_new` przy awarii LLM nadal pisze pending; allowlista hostów po redirectach (www.); scheduler poza lifespan; typ wpisu zamiast prefiksu `ing:`; wspólna kaskada mapowania; kolumna has_nutrition wyliczalna; cache znajomości.
Czeka na usera: rejestracja repo w sklepie HA, instalacja, `zakupy_token` (`! secret-run`) i `zakupy_url=http://f0987e0f-lidl:8099`; potem pomiar znajomości (Policzona: ≥70/60/50
## Wydanie 0.1.0 — 2026-11-22
Tag `v0.1.0` (non-draft), CI zielone (test + docker amd64/arm64), 159 testów, ruff/mypy czyste. Przed wydaniem: simplify, code-review, silent-failure-hunter, pr-test-analyzer, security review; blokujące naprawione (awaria LLM → pending, skip bez TTL, pusta sitemapa, testy _nightly/lock/5 MB/redirect). pubguard: flagi tylko w fixturach HTML stron źródłowych; usunięty numer telefonu firmy z 2 fixtur.
Dług (nieblokujące): `_map_new` przy awarii LLM nadal pisze pending; SameHostRedirect odrzuca zmianę subdomeny (www.); scheduler poza lifespan; `ing:` jako prefiks w `mapping`; zduplikowana kaskada mapowania; kolumna has_nutrition wyliczalna.
Czeka na usera: rejestracja repo w sklepie HA i instalacja, `zakupy_token` (`! secret-run`) i `zakupy_url=http://f0987e0f-lidl:8099`; potem pomiar znajomości (Policzona: ≥70/60/50%) na żywym crawlu. Następny: E4 planer (wywiad `grill-me`).

## Instalacja 0.1.0 i pierwszy crawl — 2026-11-22
Zainstalowane (Supervisor, `b969c720_dieta`), `zakupy_url`/`zakupy_token` i `llm_url`/`llm_key` (freellmapi, `auto:text`) ustawione skryptem `secret-run` (WebSocket `supervisor/api`, bez wypisywania wartości). Sync zakupów: 133 składniki.
Pierwszy crawl (bez LLM w trakcie): Respo 150, Lidl 149 (1× IncompleteRead), Policzona 2 przepisy + 43× HTTP 429. Katalog 301 przepisów, 79 z plakietką „brak kcal”.
**Pomiar znajomości — NIEMIARODAJNY:** ≥70% = 0, ≥60% = 2, ≥50% = 7 (z 301). Składniki przepisów leżały w `pending` (567 do zatwierdzenia), więc liczą się jako nieznane. Powtórzyć po zmapowaniu składników z LLM i po pełnym crawlu Policzonej.
**Błąd do poprawki (0.1.1):** fetcher nie robi backoffu na 429 — Policzona (limit tempa) odrzuca ~95% zapytań przy 1 req/s. Potrzebne: respektowanie `Retry-After`/wykładniczy backoff i przerwanie przebiegu źródła po kilku 429 z rzędu (bez trwałego skip, 429 jest już traktowane jako przejściowe).

## Wydanie 0.1.1 — 2026-11-22 (backoff 429) i stan otwarty
Tag `v0.1.1` (non-draft), CI zielone, 163 testy, zainstalowane przez Supervisor (opcje zachowane). Fetcher: backoff po 429 (Retry-After/10 s/30 s), tempo ×2 do 8 s, `crawl` przerywa źródło po 3 kolejnych 429 i zwraca limit dzienny.
**OTWARTE — Policzona dalej nie oddaje przepisów:** po ręcznym „Odśwież” (POST /katalog/odswiez?zrodlo=policzona) przez ~12 min katalog stoi na 301 (Respo 150, Lidl 149, Policzona 2), status „pobieranie…”, liczniki (błędy 43) bez zmian, ostatni błąd = świeży 429 (inny adres niż wcześniej, potem ten sam przez >5 min). Log add-onu nie zawiera żadnych wpisów o crawlu (Dieta nie loguje przebiegów — tylko start/stop serwera).
Hipotezy do sprawdzenia (nie zweryfikowane): (a) przebieg wisi w kroku po crawlu w `run_catalog` (mapowanie składników przez LLM freellmapi — paczki po 25, wolne), a „pobieranie…” to flaga całego `run_catalog`; (b) Policzona (Shopify) nadal odrzuca ~wszystko — wtedy wolniejsze tempo dla źródła (5–8 s) lub rozłożenie na noce; (c) zawieszenie w fetchu. Pierwszy krok: dodać logi przebiegu (start/koniec źródła, liczniki, 429, czas mapowania) i odtworzyć przebieg lokalnie z fałszywym fetch/ask; potem decyzja.
Pomiar znajomości nadal niemiarodajny (0/2/7 z 301 przy ≥70/60/50%, składniki w `pending`).

## Wydania 0.1.2 i 0.1.3 — diagnoza crawla Policzonej (2026-11-22/23)
Tagi `v0.1.2` (logi przebiegu), `v0.1.3` (cooldown), non-draft, CI zielone, 164 testy, ruff/mypy czyste, pubguard 0 flag; 0.1.3 zainstalowane (Supervisor, opcje zachowane).
**Diagnoza (z logów 0.1.2):** crawl Policzonej trwał 380 s (Retry-After do 60 s × 2 ponowienia × 3 kolejne 429), dodał 0 przepisów i został przerwany. Dopiero potem ruszyło mapowanie składników LLM: 628 powiązań w 796 s, metadane AI 207 w 192 s. „pobieranie…” w UI to flaga całego przebiegu, nie zawieszenie. Hipoteza (c) odrzucona; (a) potwierdzona jako wolna faza (nie wisi); (b) potwierdzona — serwis dławi od pierwszego zapytania.
**Poprawka 0.1.3:** po przerwaniu źródła przez 429 kolejne próby (też ręczne) wstrzymane do następnego dnia (`crawl_cooldown:<źródło>`).
**Pomiar znajomości po mapowaniu (301 przepisów: Respo 150, Lidl 149, Policzona 2):** ≥70% = 4 (1,3%), ≥60% = 14 (4,7%), ≥50% = 44 (14,6%).
**Bramka E0 (znajomość ≥70% dla <10% przepisów) — DECYZJA USERA:** obniżyć próg (np. 50–60%) i/lub poszerzyć źródła; Policzona (cel: fit, kcal) dalej zablokowana przez 429 — rozważyć wolniejsze tempo/inny dostęp. Następny etap E4 (planer) czeka na tę decyzję.

## E3b PLAN GOTOWY 2026-11-23 (przed E4)
Decyzja usera: domyślny próg znajomości 50% + szersze źródła. Plan: `docs/PLAN_E3b.md`. W1 = konektor Aniagotuje + próg 50%, W2 = Policzona przez kanał Atom (`/blogs/przepisy.atom?page=N`, omija 429). Wydanie 0.1.4 po etapie, potem pomiar znajomości per źródło; następny E4 (planer).

## E3b — wydanie 0.1.4 (2026-11-23): kod ZROBIONY, pomiar CZEKA
Tag `v0.1.4` non-draft, CI zielone, 180 testów, ruff/mypy czyste, pubguard: 2 flagi = publiczna treść bloga w fixturze (bez danych osobowych); zainstalowane (Supervisor). Review: 0 blokujących; poprawki po review (host/schemat linków z kanału, wpisy bez treści, przyczyna błędu listowania, przepis bez składników, testy prefetch) w `16721b6`.
Opcje na żywo ustawione (`familiar_threshold: 50`, `sources` + `aniagotuje`), status `pending_restart`: **user restartuje add-on Dieta**.
Dalej: „Odśwież teraz” dla `policzona` i `aniagotuje` → po zakończeniu mapowania LLM pomiar (`pomiar`): przepisy per źródło, % z kcal, odsetek ≥70/60/50% ogółem i per źródło; ≥50% nadal <10% → stop i pytanie. Potem E4 (planer, wywiad `grill-me`).
Znane (nieblokujące): Aniagotuje bez kcal na porcję („brak kcal”, poza planerem); sitemapy bez kontroli hosta URL-i (dług sprzed 0.1.4); `totalTime` Aniagotuje liczy moczenie.

### Pomiar E3b po 0.1.4 (2026-11-23, na żywo, `GET /katalog?prog=N[&zrodlo=…]`, liczone po wierszach listy)
Kontrakt jak w pomiarze 0.1.3. Mapowanie LLM Ani Gotuje mogło jeszcze trwać (chip „pobieranie…”), więc liczby Aniagotuje to dolna granica; powtórzyć po zakończeniu.
- Katalog: 791 przepisów, z czego 229 bez kcal. Ania Gotuje 148 (0 błędów, 2 pominięte) — **148/148 bez kcal** (tylko wartości na 100 g). Policzona 44 (2 bez kcal).
- Znajomość ogółem: ≥50% **95 (12,0%)**, ≥60% 29 (3,7%), ≥70% 11 (1,4%). Z kcal przy ≥50%: 73 (9,2% katalogu).
- Per źródło ≥50%: Ania Gotuje 5/148 (wszystkie bez kcal → poza planerem), Policzona 4/44.
- Wniosek: kryterium ≥10% przy progu 50% spełnione (12,0%), ale użyteczne dla planera (z kcal) jest 9,2% i Aniagotuje nic nie dokłada. Decyzja usera: kcal na porcję dla Aniagotuje (ze składników: OFF/tabele), albo inne źródło z kcal.

## E3b ZAMKNIĘTY 2026-11-23 → następny E3c (kcal ze składników)
E3b: wydanie `v0.1.4` (non-draft, CI zielone, 180 testów), zainstalowane i zrestartowane; opcje na żywo `familiar_threshold: 50` + `aniagotuje`; pomiar w sekcji wyżej (≥50% = 12,0%; Aniagotuje 148 przepisów, wszystkie bez kcal).
**Decyzja usera 2026-11-23:** opcja 1 — kcal na porcję dla Ani Gotuje liczone ze składników („kcal ze składników+”; znaczenie „+” do wyjaśnienia w wywiadzie).
**E3c (do zrobienia, najpierw wywiad `grill-me`):** kto korzysta z wyniku, co automatycznie / za zgodą, miara sukcesu; źródła danych odżywczych (OFF, tabele, IŻŻ), dokładność i tolerancja błędu, porcje, zakres (tylko Aniagotuje czy też Respo/Lidl bez kcal), oznaczenie „szacunek” w UI. Potem `docs/PLAN_E3c.md`, wykonawcy, wydanie 0.1.5. Przed E4 powtórzyć pomiar znajomości po zakończeniu mapowania LLM Ani Gotuje.

## E3c PLAN GOTOWY 2026-11-23
Wywiad zakończony (sesja a031f378 + runda z 2026-11-23): kcal i makro na porcję ze składników dla wszystkich przepisów bez kcal ze źródła; kaskada tabela → OFF → LLM; auto z plakietką „szacunek”, do kolejki tylko niska pewność i pokrycie <85% masy; porcje bez liczby = masa/400 g. Plan: `docs/PLAN_E3c.md`. Paczki: seedy (ja) → W1 ‖ W2a → W2b (sonnet, ryzyko: migracja v4) → W3. Wydanie 0.1.5, potem E4.

## E3c ZROBIONE 2026-11-23 — wydanie `v0.1.5` (non-draft), zainstalowane
- Testy 301, ruff/mypy czyste, CI zielone, pubguard bez UWAG; review: blokujące i ważne naprawione w `af2a565` (niewiarygodne wartości pending poza szacunkiem, kontrola nazw w odpowiedzi LLM, kwalifikatory seeda, wyłącznik OFF, kontrola kcal/100 g przy wodzie).
- **Odstępstwo od kontraktu GO (decyzja usera):** wszystkie szacunki w ±30% = 68–69% (próg 80%); przepisy `auto` mediana błędu 11–13%, ~80% w ±30%. Szczegóły: `docs/POMIAR_kcal.md`.
- Na żywo (po „Odśwież teraz”, 706 s): migracja v4 OK; szacunek 229 przepisów — auto 131, do sprawdzenia 98, błędy 0; składniki: tabela 310, OFF 0, LLM 134; 536 linii z ilością domyślną. Z kcal/szacunkiem 693/791 (87,6%, wcześniej 562 = 71%). Ania: auto 75, pending 73 (148/148 mają szacunek); Lidl 55/24; Policzona 1/1, 42 bez szacunku.
- **Ustalenie na żywo:** Open Food Facts jest blokowany przez robots.txt (`FetchError`) → OFF = 0, wyłącznik po 3 awariach działa, całość idzie tabela → LLM. Decyzja do podjęcia: zostawić (respektujemy robots.txt) albo wyciąć OFF z kaskady i NOTICE.
- LLM: `JSONDecodeError` w paczkach słownika (`dictionary.py`) — sporadyczne, do diagnozy.
- Do zrobienia: 42 przepisy Policzonej bez szacunku (kolumna `kcal_source` pusta — sprawdzić dlaczego), kolejka 98 + 34 składniki do przejrzenia przez usera, pomiar z OFF (jeśli zostaje), lock `decide_ingredient` vs crawl, backfill `kcal_100g` Ani, zrzuty Playwright 390/1280 (niedostępne z kontenera: ingress 401, direct-IP wisi, tunel SSH zablokowany), powtórny pomiar znajomości.
- Następny etap: **E4 planer** (wywiad `grill-me`).

## E4 PLAN GOTOWY 2026-11-23 (czeka na akceptację)
Wywiad zakończony, plan: `docs/PLAN_E4.md`. Pomiar znajomości po 0.1.5 (na żywo): znajome ≥50% w puli z kcal = 98 z 693 (obiad 22, kolacja 20, śniadanie 23), ≥60% = 36, ≥70% = 14; wszystkie mają `storage_days`.
Decyzje: sesje wielodniowe od razu; tydzień pon–niedz; sloty 25/35/30 + 10% rezerwa; pula = kcal ze źródła lub szacunek auto; wspólne dania ze skalowaniem porcji (0,6–1,8); zatwierdzenie zamraża plan w add-onie (bez Cookidoo/kalendarza); suwak odkrywania 0–3 (domyślnie 1); wymiana sesji lub dnia + cofnij; kcal dnia ±10%, tydzień ±5%.
Paczki: W1 algorytm (`planner.py`, sonnet) ‖ W2 opcje + cele + migracja v5 (sonnet, ryzyko) → W3 serwis + ekran Plan (haiku). Bez wydania (0.2.0 po E5).

## E4 ZROBIONE 2026-11-23 (kod na `main`, bez wydania — 0.2.0 po E5)
Commity: `258642b` W1 (`planner.py`), `0677c35` W2 (opcje, `targets.py`, migracja v5, metody planu w `store.py`), `cd2cb73` W3 (`plan_service.py`, ekran Plan). Plan: `docs/PLAN_E4.md`.
Weryfikacja: 367 testów, ruff/mypy czyste; verifier W1, W2, W3 APPROVE. Na kopii prawdziwej `dieta.db` (profile syntetyczne, tylko kopia): pula 689 z 693 (makro wyklucza 4), generacja tygodnia 0,18 s, każdy dzień ±10% celu obu osób, 1 ostrzeżenie (śniadania: próg znajomości obniżony do 40%). Playwright 390/1280 px niewykonany (ingress niedostępny z kontenera) — ekran nieoglądany wizualnie.
Odstępstwa od planu (przyjęte): poniedziałek zawsze dozwolonym startem sesji (inaczej pon–wt puste przy `sat,sun,wed`); zamiast lokalnej poprawy twardy filtr ±10% na slocie, a przepis poza tolerancją tylko ostatecznością z ostrzeżeniem; sesja 1-dniowa po wymianie dnia także poza dniem gotowania; 4 asercje wersji schematu 4→5 w istniejących testach; `Store.update_plan_params` dopisane przez rodzica (regeneracja nie zapisywała seed/params).
Nieblokujące (do raportu wydania): `undo` nie przywraca `seed`/`params_json`; testy wymiany (`test_swap_*`, undo) zależą od losowego ziarna wymiany (`plan_service._apply`) — przekazać ziarno z parametru; `P_REPEAT` w planer.py nieużywana; `del settings` w `targets.py`; `ids[meal.session_index]` → IndexError przy złym indeksie; sesja śniadaniowa 120 min startuje w dzień roboczy (limit czasu to kara miękka); ikony kostka/serce (SVG rysowane ręcznie) do oceny wizualnej; brak testu ostrzeżeń generowanych przez planer w ekranie.
Następny: **E5 realizacja** (odhaczanie zjedzone/pominięte/zamiennik, bilans dnia i tygodnia na osobę, poranny push z planem dnia; wydanie 0.2.0) — najpierw wywiad `grill-me`. Przed E5: obejrzeć ekran Plan (ingress) i wpisać profile w UI.

## E5 PLAN GOTOWY 2026-11-23
Wywiad zakończony (potwierdzony „Go”), plan: `docs/PLAN_E5.md`. Decyzje: odhaczanie per osoba × dzień × slot (zjedzone / pominięte / zamiennik = wpis ręczny nazwa+kcal+makro opcjonalnie; brak odhaczenia = „bez odpowiedzi”, nie liczy się); okno edycji dziś…−7 dni, tylko zatwierdzony plan; bilans kcal+makro per osoba, tydzień = suma, licznik „zostało porcji” z sesji wielodniowych (bez przenoszenia); jeden poranny push na `notify.family` (opcje `push_notify`, `push_time` 07:00, `homeassistant_api: true`), bez wieczornego przypomnienia; migracja v6 `meal_log` (migawka, niezależna od `plan_meal`).
Paczki: W1 (sonnet, ryzyko: migracja + `realization.py` + szkielety) → W2 ekrany Dziś/Bilans ‖ W3 `push.py` (haiku). Potem `E5-release`: simplify → code-review → release 0.2.0.

## E5 ZROBIONE 2026-11-23 (kod na `main`, bez wydania — krok `E5-release` następny)
Commity: `5c1ca4e` W1 (migracja v6 `meal_log`, `realization.py`, opcje `push_notify`/`push_time`, `homeassistant_api`), `867ef1d` W3 (`push.py`), `d66a6ff` W2 (ekrany Dziś i Bilans). Plan: `docs/PLAN_E5.md`.
Weryfikacja: 425 testów, ruff/format/mypy czyste; verifier W1, W2, W3 APPROVE (claimcheck: W1 2 flagi sprawdzone przez verifiera, W2/W3 0). Playwright 390/1280 px niewykonany (ingress niedostępny z kontenera) — ekrany Dziś/Bilans nieoglądane wizualnie; push niesprawdzony na żywo.
Odstępstwa (przyjęte): poniedziałek liczony lokalnie w `realization.py` (`_monday`), bo `plan_service.monday_of` ma inną sygnaturę; „Cofnij” w Dziś zawsze przy `editable`; `compose` nie używa `panel_path` w treści (link w `data` przy wysyłce); linia „Wczoraj bez odpowiedzi” pokazuje obie osoby przy sumie > 0.
Nieblokujące (do raportu wydania): `substitute` zawsze z `recipe_id` zaplanowanego przepisu; brak plakietki „szacunek” w Dziś (`DayLog` bez flagi); `bilans.html` bez komunikatu `notice`; brak testu push dla dnia bez posiłków; układ 390 px / przyciski ≥44 px niezweryfikowane w przeglądarce.
Następny krok: `E5-release` (simplify → code-review → release 0.2.0 → weryfikacja na żywo). Przed wydaniem: user wpisuje profile i restartuje add-on (nowe opcje + `homeassistant_api`); obejrzeć ekrany Plan/Dziś/Bilans. Potem E6 (koszt i lista).

## E5-release ZROBIONE 2026-11-23 — wydanie `v0.2.0` (non-draft), zainstalowane
- Commity: `148f6a9` simplify (wspólne `monday()`, `candidates_for()` zamiast puli przy odhaczaniu/Dziś, jedno `plan_meals` w bilansie, martwe parametry), `9ec1d0e` poprawki po review (push: 3 próby co 10 min, traceback i nazwa usługi w logu; `day_log` odporny na brak przepisu; zakres dat 2000–2100 i limit długości `osoba` w `/dzis`), `14f4252` release (bump, CHANGELOG, DOCS).
- Weryfikacja: 428 testów, ruff/mypy czyste, CI zielone, pubguard 0 flag; security review bez blokujących; silent-failure-hunter, pr-test-analyzer i code-review — blokujące naprawione.
- Na żywo: Supervisor zainstalował 0.2.0, `homeassistant_api: true` przyjęte, opcje `push_notify=family`/`push_time=07:00`, tabela `meal_log` istnieje (migracja v6), `/healthz` `/dzis` `/bilans` `/plan` → 200, kontener bez restartów. Push niesprawdzony na żywo (wyśle się o 07:00).
- Pominięte (nit/doradcze): `eaten` przy kcal 0; odchylenie % tygodnia liczy cel wszystkich dni do dziś także bez odpowiedzi; link panelu w pushu (`HOSTNAME`) niesprawdzony; brak tokenu → `send` zwraca True (tryb dev); `push_notify` domyślnie `family`; zduplikowane `_base`/`_number`/makra szablonów; sqlite połączenie per metoda; `queue_size` 4 zapytania; `push` importuje `web.kupujemy.seconds_until`; brak testów `day_log` z brakującym przepisem oraz granic okna/walidacji.
- Czeka na usera: wpisać profile (bez nich Plan/Dziś pokazują „Uzupełnij profile”), obejrzeć ekrany Plan/Dziś/Bilans w ingress, sprawdzić `notify.family` i czy link z pushu otwiera panel. Brak bezpiecznego downgrade do 0.1.5 (v6).
- Następny: **E6 koszt i lista** (wywiad `grill-me`).

## E6 PLAN GOTOWY 2026-11-23
Wywiad zakończony (potwierdzony „Go”), plan: `docs/PLAN_E6.md`. Decyzje: dwa kroki cen (E6 = mediana ceny płaconej z paragonów, nowe pola `unit_price`/`is_weight`/`last_unit_price` w `/api/v1/purchases` → wydanie Zakupów 1.11.0; E6b = ceny półkowe i promocje); lista zakupów na ekranie „Lista” + przycisk „Wyślij do todo HA” (`todo_entity`), „Pewnie masz” z „Dodaj jednak”; koszt tygodnia dla domu z pokryciem %; tryb „taniej” (`cheaper_mode`, domyślnie wył.) premiuje składniki „w domu”.
Paczki: W0 Zakupy (sonnet) ‖ W1 dane/ceny/lista/opcje (sonnet, migracja v7) → W2 web+todo (haiku) ‖ W3 planer (sonnet). Potem `E6-release`: simplify → code-review → release Zakupy 1.11.0 + Dieta 0.3.0 → pomiar pokrycia na żywo (cel ≥70% masy). E7 → 0.4.0 / Zakupy 1.12.0.

## E6 ZROBIONE 2026-11-23 (kod na `main`, bez wydania — krok `E6-release` następny)
Commity: Zakupy `6f4271f` W0 (`unit_price`/`last_unit_price`/`is_weight` w `/api/v1/purchases`); Dieta `6fea89c` W1 (migracja v7, `pricing.py`, `shopping.py`, opcje `todo_entity`/`cheaper_mode`), `740c6fb` W2 (ekran Lista, pasek kosztu w Planie, `todo.py`), `b118d33` W3 (tryb „taniej”, `home_ratio`). Plan: `docs/PLAN_E6.md`.
Weryfikacja: Dieta 491 testów, Zakupy 870; ruff/format/mypy czyste; verifier W0, W1, W2, W3 APPROVE (bez blokujących). Playwright 390/1280 px niewykonany (ingress niedostępny z kontenera) — ekran Lista i pasek kosztu nieoglądane wizualnie; pokrycie cen na żywej bazie niezmierzone (Zakupy 1.11.0 jeszcze niewydane).
Nieblokujące (do raportu wydania): `home_ratio` = N+1 zapytań i `_context` liczony zawsze (też przy `cheaper=false`), import prywatnego `_context`; wydajność generacji tygodnia (było 0,18 s) niezmierzona — zmierzyć na kopii bazy; `is_weight` w kliencie `bool(row[...])` → `is True`; pokrycie listy bez „w domu” vs `plan_cost` z nimi; linie przepisu bez `ingredient_id` pomijane w liście i koszcie; przy `no_prices` przycisk todo wysyła też pozycje bez ceny (potwierdzić); `is_weight` w Zakupach z ostatniej pozycji `quantity > 0`, test API sprawdza tylko obecność kluczy; zmiana helpera `_recipe` w `test_web_plan.py`.
Następny krok: `E6-release` (simplify → code-review + silent-failure-hunter + pr-test-analyzer + security → release Zakupy 1.11.0, potem Dieta 0.3.0 → pomiar pokrycia na żywo, cel ≥70% masy). Potem E6b (ceny półkowe i promocje, Zakupy 1.12.0), E7.

## E6-release ZROBIONE 2026-11-23 — Zakupy `v1.11.0` + Dieta `v0.3.0` (non-draft), oba zainstalowane
- Commity: Zakupy `a803083` release (przedtem simplify, fix review: jedna jednostka `unit_price` z ostatniego paragonu, bez linii zerowych); Dieta `c090b17` release (przedtem simplify: wspólny `shopping.line_grams`, publiczny `Context`/`build_context`, `home_ratio` liczony tylko przy `cheaper_mode`; fix review: opakowania „N szt.” nie liczone z masy sztuki, `plan_cost` odizolowany w `/plan`).
- Weryfikacja: Zakupy 872 testy, Dieta 492, ruff/mypy czyste, CI zielone (Dieta, obraz), pubguard 0 flag. Review (code-review, silent-failure-hunter, pr-test-analyzer, security): 0 blokujących, 3 ważne naprawione.
- Na żywo: Supervisor 1.11.0 i 0.3.0, kontener bez restartów, `/healthz` `/plan` `/lista` `/dzis` `/bilans` `/katalog` → 200 (0,3–1,0 s), brak błędów w logu. Po sync: 775/776 produktów z `unit_price`, `build_context` 1,1 s, 63 składniki z ceną.
- **Pokrycie cen (cel ≥70% masy): niespełnione na katalogu** — mediana pokrycia przepisu 0,29; ≥70%: 106/791 (13%), ≥50%: 232/791. Pomiar planu (z profilami) niemożliwy, dopóki user nie wpisze profili i nie zatwierdzi planu; przepisy „znajome” (domyślnie w planie) powinny mieć wyższe pokrycie — zmierzyć na zatwierdzonym planie. Przyczyna: składniki spoza zakupów nie mają ceny (ceny półkowe = E6b).
- Nieblokujące do kolejnych etapów (z review): pokrycie zawyżone, gdy linia bez masy wypada z mianownika; suma sesji z `grams=None` pokazuje zaniżoną ilość bez „co najmniej”; „0,00 zł” zamiast „—” przy braku cen; `notice=ok` mimo braku cen składników planu; sesja z usuniętym przepisem znika z listy bez ostrzeżenia; todo: komunikat „wysłano X z Y”, rozdzielić brak konfiguracji od braku połączenia, WARNING przy braku tokenu, nazwa encji w logu, retry po timeoucie może zdublować; stary Zakupy kasuje ceny (log ostrzegawczy); `build_context` 1,1 s liczony 2× na stronę Planu (cache per request); `toggle` bez transakcji; walidacja `todo_entity`; luki testów (statusy mapowań cen, `grams=None`, brzeg tygodnia, `_home_ratio` mieszany, `is_weight=None`).
- Czeka na usera: wpisać profile, ustawić `todo_entity` (opcja) i obejrzeć Plan/Lista/Dziś/Bilans w ingress; brak bezpiecznego downgrade do 0.2.0 (v7).
- Następny: E6b (ceny półkowe i promocje, Zakupy 1.12.0) albo E7 — kolejność do potwierdzenia (wywiad `grill-me`); najpierw pomiar pokrycia na zatwierdzonym planie.

## E6b.0 SPIKE ceny półkowe — 2026-11-23 (bramka NIEROZSTRZYGNIĘTA, spike zawieszony)
Decyzje z wywiadu: E6b przed E7; cel pokrycia ≥70% masy składników zatwierdzonego planu (reszta „szacunek”); odświeżanie tygodniowe, wszystkie składniki katalogu; długi z E6 (cache `build_context` per request, „—” zamiast 0,00 zł, komunikaty todo „wysłano X z Y”) wchodzą do E6b.
**Założenie z wywiadu obalone:** `products/search` Biedronki zwraca tylko `{name, sap_product_number, unit}` (spike E31.1), a `products/price` wymaga EAN — cena półkowa składnika bez zakupów nie wynika z samego wyszukiwania. Lidl: brak API półki, tylko promocje/gazetka.
Sprawdzone na żywo (tylko odczyt, narzędzia w `~/dev/biedronka-spike/scripts/e6b_*.py`, wyniki `data/e6b/`):
- `zakupy.biedronka.pl` (robots `Allow: /`): sitemapa ~1700 stron produktów, strona ma nazwę, gramaturę, `sku` (= SAP), **bez cen dla gościa** i bez EAN.
- Pomysł usera: EAN-y z zewnątrz + prywatne API do cen. OFF `stores_tags=biedronka&countries_tags=en:poland` = 696 EAN, pula spożywcza 153. Próbka 100 przez `products/price/3889/<EAN>/`: **29 cen** (nabiał, wędliny, jaja, ryby; zero mąk/ryżu/makaronów/olejów/warzyw), 71 błędów. Odpowiedź ceny zawiera `name`, `unit_price` (zł/kg|l|szt), `price`, `regular_price`, promocję.
- Drugi przebieg (112 EAN podstawowych kategorii z OFF bez tagu sklepu): 112/112 błąd, a ponowiony znany dobry EAN też — token/limit API po stronie Biedronki, wynik **niemiarodajny**. Log Zakupów z 3 h bez wpisów o auth.
Wniosek częściowy: OFF z tagiem sklepu jest za wąskie (kilkadziesiąt cen, tylko nabiał/mięso). Niezbadane: czy EAN-y bez tagu (mąka, ryż, makaron, olej) mają ceny po odzyskaniu dostępu; sprawdzenie pokrycia względem słownika Diety. Następny krok: odczekać/odświeżyć token spike'u (login przez user: `! python spike.py login osoba1`), powtórzyć próbkę 112 EAN, potem decyzja: EAN-y spoza tagu vs tabela orientacyjna (GUS/LLM, „szacunek”).

### E6b.0 — kodyzbiedry.pl (sprawdzone 2026-11-23, tylko odczyt publicznych stron)
Baza EAN produktów Biedronki (nazwa, kategoria, cena, dostępność, `createdAt`); sitemapy `sitemap-verified-0..20` ≈ 104 789 EAN-ów; `robots.txt` Allow (blokuje tylko Bytespider/CCBot i strony techniczne). Statyczny `page-data.json` produktu = 557 B z EAN/nazwą/ceną/kategorią — **ale dla losowych EAN-ów z sitemap zwraca stronę główną (SPA fallback): 199/200 bez danych**; pełne dane tylko przez `api.kodyzbiedry.pl/api/products` („wymaga uprawnień”, nie obchodzimy). Kategorie przez statyczny page-data: tylko 5 stron × 25 produktów na kategorię. Ceny nie są per sklep, świeżość nieznana (źródło społecznościowe/własny crawl). Wniosek: bez zgody właściciela serwis nie rozwiązuje problemu; opcja — zapytać o dostęp do API (kontakt: formularz na stronie). Nadal otwarte: crawl `zakupy.biedronka.pl` (~1700 produktów, nazwa/gramatura/SKU, bez cen), odzyskanie tokenu API Biedronki (`! python ~/dev/biedronka-spike/spike.py login osoba1`).

### E6b.0 — inne źródła cen (2026-11-23): **Frisco, publiczny feed produktowy — kandydat nr 1**
Przegląd (agenci docs-scout, nie zweryfikowane w całości): GUS BDL (CC BY 4.0, kilkadziesiąt pozycji, bez sieci — tylko tabela referencyjna), Open Prices (ODbL, po EAN, pokrycie PL nieznane), Ceneo odpada (robots), Auchan/Carrefour/Lidl.pl — regulaminu nie udało się odczytać (403/PDF), do ręcznego przeczytania.
**Frisco:** `robots.txt` wprost zaprasza do użytku osobistego, niekomercyjnego przez gotowy feed: `https://commerce.frisco.pl/api/v1/integration/feeds/public?language=pl` (jeden GET, 51,8 MB JSON, `generatedAt` dzienny; prosimy o grzeczność — nie scrapować ręcznie). Zweryfikowane na żywo: 15 189 produktów, **wszystkie z EAN**, `price.price` w 15 111, `grammage` + `unitOfMeasure`, `promotions`, `isAvailable`, kategorie (drzewo, PL/EN), `packSize`. Podstawowe składniki są (mąka pszenna 24, ryż 236, makaron 219, olej rzepakowy 13, cukier 108, kasza 61, płatki owsiane 25, pomidory krojone 19, masło 36, jaja 25, oliwa 47, soczewica 10, jogurt naturalny 36…); brak „pierś z kurczaka” po tej frazie (mięso pod inną nazwą — do sprawdzenia).
Zastrzeżenia: Frisco to sklep z dostawą (ceny wyższe niż Biedronka/Lidl) → traktować jako **cenę rynkową/„szacunek”** dla składników bez zakupów, zakupy z paragonów mają pierwszeństwo; mapowanie nazwa→składnik (słownik + LLM), przeliczenie na zł/100 g z `grammage`/`unitOfMeasure`; dane nie trafiają do repo (użytek niekomercyjny, bez redystrybucji; w NOTICE wzmianka o źródle); jeden pobranie/tydzień ≈ 52 MB.
Następny krok: pomiar pokrycia (składniki zatwierdzonego planu/katalogu ↔ produkty feedu) i plan E6b na tym źródle zamiast Biedronki.

## E6b PLAN GOTOWY 2026-11-23 (wywiad zakończony, plan: `docs/PLAN_E6b.md`)
Kaskada cen: paragon ≤30 dni → Biedronka po EAN (cache 7 dni, promocja gdy ważna w tygodniu planu) → paragon 31–180 dni → Frisco × współczynnik („≈”). Frisco w Diecie (feed tygodniowo), Biedronka przez nowy `/api/v1/shelf` w Zakupach. EAN-y: paragony + Frisco + OFF + mała próba „sąsiadów” (≤100). Najpierw pomiar E6b.0 (bramka ≥50% przepisów z ≥70% masy z ceną; dziś 13%), potem kod; jedno wydanie Zakupy 1.12.0 + Dieta 0.4.0.

### E6b — zmiana architektury 2026-11-23 (decyzja usera)
Wspólny katalog cen w Zakupach (produkty + obserwacje tylko dopisywane, historia promocji, baza EAN Biedronki rośnie z paragonów/API/Frisco); Frisco wczytywane w Zakupach, Dieta czyta przez `/api/v1/*`. Szczegóły: `PLAN_E6b.md` pkt 2, E6b.1/E6b.2. E6b.0: pomiar offline gotowy (`POMIAR_ceny.md`, 75% przy regułach, współczynnik 0,75), próba Biedronki po EAN w toku.

## E6b.0 ZROBIONE 2026-11-23 (pomiar; bramka offline: GO) → następny E6b.1
Wynik: `docs/POMIAR_ceny.md`, commity `821b360`, `83fff30`; plan `PLAN_E6b.md` (`3bd600d`, `099f1e9`). Frisco + paragony: ≥70% masy z ceną dla 75% przepisów (reguły, górna granica) vs 8% dziś; współczynnik paragon/Frisco 0,75 (367 par EAN). Skrypty poza repo: `~/dev/biedronka-spike/scripts/e6b_frisco_map.py`, `e6b_coverage.py`, `e6b_prices.py` (odstęp 4 s).
Biedronka `products/price`: HTTP 429 dla wszystkich EAN/sklepów (token OK) → próba nierozstrzygnięta, powtórzyć jutro (1 zapytanie, potem partie ≤20 co ≥10 s); budżet w kaskadzie mały i konfigurowalny. Sprawdzić log Zakupów (`shelf/error`, poranny przebieg 06:30) — czy limit dotyka też produkcji.
Decyzja usera: wspólny katalog cen w Zakupach (Frisco wczytywane tam). Następny: E6b.1 (Zakupy: katalog + `/api/v1/shelf`), najpierw plan etapu z sekcją Wykonawca.

## E6b.1 PLAN GOTOWY 2026-11-23 (`docs/PLAN_E6b_1.md`)
Log Zakupów (36 h): bez błędów `shelf`/429 — limit nie dotyka produkcji. Plan etapu z sekcją Wykonawca (sonnet-worker, jedna paczka, schemat v16 `catalog_products`/`catalog_obs`, ingest paragonów + Frisco, `/api/v1/catalog`). Następny krok: `wt new` w repo Zakupów → sonnet-worker → verifier → commit → stage-end.

## E6b.1 ZROBIONE 2026-11-23 (Zakupy, commit `73ccd56`, niewydane) → następny E6b.2
Schemat v16 (`catalog_products`, `catalog_obs` tylko dopisywane, kopia `.pre-catalog`), ingest paragonów (Biedronka po EAN `b:<ean>`, Lidl po `lidl_art`) i tygodniowy feed Frisco (nd 03:00, `POST /api/v1/catalog/frisco/refresh`), `GET /api/v1/catalog?since&source&history`, `GET /api/v1/shelf?store=` (cache `shelf_prices` + produkt z katalogu po EAN). Weryfikacja: worker pełny `pytest` 888 passed, ruff/mypy czyste; verifier REJECT (shelf pusty) → poprawka → ACCEPT (test_catalog 16 passed). Wersja Zakupów zostaje 1.11.0.
Do sprawdzenia przed wydaniem (NIEBLOKUJĄCE verifiera): `catalog_products(eans=)` filtruje w Pythonie po całym katalogu; `json.load` feedu 52 MB w pamięci (pomiar na hoście HA); ingest Frisco w wątku na współdzielonym połączeniu sqlite (wiele commitów) — rozważyć jedną transakcję/partie; backfill paragonów synchronicznie w lifespan (czas na dużej historii); krok 4 planu (migracja + backfill na kopii żywej bazy) NIE wykonany — zrobić na początku E6b.2 albo przed wydaniem; treść NOTICE Frisco do przeczytania przez usera.
Następny: E6b.2 (Dieta: mapowanie Frisco→składnik reguły + AI `auto:text` + kolejka, współczynnik 0,75, migracja v8) — najpierw `docs/PLAN_E6b_2.md` z Wykonawcą.

## E6b.2 ZROBIONE 2026-11-23 (Dieta, commit `2d31c65`, niewydane) → następny E6b.3
Plan `docs/PLAN_E6b_2.md`. Migracja v8 (`catalog_item`, `catalog_map`, kopia `.pre-v8`), `fetch_catalog`, `catalog_map.py` (rule ścisłe / AI tak-nie-wątpliwe → pending), `receipt_to_frisco_factor`, `frisco_prices`, `sync_catalog` (wołany po `sync` w `default_runner`, harmonogram tygodniowy w E6b.3). Pełny pytest 519 passed, ruff/mypy czyste; verifier ACCEPT.
Pomiar na feedzie (bez AI, 15 111 produktów, 957 składników): reguły ścisłe zmapowały 1367 produktów → 369 składników; reszta (luźni kandydaci) czeka na AI przy skonfigurowanym LLM — realne pokrycie zmierzyć na żywo.
NIEBLOKUJĄCE: martwy parametr `ingredient_by_code` w `receipt_to_frisco_factor` (nie podany w `sync_catalog`) — wykorzystać albo usunąć w E6b.3; `FriscoPrice.regular100` zawsze None; UI kolejki `pending_catalog` w E6b.4; krok 4 PLAN_E6b_1 (migracja Zakupów na kopii żywej bazy) nadal niewykonany.
Następny: E6b.3 (kaskada `pricing.py`, nocna kolejka EAN do Zakupów, harmonogram tygodniowy `sync_catalog`, powtórka próby Biedronki 1 zapytanie → partie ≤20 co ≥10 s) — najpierw `docs/PLAN_E6b_3.md` z Wykonawcą.

## E6b.3 PLAN GOTOWY 2026-11-23 (`docs/PLAN_E6b_3.md`)
Zmiana zakresu: w Zakupach `/api/v1/shelf` jest tylko GET (zleceń EAN brak) → E6b.3 = tylko Dieta (kaskada `cascade_prices`, `shelf_item` v9, `sync_shelf`, harmonogram tygodniowy `sync_catalog` z `force`, cache `build_context`); **E6b.3b** (Zakupy `POST /api/v1/shelf` + nocna kolejka EAN + powtórka próby Biedronki) osobnym etapem po E6b.3, plan `PLAN_E6b_3b.md` przed startem.

## E6b.3 ZROBIONE 2026-11-23 (Dieta, commit `5238fc0`, niewydane) → następny E6b.3b
Plan `docs/PLAN_E6b_3.md` (`206fbde`). Migracja v9 (`shelf_item`, kopia `.pre-v9`), `fetch_shelf`/`sync_shelf` (po `sync_catalog` w `default_runner`), `sync_catalog(force)` z bramką 7 dni (nocna pętla), `pricing.cascade_prices` (paragon ≤30 d, próg 0,8 → Biedronka cache ≤7 d / promocja w tygodniu planu → paragon 31–180 d → Frisco ≈ `estimate`), `IngredientPrice` z `source/as_of/promo_to/estimate`, `build_context` z kaskadą i cache per request (`Store.data_version`), `ingredient_by_code` użyty. Pytest 545 passed, ruff/mypy czyste; verifier ACCEPT.
NIEBLOKUJĄCE: `_promo_applies` — promocja bez dat obowiązuje każdy tydzień (sprawdzić dane Zakupów); `data_version` per proces; test `cheaper_mode` sprawdza tylko zakres; krok 4 planu (pomiar źródeł na fixture) niewykonany; krok 4 PLAN_E6b_1 (migracja Zakupów na kopii żywej bazy) nadal niewykonany.
Następny: E6b.3b (Zakupy `POST /api/v1/shelf` zlecenia w tle + Dieta nocna kolejka EAN, powtórka próby Biedronki 1 zapytanie → partie ≤20 co ≥10 s, przycisk „Odśwież ceny planu”) — najpierw `docs/PLAN_E6b_3b.md` z Wykonawcą (dwa repo: ha-lidl-app i ha-dieta-app). Potem E6b.4, E6b-release.

## E6b.3b PLAN GOTOWY 2026-11-23 (`docs/PLAN_E6b_3b.md`)
Dwie paczki: W1 Zakupy (`POST /api/v1/shelf`, `refresh_eans`: 1 zapytanie próbne → partie ≤20 co ≥10 s, `shelf_absent` 30 dni, schemat v17, `absent` w GET) — `sonnet-worker` (ryzyko); W2 Dieta (kolejka EAN plan→katalog ≤100/noc, `post_shelf_request`, przycisk „Odśwież ceny planu”) — `haiku-worker`. Test na żywo odroczony do E6b-release.

## E6b.3b ZROBIONE 2026-11-23 (Zakupy `1253609`, Dieta `c122a81`, niewydane) → następny E6b.4
Plan `docs/PLAN_E6b_3b.md`. Zakupy: `POST /api/v1/shelf` (202/400/409 no_store|no_account|busy, zadanie w tle pod `shelf_lock`), `shelf_prices.refresh_eans` (1 zapytanie próbne → partie 20, 1 s w partii, 10 s między), `shelf_absent` 30 dni (schemat v17, kopia `.pre-absent`), `absent` w GET. Pytest 916 passed, ruff/mypy czyste, verifier ACCEPT. Dieta: `shelf_queue.py` (plan → katalog, ≤100/noc, bez świeżych ≤7 d i absent), `post_shelf_request`, nocna kolejka przed `sync_shelf`, przycisk „Odśwież ceny planu” (`POST /kupujemy/ceny`). Pytest 562 passed, ruff/mypy czyste, verifier ACCEPT.
NIEBLOKUJĄCE: `sync_shelf` robi drugi GET po `absent` — jego awaria blokuje upsert cen (rozważyć jeden GET); `plan_ingredient_names` testowany tylko monkeypatchem (sprawdzić na realnym planie); brak testu granicy świeżości ≤7 d; `.pre-absent` robiona po `executescript(_SCHEMA)` (wzorzec v15); test na żywo POST (1 EAN próbny) odroczony do E6b-release; kroki 4 PLAN_E6b_1 (migracja Zakupów na kopii żywej bazy) i pomiar źródeł nadal niewykonane.
Następny: E6b.4 (UI: plakietki źródła/„≈”, pokrycie %, „—” zamiast 0,00 zł, komunikaty todo „wysłano X z Y”), potem E6b-release (Zakupy 1.12.0 przed Dietą 0.4.0).

## E6b.4 ZROBIONE 2026-11-23 (Dieta `6476d3f` + CHANGELOG `eedd316`, niewydane) → następny E6b-release
Plan `docs/PLAN_E6b_4.md`. W1: `ShoppingLine.source/estimate/as_of/promo_to`, `coverage_by_source` (lista i plan), plakietki źródła, „≈”, „—”, linia „w tym:”, komunikaty todo `todo_ok-X-Y`/`todo_partial-X-Y`. W2: kolejka `pending_catalog` w „Do zatwierdzenia” (`POST /kupujemy/zatwierdz-katalog`), wliczona do `queue_size`. Pytest 578 passed, ruff/mypy czyste; verifier ACCEPT (W1, W2).
NIEBLOKUJĄCE: etykiety źródeł powtórzone w `plan.html` (makro) zamiast jednego miejsca w `web/lista.py`; słabe asercje „≈ ”/„szacunek” w `test_web_lista.py`; `test_catalog_approve_removes_from_queue` nie sprawdza, że drugi wpis zostaje; linia „w tym:” zawiera też „promocja”; wizualny test 390 px niewykonany (w E6b-release); DESIGN.md (sekcja E6b.4) i DOCS.md nie uzupełnione.
Następny: E6b-release (simplify → code-review + silent-failure-hunter + pr-test-analyzer + security → Zakupy 1.12.0 przed Dietą 0.4.0 → test na żywo: 1 EAN próbny POST, pomiar pokrycia, widok „Lista” 390 px).

## E6b-release ZROBIONE 2026-11-23 (Zakupy 1.12.0 `63b938c`, Dieta 0.4.0 `3a06775`; oba v-tagi non-draft, zainstalowane przez Supervisor)
Testy: Zakupy 921 passed, Dieta 578 passed; pubguard 0 flag w obu. Poprawki z simplify/review: `catalog_bulk()` (backfill 393 s → 0,18 s, tylko przy pustym katalogu), ingest Frisco odporny na pojedynczy zły produkt, `refresh_eans` (abort przy nieznanym kształcie, `absent` dla odczytu bez ceny), Dieta: jeden GET półki, filtr EAN 8–13 cyfr, `split_coverage` publiczna, bez `regular100`.
Na żywo: Zakupy 1.12.0 i Dieta 0.4.0 działają, logi bez błędów; ręczny import Frisco 15 109 produktów w 14 s (szczyt ~182 MiB); `POST /api/v1/shelf` 1 EAN → accepted=1, Biedronka odpowiedziała 429 (limit po stronie sklepu; półka jeszcze pusta, ponowi nocna kolejka); strony Diety 200 (`/zatwierdz` 404 — sprawdzić właściwą ścieżkę).
NIEWYKONANE: pomiar pokrycia (cel ≥70% masy) i widok „Lista” 390 px — brak zatwierdzonego planu na tydzień od 2026-11-23, Lista pusta; do zrobienia po zatwierdzeniu planu przez usera.
Dług (nieblokujący): parse_iso_date x5, cascade_prices jako lista warstw, SQL w catalog_products(since/eans), N+1 ingest Frisco, weekly_loop vs daily.at_time_loop, queue_size COUNT, data_version per proces, ręczne „Odśwież” z force w żądaniu HTTP, json.load feedu 52 MB bez limitu bajtów, redirect z Bearer w urllib, nazwy feedu → prompt LLM, UI nie pokazuje `catalog_sync_error`/`shelf_sync_error`/`shelf_queue_error`, pusty katalog zapisywany jako sukces, luki testów (pr-test-analyzer), catalog._ean 8–14 vs _EAN_RE 8–13, `store` w POST /shelf bez walidacji.

## E6c ZROBIONE 2026-11-24 (Zakupy 1.12.1 `af700a3`, Dieta 0.4.1 `9991a31`; tagi v1.12.1/v0.4.1 non-draft, zainstalowane, logi po starcie czyste)
Plany `docs/PLAN_E6c*.md`. Zakupy: limit feedu Frisco 150 MiB, pusty feed = błąd, walidacja `store` (400 z `store:`), EAN 8–13 cyfr, `detail` w dzienniku feedu. Dieta: opener bez przekierowań (Bearer), pusty katalog = `catalog_sync_error` bez nadpisania snapshotu, sanityzacja nazw w prompcie LLM, banery `catalog_sync_error`/`shelf_sync_error`/`shelf_queue_error` w „Kupujemy” i „Lista”, `POST /shelf` bez `store`.
Testy: Zakupy 931 passed, Dieta 588 passed (+ ruff/mypy); pubguard 0 flag; security czysto, silent-failure-hunter bez blokujących. Naprawiony test zależny od daty (`test_plan_cost_and_list_call_cascade_once_per_request`).
Zamknięte z długu E6b: limit bajtów feedu, redirect z Bearer, nazwy feedu → prompt, UI nie pokazuje błędów, pusty katalog jako sukces, `store` bez walidacji, `/zatwierdz` 404 (trasa to `POST /plan/zatwierdz`).
NIEWYKONANE (E6c.3, czeka na zatwierdzenie planu tygodnia przez usera w UI): pomiar pokrycia cen ≥70% masy → `docs/POMIAR_ceny.md`, widok „Lista” 390 px jasny / 1280 px ciemny (Playwright-HA nie jest zalogowany: ingress 401), kolejka EAN bez `shelf_queue_error` + niepusta półka Zakupów, `POST /api/v1/shelf` ze złym `store` → 400 (token tylko przez `! secret-run`).
Dług nieblokujący (bez zmian + nowe): parse_iso_date ×5, cascade_prices, SQL w catalog_products, N+1 ingest Frisco, weekly_loop vs at_time_loop, queue_size COUNT, data_version per proces, force w HTTP, `resp.read()` bez limitu w zakupy_client, `ingredient` niesanityzowany w prompcie, `role="status"` vs `alert` w bannerze, GTIN-14 pomijany po cichu w `catalog._ean` (świadome zawężenie 8–13), redirect http→https same-host to teraz błąd, luki testów (granice STORE_RE 32/33, pusty/nie-string store, EAN 8 cyfr, `refresh()` z przekroczonym limitem, `sync_errors` dla 3 kluczy naraz, `_prompt_safe` dla pustej nazwy i granicy 120), test_shopping kruchy o północy, LLM `HTTPError` w nocnym mapowaniu (WARNING dieta.dictionary — do zbadania osobno).

## E6c.3 CZĘŚCIOWO ZROBIONE 2026-11-24 (tylko odczyt + docs; brak zmian w kodzie)
Plan tygodnia 2026-11-23 wygenerowany (`POST /plan/generuj`, ostrzeżenie: próg znajomości dla śniadań obniżony do 40%) i zatwierdzony (`/plan/zatwierdz`) po wpisaniu profili przez usera; przed tym `no_profiles`.
- **Pomiar pokrycia:** plan 90%, lista 87% masy z ceną (cel ≥70% zaliczony) — `docs/POMIAR_ceny.md`, Przebieg 3. Podejrzane ceny pozycji (seler, pomidory, jajka, filet) do weryfikacji.
- **Widok „Lista”:** 390 px jasny — `scrollWidth` = `clientWidth` = 390, bez poziomego przewijania; 1280 px ciemny — 1280/1280, ciemne tło. Render lokalny z zrzutu HTML+CSS 0.4.1 (ingress 401, tunel SSH nie przepuszcza ruchu); zrzuty `~/.playwright-mcp/lista-390-light.png`, `lista-1280-dark.png`.
- **Kolejka EAN:** `shelf_queue_error` i `shelf_sync_error` puste, `shelf_queue_status=accepted`, logi Diety bez błędów; półka Zakupów nadal pusta (0 `shelf_item`) — kolejka nocna nie widziała jeszcze zatwierdzonego planu. DO SPRAWDZENIA po nocy 2026-11-25 (półka niepusta; 429 Biedronki możliwe).
- **`POST /api/v1/shelf` ze złym `store` → 400: ZROBIONE 2026-11-24** (za zgodą usera; skrypt w kontenerze Diety, token z `options.json` nie wypisany). `store` = `x store:!` i 33 znaki → oba 400, `detail`: „store: kod sklepu (1–32 znaki: litery, cyfry, _ lub -).”; bez zapisu.
- **NIEWYKONANE:** ręczna ocena 10 pozycji na paragonach; półka Zakupów niepusta po nocy 2026-11-25.
- **Nowe prośby usera (2026-11-24, do wywiadu/planu, nie zaczęte):** (1) wiek osób z daty urodzenia zamiast pola `age` w `profile`; (2) zmiana nazw osób w UI (dziś tylko opcje add-onu `person_1_name`/`person_2_name`).


## Najbliższe punkty (dopisane 2026-11-24)
- **E8 — konto Zepp Life: historia wagi i składu ciała (Dieta 0.6.0)**: plan zatwierdzony 2026-11-24, `docs/ROADMAP_E8.md` + `docs/PLAN_E8_zepp_roadmapa.md`
  (E8a klient Zepp → E8b migracja 12 i import → E8c „Konto Zepp” w profilu z OTP → E8d synchronizacja co 6 h → E8e Katch-McArdle, wykresy, korekta wg trendu → jedno wydanie).
  Zastępuje wcześniejszy plan E8 (lokalny odczyt wagi 625B z HA). Prototyp logowania: `~/dev/zepp-login`. Następny krok: `docs/PLAN_E8a.md` z sekcją Wykonawca.
  Do wyjaśnienia przed E8e: brak ważeń w Zepp po 27.10 oraz rozbieżność ~80 kg (HA) vs ~89 kg (Zepp).
- Z E7: poprawić daty urodzenia w UI (baner) i wygenerować nowy plan; push z nazwami niezweryfikowany na żywo (07:00).
- Z E6c.3: ręczna ocena 10 pozycji na paragonach (podejrzane ceny: seler, pomidory, jajka, filet); półka Zakupów niepusta po nocy 2026-11-25.
