# ROADMAP E8 — konto Zepp Life: historia wagi i składu ciała w Diecie (Dieta 0.6.0)

Wywiad z 2026-11-24, decyzje potwierdzone przez usera. Repo: `/config/addons/ha-dieta-app`, kod `dieta/app/src/dieta/`,
testy `dieta/app/tests/`. Pełny plan z kontekstem: `docs/PLAN_E8_zepp_roadmapa.md`. Po E8a–E8e jedno wydanie **Dieta 0.6.0**.
Komenda weryfikacji etapu: `cd dieta/app && ruff check . && ruff format --check . && mypy && pytest -q`.

## Cel i sukces
1. Użytkownik: Wy dwoje. W ustawieniach profilu raz łączysz konto Zepp (e-mail + hasło + jednorazowy kod OTP z e-maila).
2. Automatycznie: pełny import historii wagi i składu ciała, potem synchronizacja przyrostowa co 6 h; dzienna waga trafia do `weight`, więc obecne kcal i wykres działają.
3. Dieta używa danych: bieżąca waga do zapotrzebowania, BMR Katch-McArdle przy świeżym % tłuszczu, historia i wykresy, propozycja korekty kcal wg trendu.
4. Dopiero za zgodą (przycisk): zastosowanie korekty kcal (`kcal_override`), ponowne łączenie konta, rozłączenie.
5. Sukces: konto osoby 1 połączone z OTP, ≥284 pomiarów w historii, karta liczy BMR z Katch-McArdle, po tygodniu synchronizacja działa bez ręcznej pracy.

## Decyzje z wywiadu
- **Zepp zastępuje poprzedni E8** (lokalny odczyt wagi 625B z HA i przypisanie po najbliższej wadze) — patrz „Odrzucone”.
- **Bez hasła na dysku:** hasło jest tylko w żądaniu łączenia. Zapisujemy ciasteczka Xiaomi (`passToken`, `userId`, `deviceId`) i token Zepp w `/data`.
  Odświeżenie: `oauth2/authorize` z ciasteczkami → `data.redirectUrl` z `code` → `account.zepp.com/v2/client/login` (sprawdzone 2026-11-24).
  Gdy Xiaomi unieważni sesję: status „połącz ponownie” + jeden push.
- **Stały `deviceId` na konto** (to rozwiązanie blokady: ScaleConnect losował go przy każdym logowaniu, więc Xiaomi żądało weryfikacji za każdym razem).
- **Użycie:** bieżąca waga → kcal; Katch-McArdle; historia/wykresy; korekta wg trendu (za zgodą).
- **Dwie osoby = dwa konta Zepp:** każdy profil łączy własne konto (`zepp_account` per `person_id`); osoba 2 poda swoje dane logowania i swój OTP w swoim profilu.
  Lokalny odczyt z HA ignorujemy (bez przypisywania pomiarów po wadze).
- **Zakres danych:** tylko waga i skład ciała (waga, tłuszcz, mięśnie, woda, kości, trzewny, metabolizm, BMI, impedancja, wynik ciała, wiek metaboliczny). Bez aktywności z opaski.
- **Synchronizacja:** pełny import przy połączeniu, potem co 6 h + „Synchronizuj teraz”; limit 1 logowanie/h na konto.

## Fakty z odczytu i prototypu (2026-11-24)
- Prototyp `~/dev/zepp-login/zepp_login.py` (komendy `login`, `send`, `verify`, `members`, `weights`) przeszedł cały przepływ i pobrał 284 pomiary
  (2016-04-27…2026-10-27), jeden profil (fuid −1). Xiaomi oferuje tylko weryfikację e-mailem (flaga 8). `verifyEmail` zwraca `location`, który trzeba odwiedzić (ustawia ciasteczka zaufania).
- Endpointy Zepp (zgodne z kodem ScaleConnect `pkg/zepp`): `POST api-mifit.zepp.com/huami.health.scale.familymember.get.json` (`fuid=all&userid=`),
  `GET api-mifit.zepp.com/users/{uid}/members/{fuid}/weightRecords?limit=200&toTime=` z nagłówkiem `apptoken`, paginacja polem `next`, pomijamy `weightType != 0`.
- Odpowiedzi Xiaomi mają prefiks `&&&START&&&`; błąd Zepp `0106` = zły kod OAuth; `401 invalid token` = wygasły token aplikacji.
- Dieta (kod): tabela `weight(person_id, day, kg)` (`store.py:63`), `Store.add_weight/weights/latest_weight` (`store.py:615-640`), `_MIGRATIONS` (`store.py:49`, ostatnia v11, następna = 12),
  `BACKUP_BEFORE_VERSIONS` (`store.py:275`), BMR/TDEE w `targets.py:30-43` i `nutrition.py`, wykres `web/chart.py`, formularz `profiles.html` + `POST /profile/{id}` (`web/app.py:201`),
  pętla nocna `_nightly` (`web/app.py:98`), push `push.py`. HTTP: stdlib `urllib` ze wstrzykiwanym `opener` (wzorzec `todo.py`, `zakupy_client.py`).
- Wyjaśnione przez usera (2026-11-24): brak ważeń w Zepp po 27.10 to brak ważeń osoby 1 od tej daty (synchronizacja działa); ważenia z HA z 17 i 20.11 (~80 kg) to osoba 2.

## E8a — klient Zepp (`docs/PLAN_E8a.md`, sekcja Wykonawca)
Fundament: moduł `dieta/zepp.py`, bez UI i bez migracji. Czyste funkcje z wstrzykiwanym `opener`:
`start_login(email, pw, device_id)` → `NeedsOtp` lub `Linked`; `send_ticket`; `verify(state, code)` (z odwiedzeniem `location`); `refresh(cookies)`;
`members(token)`; `weight_records(token, fuid, to_time)` z paginacją.
Kroki: (1) testy czerwone na zanonimizowanych fixture'ach (`&&&START&&&`, `securityStatus 16`, `0106`, `401`, pusta lista, paginacja); (2) implementacja; (3) pytest + ruff + mypy; commit.
Wymóg: komunikaty błędów bez haseł, tokenów i ciasteczek. Rollback: `git revert` (moduł nieużywany).

## E8b — przechowywanie i import (`docs/PLAN_E8b.md`)
Migracja 12 (dodać 12 do `BACKUP_BEFORE_VERSIONS`, kopia `dieta.db.pre-v12`):
- `zepp_account(person_id PK, device_id, cookies_json, user_id, app_token, fuid, status, last_sync_ts, last_error)`; status: `pending_otp|linked|reconnect`;
- `body_measurement(person_id, ts, source, weight_kg, fat_pct, muscle_kg, water_pct, bone_kg, visceral, bmr_kcal, bmi, impedance, body_score, metabolic_age, raw_json, PK(person_id, ts, source))`.
Import zapisuje też dzienny wpis do `weight` (ostatni pomiar dnia; ręczny wpis z tego dnia wygrywa), więc `targets.py` i wykres działają bez zmian.
Testy: migracja z v11, import idempotentny, duplikaty, ręczny wpis wygrywa. Rollback: przywrócenie `dieta.db.pre-v12` (tabele addytywne).

## E8c — „Konto Zepp” w ustawieniach profilu (`docs/PLAN_E8c.md`)
Wartość: user sam łączy konto. Przepływ w `profiles.html`: e-mail + hasło → (OTP) „wyślij kod” → pole kodu → wybór członka (fuid) gdy >1 → pełny import z licznikiem.
Trasy `POST /zepp/{pid}/start|send|verify|member|sync|disconnect`. Hasło tylko w pamięci żądania; nigdy w logach, `meta` ani HTML.
Status w profilu: połączono / ostatnia synchronizacja / „połącz ponownie” / „rozłącz” (kasuje tokeny, historia zostaje).
Kroki: testy tras (FastAPI TestClient z wstrzykniętym klientem) → UI w stylu „Pojemniki” (ikony tylko Lucide SVG) → weryfikacja Playwrightem 390 px i 1280 px.
Rollback: flaga ukrywająca sekcję; dane zostają.

## E8d — synchronizacja w tle (`docs/PLAN_E8d.md`)
Pętla w `lifespan` (wzorzec `_nightly`): co 6 h przyrostowo od `last_sync_ts`; przy 401 jedno `refresh`; porażka refresh → `reconnect` + jeden push (`push.py`).
Przycisk „Synchronizuj teraz”. Limit 1 logowanie/h na konto (ochrona przed blokadą Xiaomi). Testy z fałszywym zegarem i klientem.
Rollback: opcja add-onu `zepp_sync: false` (domyślnie włączona dopiero po wydaniu).

## E8e — użycie w diecie (`docs/PLAN_E8e.md`)
- `targets.py`: BMR Katch-McArdle (370 + 21,6 × masa beztłuszczowa) gdy pomiar składu ciała ma ≤14 dni, inaczej Mifflin; karta pokazuje, którą formułą liczono.
- Karta i wykres (`web/chart.py`): waga + % tłuszczu + mięśnie, 90 dni; pełna historia w profilu; wiek ostatniego pomiaru widoczny.
- Korekta wg trendu: gdy 3-tygodniowy trend odbiega od `pace_kg_week` o więcej niż 0,25 kg/tydz., na karcie pojawia się propozycja ±kcal z przyciskiem „zastosuj”
  (ustawia `kcal_override`); nic automatycznie.
Testy: `test_targets.py`, `test_nutrition*.py` (graniczne: brak składu, stary pomiar, brak trendu). Rollback: `git revert`; dane zostają.

## E8-release — Dieta 0.6.0 (ostatni etap, bez pytania)
`simplify` → `code-review` ‖ `silent-failure-hunter` ‖ `pr-test-analyzer` ‖ review bezpieczeństwa (sekrety w `/data`, brak wycieku w logach i HTML, SSRF: tylko stałe domeny Xiaomi/Zepp)
→ `release` (CHANGELOG, `config.yaml` 0.6.0, tag nie-draft) → weryfikacja na żywo: add-on startuje, logi bez wyjątków, istnieje `dieta.db.pre-v12`,
połączenie konta osoby 1 z OTP, ≥284 pomiarów, karta z Katch-McArdle. Krok produkcyjny: aktualizacja add-onu przez Supervisor; rollback = poprzednia wersja + kopia `.pre-v12`.

## Ryzyka
- Xiaomi może okresowo żądać nowego OTP → status `reconnect` + push, bez pętli logowań.
- Zepp zwykle wylogowuje aplikację w telefonie przy logowaniu z innego urządzenia — sprawdzić przy E8c.
- Gdy ktoś długo się nie waży, dane są stare → karta pokazuje wiek ostatniego pomiaru, a Katch-McArdle wraca do Mifflina po 14 dniach.
- Osoba 2 łączy konto dopiero po E8c; do tego czasu jej waga jest wpisywana ręcznie. Waga na wspólnej wadze trafia do konta Zepp tej osoby, która się ważyła.
- Tokeny w SQLite trafiają do backupów HA (akceptowane, bo bez hasła).

## Odrzucone (2026-11-24)
- Poprzedni plan E8: lokalny odczyt wagi 625B z HA (encje `sensor.mi_body_composition_scale_625b_*`), przypisanie osobie po najbliższej ostatniej wadze,
  kolejka niepewnych pomiarów, migracja `weight_inbox`. Powód: user wybrał chmurę Zepp, która daje pełny skład ciała i przypisanie do profilu bez zgadywania.
  Decyzja usera 2026-11-24: lokalny pomiar ignorujemy, bo obie osoby mają konta Zepp.
- ScaleConnect (`alexxit/smartscaleconnect`) jako pośrednik: losowy `deviceId` + brak obsługi weryfikacji Xiaomi. Zastąpiony własnym klientem.

## Poza zakresem
- Aktywność z opaski (kroki, sen, tętno, spalone kcal).
- Import `zepp.csv` (stack `scaleconnect`, 278 pomiarów do 31.03.2026): dane są podzbiorem tego, co daje chmura.
- Zapis wagi z powrotem do Zepp lub Garmina.

## Stan
Plan zatwierdzony 2026-11-24. Zrobione: prototyp logowania i pobrania wag (`~/dev/zepp-login`). Wątpliwości z odczytu wyjaśnione (brak ważeń = brak ważeń; 80 kg = osoba 2). Kod w Diecie niezaczęty — następny krok: `docs/PLAN_E8a.md` z sekcją Wykonawca.
