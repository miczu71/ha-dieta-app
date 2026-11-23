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
- `slot_*_pct` i opcje planera — udział slotów w kcal dnia i parametry układania tygodnia.

## Katalog
Zakładka „Katalog” pokazuje przepisy z kcal i makro na porcję oraz „znajomość”: ile składników (bez podstawowych: sól, pieprz, woda, olej, cukier)
kupujecie. Sort po znajomości, filtr progu, posiłku, źródła, polubionych i odrzuconych (wspólne dla domu). Przepisy bez kcal mają plakietkę „brak kcal”.
Pobieranie: ≤ 1 zapytanie/s, z poszanowaniem `robots.txt`; przepis raz pobrany nie jest pobierany ponownie. „Odśwież teraz” działa w tle.
Niepewne metadane przepisów i niezmapowane składniki trafiają do „Co kupujemy → Do zatwierdzenia”.

## Plan, Dziś i Bilans
„Plan” układa tydzień (sesje gotowania na kilka dni) i po zatwierdzeniu go zamraża. „Dziś” pozwala odhaczyć posiłek osoby (zjedzone, pominięte, zamiennik z własną nazwą i kcal) w oknie dziś i 7 dni wstecz; brak odhaczenia = „bez odpowiedzi” i nie liczy się do bilansu. „Bilans” sumuje kcal i makro dnia i tygodnia na osobę oraz pokazuje zostałe porcje sesji wielodniowych.
Baza w wersji 6 (tabela `meal_log`) nie jest czytelna dla 0.1.5 — nie wracaj do starszej wersji bez kopii `/data`.
