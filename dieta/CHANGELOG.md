# Dziennik zmian

## 0.1.0 (2026-11-22)
- Katalog (E3): przepisy z Centrum Respo, Lidl Przepisy i Policzonej Szamy z kcal/makro na porcję, znajomość względem zakupów (sort, próg %), filtry (posiłek, źródło, polubione, odrzucone), serce i „nie proponuj” wspólne dla domu, szczegóły ze składnikami; nocny crawl po syncu zakupów (≤ 1 zapytanie/s, robots.txt, limit dzienny) i „Odśwież teraz” per źródło; metadane planera z reguł i AI, kolejka zatwierdzania obejmuje przepisy i ich składniki.
- Odporność crawla i mapowania: awaria LLM nie zalewa kolejki, trwałe błędy 4xx i wygasające pominięcia (30 dni), pusta sitemapa to błąd, przekierowania tylko w obrębie hosta, robots.txt niedostępny blokuje host na przebieg.
- Szkielet add-onu (E1): ingress, opcje, healthcheck.
- Ekran „Profile”: cele kcal i makro dla 2 osób (Mifflin-St Jeor, PAL, tempo), ręczny cel kcal, wpisy wagi z wykresem 90 dni i średnią 7 dni.
