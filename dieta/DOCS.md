# Dieta

Planer posiłków wielodniowych dla dwóch osób. Profile, katalog przepisów, plan tygodnia, odhaczanie posiłków i bilans.

Wartości kcal są szacunkowe. Aplikacja nie jest poradą medyczną.

## Opcje
- `person_1_name`, `person_2_name` — imiona w panelu. Reszta profilu jest w panelu i w `/data`.
- `log_level` — poziom logów.
- `crawl_daily_limit` — dzienny limit nowych przepisów na źródło (domyślnie 150).
- `familiar_threshold` — domyślny próg znajomości w Katalogu, w % (50).
- `sources` — włączone źródła przepisów: `respo`, `lidl`, `policzona`, `aniagotuje`.
- `refresh_time` — godzina nocnego odświeżenia: sync zakupów, potem crawl katalogu.
- `push_notify` — nazwa usługi `notify.<nazwa>` do porannego powiadomienia z planem dnia (domyślnie `family`; pusta = wyłączone). Add-on ma uprawnienie `homeassistant_api` tylko po to, by ją wywołać. Po zmianie opcji zrestartuj add-on.
- `push_time` — godzina porannego powiadomienia (07:00); przy błędzie do 3 prób co 10 minut, bez nadrabiania po restarcie.
- `todo_entity` — encja listy todo HA (np. `todo.zakupy`) dla przycisku „Wyślij do todo HA” w Liście; pusta = przycisk nieaktywny.
- `cheaper_mode` — tryb „taniej”: planer premiuje przepisy ze składnikami, które pewnie macie w domu (domyślnie wyłączony).
- `slot_*_pct` i opcje planera — udział slotów w kcal dnia i parametry układania tygodnia.

## Katalog
Zakładka „Katalog” pokazuje przepisy z kcal i makro na porcję oraz „znajomość”: ile składników (bez podstawowych: sól, pieprz, woda, olej, cukier)
kupujecie. Sort po znajomości, filtr progu, posiłku, źródła, polubionych i odrzuconych (wspólne dla domu). Przepisy bez kcal mają plakietkę „brak kcal”.
Pobieranie: ≤ 1 zapytanie/s, z poszanowaniem `robots.txt`; przepis raz pobrany nie jest pobierany ponownie. „Odśwież teraz” działa w tle.
Niepewne metadane przepisów i niezmapowane składniki trafiają do „Co kupujemy → Do zatwierdzenia”.

## Plan, Dziś i Bilans
„Plan” układa tydzień (sesje gotowania na kilka dni) i po zatwierdzeniu go zamraża. „Dziś” pozwala odhaczyć posiłek osoby (zjedzone, pominięte, zamiennik z własną nazwą i kcal) w oknie dziś i 7 dni wstecz; brak odhaczenia = „bez odpowiedzi” i nie liczy się do bilansu. „Bilans” sumuje kcal i makro dnia i tygodnia na osobę oraz pokazuje zostałe porcje sesji wielodniowych.
Baza w wersji 7 nie jest czytelna dla 0.2.0 ani starszych — nie wracaj do starszej wersji bez kopii `/data`.

## Lista i koszt
„Lista” buduje zakupy z zatwierdzonego planu: składniki sesji scalone, bez tych, które pewnie macie w domu (przycisk „Dodaj jednak”). Koszt planu to szacunek z cen płaconych na paragonach (mediana, zł/100 g) z podanym pokryciem — udziałem masy składników z ceną. Wymaga Zakupów 1.11.0 i zsynchronizowanych zakupów.

## Ceny: kaskada i katalog Frisco (w przygotowaniu)
Po odświeżeniu zakupów Dieta pobiera z Zakupów katalog cen Frisco (`/api/v1/catalog?source=frisco`) i mapuje produkty na składniki z przepisów. Nazwa produktu zbliżona do nazwy składnika = mapowanie automatyczne; luźne dopasowania ocenia AI (wymaga skonfigurowanego LLM) — wątpliwe czekają w kolejce. Z par „ten sam EAN na paragonie i we Frisco” liczony jest współczynnik cen. Wynik jest zapisywany w bazie i nie wpływa jeszcze na koszt planu.

Cena składnika w koszcie planu i na liście liczona jest kaskadą, pierwsze trafienie wygrywa: (1) paragon z ostatnich 30 dni (ostatnia cena, a gdy jest poniżej 0,8 × mediany — mediana), (2) cache cen Biedronki z Zakupów (`/api/v1/shelf`, nie starszy niż 7 dni; cena promocyjna tylko gdy promocja obejmuje tydzień planu i nie jest „tylko z kartą”), (3) starszy paragon (do 180 dni, mediana), (4) Frisco × współczynnik — przybliżenie. Katalog Frisco odświeża się samo raz na 7 dni (nocna pętla), przycisk odświeżenia zakupów pobiera go od razu. Cache Biedronki wypełnia się w Zakupach (ręcznie z ekranu „Półka”); Dieta tylko go czyta.
