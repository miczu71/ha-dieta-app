# Dieta

Planer posiłków wielodniowych dla dwóch osób, który układa plan z przepisów i tego, co kupujecie, a porcje dobiera do kcal każdej osoby.

## Language

**Osoba**:
Jedna z dwóch osób, dla których powstaje plan; zawsze numer 1 albo 2. Historia (posiłki, waga, plany) odwołuje się do numeru, nie do nazwy.
_Avoid_: użytkownik, domownik, konto

**Nazwa osoby**:
Etykieta Osoby widoczna na ekranach i w powiadomieniach. Nie ma jej, dopóki nikt jej nie wpisze; wtedy Osoba jest podpisana „Osoba 1” albo „Osoba 2”.
_Avoid_: imię, login, nick

**Profil**:
Dane Osoby potrzebne do wyliczenia jej dziennego celu kcal: płeć, data urodzenia, wzrost, aktywność, cel i tempo zmiany wagi. Bez Profilu Osoby plan nie powstaje.
_Avoid_: konto, ustawienia osoby

**Data urodzenia**:
Jedyne źródło wieku Osoby. Może być przybliżona, gdy pochodzi ze starego zapisu samej liczby lat; Profil z taką datą prosi o poprawkę.
_Avoid_: rocznik, wiek (jako dane wejściowe)

**Wiek**:
Pełne lata Osoby w danym dniu, liczone z Daty urodzenia; nigdy nie jest wpisywany ręcznie.
_Avoid_: lata, age

**Plan**:
Zestaw posiłków na tydzień, wygenerowany i potem zatwierdzony. Zatwierdzony Plan zachowuje cele kcal z dnia generowania, nawet gdy Profil zmieni się później.
_Avoid_: jadłospis, menu

**Cel kcal**:
Dzienna liczba kcal Osoby wyliczona z Profilu, albo ręcznie ustawiona zamiast wyliczonej. Plan dzieli ją na posiłki.
_Avoid_: limit, norma

## Relationships

- Plan jest dla obu **Osób**; każda **Osoba** ma najwyżej jeden **Profil** i najwyżej jedną **Nazwę osoby**.
- **Wiek** wynika z **Daty urodzenia** i dnia, na który się go liczy; z **Wieku** wynika **Cel kcal** w **Profilu**.
- **Plan** zamraża **Cel kcal** z dnia generowania; ekrany „Dziś”, „Bilans” i karta Profilu liczą **Cel kcal** na dzisiaj.

## Example dialogue

> **Użytkownik:** Zmieniłam nazwę Osoby 2 na „Ola”. Czy stare plany pokażą „Ola”?
> **Ekspert:** Tak. Plany i dziennik trzymają numer Osoby, więc nazwa zmienia się wstecz wszędzie.
>
> **Użytkownik:** A jeśli „Ola” ma urodziny w środę, a Plan jest już zatwierdzony?
> **Ekspert:** Plan zostaje z Celem kcal z dnia generowania. Karta Profilu i Bilans policzą już nowy Wiek od środy.
>
> **Użytkownik:** Mam w Profilu dopisek „data przybliżona”.
> **Ekspert:** To stary zapis samego wieku. Wpisz prawdziwą Datę urodzenia, a dopisek zniknie.
