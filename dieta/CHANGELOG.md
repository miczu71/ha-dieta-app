# Dziennik zmian

## 0.2.0 (2026-11-23)
- Plan tygodnia (E4): sesje gotowania na kilka dni, wspólne dania ze skalowaniem porcji, wymiana sesji lub dnia, cofnij, zatwierdzenie; opcje slotów (`slot_*_pct`) i planera.
- Realizacja (E5): ekran „Dziś” — odhaczanie per osoba i posiłek: zjedzone / pominięte / zamiennik (nazwa, kcal, makro opcjonalnie), cofnięcie, okno edycji dziś i 7 dni wstecz, tylko zatwierdzony plan. Ekran „Bilans” — kcal i makro dziennie i tygodniowo na osobę, posiłki „bez odpowiedzi” nie liczą się, licznik zostałych porcji z sesji wielodniowych.
- Poranny push z planem dnia na `notify.<usługa>`: opcje `push_notify` (domyślnie `family`, pusta = wyłączony) i `push_time` (07:00); do 3 prób co 10 min przy błędzie, bez nadrabiania po restarcie. Wymaga uprawnienia `homeassistant_api` (dodane) i **restartu add-onu po aktualizacji**; sprawdź, że usługa `notify.family` istnieje albo zmień opcję.
- Migracja bazy v6 (tabela `meal_log`): **po aktualizacji nie wracaj do 0.1.5** — starsza wersja nie zna schematu v6.
- Znane: link w powiadomieniu (ścieżka panelu) niesprawdzony na żywo; odchylenie tygodnia w Bilansie liczy cel wszystkich dni do dziś, także bez odpowiedzi.

## 0.1.5 (2026-11-23)
- Szacunek kcal i makro na porcję dla przepisów bez wartości ze źródła (m.in. cała Ania Gotuje): wartości na 100 g składnika z wbudowanej tabeli, potem Open Food Facts, potem AI; ilości w miarach domowych i sztukach przeliczane na gramy, linie bez ilości szacuje AI (waga 0,5 w pokryciu). Przepisy z kcal ze źródła pozostają bez zmian.
- Przepis z pokryciem masy ≥ 85% (i zgodny z kcal/100 g ze strony Ani, ±25%) dostaje status „szacunek” i wchodzi do planera; pozostałe są „niepełnym szacunkiem” w kolejce do sprawdzenia. W Katalogu: „~420 kcal (szacunek)”, pokrycie, nieznane składniki, „porcje szacowane”, filtr kcal.
- Kolejka zatwierdzania: składniki z niską pewnością lub wartością odstającą (edycja 4 liczb) oraz przepisy z niepełnym szacunkiem. Wartości niewiarygodne (>900 kcal/100 g, ujemne, same zera) nie wchodzą do szacunku.
- Migracja bazy v4 (tabela `ingredient_nutrition`, kolumny szacunku); wersja 0.1.4 odczyta bazę v4.
- Znane: kontrakt pomiaru (≥ 80% przepisów w ±30%) niespełniony dla wszystkich szacunków (68%); przepisy „auto”: mediana błędu 11,5%, 80% w ±30%. Open Food Facts był w pomiarze niedostępny (HTTP 503), więc jego wpływ nie jest zmierzony; szczegóły w `docs/POMIAR_kcal.md`.

## 0.1.4 (2026-11-23)
- Nowe źródło: Ania Gotuje (parser microdata; wartości odżywcze tylko na 100 g, więc przepisy bez kcal na porcję są oznaczone „brak kcal” i pomijane przez planer).
- Policzona Szama: lista przepisów z kanału Atom (30 najnowszych z pełną treścią, bez zapytań o artykuły i bez limitu dziennego) oraz sitemapa dla zaległości; linki z kanału tylko z własnego hosta (https). Gdy padną kanał i sitemapa, przebieg pokazuje przyczynę zamiast „sitemapa nie zawiera przepisów”.
- Domyślny próg znajomości 50% (suwak w opcjach bez zmian); w już zainstalowanym add-onie opcje `familiar_threshold` i `sources` trzeba zmienić ręcznie.
- Aniagotuje: przepis bez składników to błąd przebiegu, nie cichy zapis.

## 0.1.1 (2026-11-22)
- Crawl: po odpowiedzi HTTP 429 (limit tempa serwisu) fetcher odczekuje (nagłówek Retry-After albo 10 s, potem 30 s), ponawia do 2 razy i zwalnia tempo reszty przebiegu; po 3 kolejnych 429 przebieg źródła jest przerywany, a odrzucone zapytania nie zużywają dziennego limitu. Naprawia Policzoną Szamę, która przy 1 zapytaniu/s odrzucała ok. 95% stron.

## 0.1.0 (2026-11-22)
- Katalog (E3): przepisy z Centrum Respo, Lidl Przepisy i Policzonej Szamy z kcal/makro na porcję, znajomość względem zakupów (sort, próg %), filtry (posiłek, źródło, polubione, odrzucone), serce i „nie proponuj” wspólne dla domu, szczegóły ze składnikami; nocny crawl po syncu zakupów (≤ 1 zapytanie/s, robots.txt, limit dzienny) i „Odśwież teraz” per źródło; metadane planera z reguł i AI, kolejka zatwierdzania obejmuje przepisy i ich składniki.
- Odporność crawla i mapowania: awaria LLM nie zalewa kolejki, trwałe błędy 4xx i wygasające pominięcia (30 dni), pusta sitemapa to błąd, przekierowania tylko w obrębie hosta, robots.txt niedostępny blokuje host na przebieg.
- Szkielet add-onu (E1): ingress, opcje, healthcheck.
- Ekran „Profile”: cele kcal i makro dla 2 osób (Mifflin-St Jeor, PAL, tempo), ręczny cel kcal, wpisy wagi z wykresem 90 dni i średnią 7 dni.
