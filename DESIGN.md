Dieta to planer posiłków wielodniowych: gotujesz raz, jeść będziesz kilka dni. Styl nazywa się **Pojemniki**: sesja gotowania to rząd pojemników, każdy z wieczkiem w kolorze dnia.

## Zasady treści
- Pisz po polsku, na „ty”, krótko i konkretnie. Przyciski mówią, co się stanie: „Wymień”, „Rozdziel na dni”, „Z polubionych”.
- Dwie osoby pokazuj zawsze obok siebie: „Ty 612 kcal · Ona 540 kcal”. Liczby w `data` lub `figure`, jednostki małą czcionką w `muted`.
- Dni to skróty: Pn Wt Śr Cz Pt Sb Nd. Dane przykładowe oznaczaj słowem „przykład”.
- Bez emoji. Ikony wyłącznie SVG, zbiór Lucide (ISC), kreska 2 px, `currentColor`.

## Kolor
- Tło ekranu `bg`, karty i pojemniki `surface`, tekst `ink`, drugorzędny `muted`. Rozdzielaj linią `line`, nie cieniem.
- Jedna akcja główna na widok w `accent` z tekstem `on-accent`. Reszta to przycisk ghost.
- Dni tygodnia mają stałe kolory `day-1`…`day-7` i służą wyłącznie jako wieczka pojemników i kropki dnia. Obok koloru zawsze skrót dnia, kolor sam nie niesie informacji.
- Makro: `macro-protein`, `macro-carb`, `macro-fat`, zawsze z etykietą i gramami. Promocje i trwałość: `promo-bg` z `promo-ink`.
- Motyw jasny i ciemny to ten sam układ, inne wartości tokenów. Nigdy nie wpisuj kolorów literałem.

## Typografia
- Nagłówki i tekst: Outfit (`title`, `heading`, `subheading`, `body`, `label`). Liczby: DM Mono (`figure`, `data`) z cyframi tabelarycznymi.
- Etykiety wielkimi literami tylko w stylu `label`.

## Kształt i odstępy
- Karty `radius-lg`, pojemniki i przyciski `radius-md`, wieczka i pasek makro `radius-sm`, plakietki `radius-pill`.
- Odstępy tylko z `space-1…8`. Pojemnik ma dolną krawędź `shadow-box`, nic więcej nie rzuca cienia.
- Fokus: 2 px `accent` z odsunięciem 2 px, na każdym elemencie interaktywnym.

## Komponenty
`Button`, `Chip`, `MacroBar`, `RecipeCard`, `PlannerDay`, `ContainerRow`. Plakietki dotyczą jednej informacji (promocja, trwałość, sesja). Ekran planera to lista `PlannerDay`, ekran sesji to `ContainerRow` plus lista zakupów.

## Dla kodu add-onu
Źródłem prawdy jest kod add-onu: `dieta/app/src/dieta/web/static/app.css` (tokeny + komponenty `.dz-*`). Lustro z narzędzia projektowego: `~/dev/dieta-spike/design-system/project/` (`tokens.json`, `components/bundle.css`); po zmianie stylu w kodzie odtwórz ją tam.

## E2: Co kupujemy
Nawigacja `.dz-nav` (zakładki Profile / Co kupujemy, chip z liczbą pozycji do zatwierdzenia), lista `.dz-list` z wierszami `.dz-item`
(składnik, znacznik `.dz-chip--home` „prawdopodobnie w domu”, meta w DM Mono) i kolejka `.dz-pending` w kartach z formularzem
zatwierdzania. Adresy są względne: szablony dostają `root` ("" albo "../"), więc działają pod prefiksem Ingress.
