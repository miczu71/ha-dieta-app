# Plan: Dieta E8 (v2) — konto Zepp Life w profilu, historia wagi i składu ciała

## Context
- Dziś (2026-11-24) prototyp `~/dev/zepp-login/zepp_login.py` przeszedł logowanie Xiaomi→Zepp:
  stały `deviceId` + jednorazowy kod e-mail (Xiaomi oferuje tylko e-mail, flaga 8) → ciasteczka
  `passToken`/`userId`/`deviceId` → `oauth2/authorize` zwraca `data.redirectUrl` z `code` →
  `account.zepp.com/v2/client/login` → `app_token`. Pobrał 284 pomiary (2016-04-27…2026-10-27,
  jeden profil (osoba 1, fuid −1)) z pełnym składem ciała.
- User chce to w add-onie Dieta: logowanie w ustawieniach profilu, jednorazowy OTP, cała historia
  zapisana i używana do diety. Decyzje z wywiadu:
  - **Zepp zastępuje stary E8** (lokalny BLE 625B + przypisanie po najbliższej wadze) — stary plan idzie do „Odrzucone”.
  - **Bez hasła na dysku**: hasło tylko w żądaniu łączenia; trzymamy ciasteczka Xiaomi + token Zepp w `/data`.
    Odświeżenie tokenu = `authorize` z ciasteczkami (sprawdzone dziś). Gdy Xiaomi unieważni sesję → status „połącz ponownie”.
  - **Użycie**: bieżąca waga do kcal, BMR Katch-McArdle przy świeżym % tłuszczu, historia/wykresy, propozycja korekty kcal wg trendu (za zgodą).
  - **Zakres**: tylko waga i skład ciała (bez aktywności z opaski).
  - Synchronizacja: pełny import przy połączeniu, potem przyrostowo co 6 h + „Synchronizuj teraz” (domyślne, nie pytane).

Repo: `/config/addons/ha-dieta-app` (kod `dieta/app/src/dieta/`, FastAPI+Jinja, SQLite z `_MIGRATIONS` w `store.py:49`, ostatnia v11; HTTP przez stdlib `urllib` ze wstrzykiwanym `opener`, wzorzec `todo.py`/`zakupy_client.py`; testy `cd dieta/app && ruff check . && ruff format --check . && mypy && pytest -q`).
Brudne drzewo: `M docs/ROADMAP.md`, `?? docs/ROADMAP_E8.md` (moje z wcześniejszej sesji, niezacommitowane).

## Co robię teraz (po akceptacji) — tylko dokumenty
1. Kopia tego planu → `docs/PLAN_E8_zepp_roadmapa.md`.
2. `docs/ROADMAP_E8.md` przepisany na wersję Zepp (sekcje niżej); stary plan BLE skrócony do sekcji „Odrzucone (2026-11-24)” z powodem.
3. `docs/ROADMAP.md` → sekcja „Najbliższe punkty”: E8 = Zepp, link do `ROADMAP_E8.md`.
4. Commit tylko `docs/` na `main` (bez push/wydania), opis po polsku.
5. Pamięć: aktualizacja `project_dieta_addon.md` (decyzja Zepp, przepływ logowania, pułapki) + linia w MEMORY.md.
Implementacja etapów startuje osobno (każdy etap: `docs/PLAN_E8x.md` z sekcją Wykonawca przed kodem).

## Etapy (treść ROADMAP_E8.md)

**E8a — klient Zepp (`dieta/zepp.py`), bez UI.** Czyste funkcje z `opener`:
`start_login(email, pw, device_id) -> NeedsOtp(ctx, sid, cookies) | Linked`, `send_ticket(state)`,
`verify(state, code) -> cookies` (z odwiedzeniem `location`!), `refresh(cookies) -> ZeppToken`
(authorize → `redirectUrl` code → client/login), `members(token)`, `weight_records(token, fuid, to_time)` z paginacją `next`, filtr `weightType==0`.
Testy na zanonimizowanych fixture'ach odpowiedzi (prefiks `&&&START&&&`, securityStatus 16, 0106, 401). Błędy bez tokenów/haseł w treści.
Wartość: przetestowany klient. Rollback: usunięcie modułu.

**E8b — przechowywanie (migracja 12, kopia bazy przed v12).**
- `zepp_account(person_id PK, device_id, cookies_json, user_id, app_token, fuid, status, last_sync_ts, last_error)` — status: `pending_otp|linked|reconnect`.
- `body_measurement(person_id, ts, source, weight_kg, fat_pct, muscle_kg, water_pct, bone_kg, visceral, bmr_kcal, bmi, impedance, body_score, metabolic_age, raw_json, PK(person_id, ts, source))`.
- Import zapisuje też dzienny wpis do `weight` (ostatni pomiar dnia; ręczny wpis z tego dnia wygrywa) → obecne `targets.py`/wykres działają bez zmian.
Testy migracji i importu (idempotencja, duplikaty). Rollback: przywrócenie kopii bazy sprzed v12.

**E8c — „Konto Zepp” w ustawieniach profilu (`profiles.html`, `web/app.py`).**
Przepływ: e-mail + hasło → (jeśli OTP) „wyślij kod” → pole kodu → wybór członka (fuid) gdy >1 → pełny import z licznikiem.
Trasy `POST /zepp/{pid}/start|send|verify|member|sync|disconnect`. Hasło tylko w pamięci żądania; nigdy w logach/meta/HTML.
Status w profilu: połączono / ostatnia synchronizacja / „połącz ponownie” / „rozłącz” (kasuje tokeny, zostawia historię).
Rollback: wyłączenie sekcji (flaga) — dane zostają.

**E8d — synchronizacja w tle.** Pętla w `lifespan` (wzorzec `_nightly`, `web/app.py:98`): co 6 h przyrostowo od `last_sync_ts`, przy 401 jedno `refresh`, przy porażce refresh → `reconnect` + jeden push (`push.py`). Przycisk „Synchronizuj teraz”. Limit: max 1 logowanie/h na konto (ochrona przed blokadą Xiaomi).

**E8e — użycie w diecie.**
- `targets.py`: BMR Katch-McArdle (370 + 21,6 × LBM) gdy % tłuszczu ≤ 14 dni, inaczej Mifflin; na karcie widać, którą formułą liczono.
- Karta/wykres (`web/chart.py`): waga + % tłuszczu + mięśnie, 90 dni; historia pełna w profilu.
- Korekta wg trendu: gdy 3-tyg. trend odbiega od `pace_kg_week` o > 0,25 kg/tydz. → propozycja na karcie (±kcal) z przyciskiem „zastosuj” (ustawia `kcal_override`); nic automatycznie.
Testy: `test_targets.py`, `test_nutrition*.py`.

**E8-release — Dieta 0.6.0**: simplify → code-review (+ silent-failure-hunter, pr-test-analyzer, security review: sekrety w /data, brak wycieku w logach/HTML) → release → weryfikacja na żywo: połączenie konta osoby 1 z OTP, 284+ pomiarów w historii, karta liczy z Katch-McArdle.

## Ryzyka
- Xiaomi może okresowo żądać nowego OTP → status `reconnect` + push, nie pętla logowań.
- Zepp zwykle wylogowuje aplikację w telefonie przy logowaniu z innego urządzenia — sprawdzić przy E8c.
- Chmura nie ma ważeń po 27.10 (bieżące idą BLE→HA). Bez naprawy synchronizacji w aplikacji Zepp dieta będzie miała stare dane → user sprawdza Zepp Life na telefonie; karta pokazuje wiek ostatniego pomiaru.
- Rozbieżność ~80 kg (HA, 17/20.11) vs 89 kg (Zepp, 27.10) — wyjaśnić z userem przed E8e (inna osoba na wadze?).
- Druga osoba: brak danych w Zepp; może połączyć własne konto albo wpisywać ręcznie.
- Tokeny w SQLite trafiają do backupów HA (akceptowane: bez hasła).

## Weryfikacja (dla tego kroku — dokumenty)
- `git -C /config/addons/ha-dieta-app diff --stat` pokazuje tylko `docs/`; `git log -1` = commit docs.
- `ROADMAP_E8.md` zawiera E8a–E8e + release, rollback w każdym etapie, sekcję „Odrzucone”.
