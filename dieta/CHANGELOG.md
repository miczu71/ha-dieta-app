# Dziennik zmian

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
