# Dieta

Planer posiłków wielodniowych dla dwóch osób. Etap E1: profile, cele kcal i makro, wpisy wagi.

Wartości kcal są szacunkowe. Aplikacja nie jest poradą medyczną.

## Opcje
- `person_1_name`, `person_2_name` — imiona w panelu. Reszta profilu jest w panelu i w `/data`.
- `log_level` — poziom logów.
- `crawl_daily_limit` — dzienny limit nowych przepisów na źródło (domyślnie 150).
- `familiar_threshold` — domyślny próg znajomości w Katalogu, w % (50).
- `sources` — włączone źródła przepisów: `respo`, `lidl`, `policzona`, `aniagotuje`.
- `refresh_time` — godzina nocnego odświeżenia: sync zakupów, potem crawl katalogu.

## Katalog
Zakładka „Katalog” pokazuje przepisy z kcal i makro na porcję oraz „znajomość”: ile składników (bez podstawowych: sól, pieprz, woda, olej, cukier)
kupujecie. Sort po znajomości, filtr progu, posiłku, źródła, polubionych i odrzuconych (wspólne dla domu). Przepisy bez kcal mają plakietkę „brak kcal”.
Pobieranie: ≤ 1 zapytanie/s, z poszanowaniem `robots.txt`; przepis raz pobrany nie jest pobierany ponownie. „Odśwież teraz” działa w tle.
Niepewne metadane przepisów i niezmapowane składniki trafiają do „Co kupujemy → Do zatwierdzenia”.
