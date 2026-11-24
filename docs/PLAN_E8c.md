# PLAN E8c — „Konto Zepp” w ustawieniach profilu

Roadmapa: `docs/ROADMAP_E8.md`. Wartość etapu: użytkownik sam łączy konto Zepp (e-mail + hasło + OTP) i dostaje pełny import historii.
Bez pętli w tle (to E8d) i bez zmian w `targets.py` (E8e). Nic produkcyjnego poza kodem; połączenie prawdziwego konta dopiero przy wydaniu (E8-release).

## Decyzje
- **Stan `NeedsOtp` w pamięci procesu**, nie na dysku: `ZeppService._pending: dict[int, _Pending]` (`NeedsOtp` + `sent: bool` + czas utworzenia, TTL 15 min).
  Restart add-onu w trakcie łączenia = trzeba zacząć od nowa (akceptowane). Na dysku (`zepp_account`) są wyłącznie: `device_id`, ciasteczka Xiaomi, token Zepp, `fuid`, status — **nigdy hasło**.
- **`device_id` stały na konto:** `start` używa istniejącego `zepp_account.device_id` albo `zepp.new_device_id()`.
- **Wybór członka (fuid):** po zalogowaniu `members()`; 1 członek → od razu `fuid` i import; >1 → konto zapisane z `status='linked'`, `fuid=NULL`, UI pokazuje wybór.
- **Synchronizacja = `ZeppService.sync(person_id)`** (E8d użyje jej w pętli): `weight_records(since_ts=last_sync_ts)` → `body.import_records` → `set_zepp_last_sync(now)`.
  `ReconnectNeeded` (401) → **jedno** `zepp.refresh(cookies, device_id)`, zapis nowego tokenu/ciasteczek, ponowna próba; `ReconnectNeeded` z `refresh` → `set_zepp_status('reconnect', last_error=…)`.
  Pierwszy import: `since_ts=0`. Przy każdej porażce `last_error` zawiera tylko komunikat `ZeppError` (bezpieczny z założenia E8a), nigdy treść odpowiedzi.
- **Dzięki `to_thread`** wywołania sieciowe nie blokują pętli zdarzeń (`asyncio.to_thread`).
- **Komunikaty zwrotne** przez zapytanie `?zepp=<kod>[&n=<liczba>]` (wzorzec `?error=`); kody: `otp` (wpisz kod), `sent`, `ok` (+`n`), `wrong_code`, `login_failed`, `expired` (pending wygasł), `error`, `disconnected`. Hasła ani e-maila nie ma w URL-ach.
- **Flaga rollbacku:** opcja add-onu `zepp_enabled` (bool, domyślnie `true`); `false` → sekcja ukryta, trasy `/zepp/*` zwracają 404.
- **Ikony:** wyłącznie Lucide SVG (inline), bez emoji/PNG. Styl „Pojemniki” = istniejące klasy `dz-*` (`dz-details`, `dz-form`, `dz-btn`, `dz-note`, `dz-err`); nowe style tylko gdy konieczne, w tym samym pliku CSS.

## Trasy (router `web/zepp.py`, `make_zepp_router(settings, store, templates, service)`; wszystkie `POST`, `person_id ∈ {1,2}` inaczej 404)
| trasa | pola formularza | działanie |
|---|---|---|
| `/zepp/{pid}/start` | `email`, `password` | `start_login`; `Linked` → krok „członek/import”; `NeedsOtp` → `_pending`, konto `pending_otp`, `?zepp=otp` |
| `/zepp/{pid}/send` | — | `send_ticket` na stanie z `_pending` (brak/wygasły → `expired`); `?zepp=sent` |
| `/zepp/{pid}/verify` | `code` | `verify` → `refresh` → `Linked` → zapis konta → krok „członek/import”; zły kod → `wrong_code` (pending zostaje) |
| `/zepp/{pid}/member` | `fuid` | `fuid` musi być w `members()`; zapis; pełny import; `?zepp=ok&n=` |
| `/zepp/{pid}/sync` | — | `ZeppService.sync`; `?zepp=ok&n=` albo `error`/`expired`→„połącz ponownie” |
| `/zepp/{pid}/disconnect` | — | `delete_zepp_tokens`, kasuje `_pending`; historia zostaje; `?zepp=disconnected` |
Przekierowania 303 względne jak `_back` (`up=2`, Ingress). `create_app(..., zepp_http: Http | None = None, zepp_now: Callable[[], int] | None = None)` — szwy testowe.
`ZeppService(store, *, http=zepp.default_http, now=time.time)`.

## UI (`web/templates/_zepp.html`, dołączany w `profiles.html` jako osobny `<details class="dz-details">` „Konto Zepp” pod „Ustawienia”)
Stany sekcji (per osoba): brak konta → formularz e-mail + hasło (`autocomplete="off"`, `type=password`); `pending_otp` → przycisk „Wyślij kod” + pole kodu; `linked` bez `fuid` → lista członków (radio);
`linked` → „Połączono”, ostatnia synchronizacja (czas), liczba pomiarów, przyciski „Synchronizuj teraz” i „Rozłącz”; `reconnect` → komunikat „Połącz ponownie” + formularz logowania.
Baner z `?zepp=` nad sekcją (`role="alert"` dla błędów, `role="status"` dla `ok`/`sent`). Szablon nie dostaje `cookies_json`, `app_token` ani hasła — tylko `ZeppView` (status, `fuid`, `last_sync_ts`, `last_error`, liczba pomiarów, `members`).

## Kroki
1. Testy czerwone: `tests/test_zepp_service.py` (serwis z fałszywym `http` na fixture'ach z `test_zepp.py` albo z wstrzykniętym fałszywym modułem klienta — worker wybiera prostsze, bez zmiany `zepp.py`) i `tests/test_web_zepp.py` (TestClient).
2. `zepp_service.py`, `web/zepp.py`, `Settings.zepp_enabled` + `config.yaml` (options/schema), podpięcie routera w `create_app`, szablon + ewentualny CSS.
3. Pełna weryfikacja + Playwright 390 px i 1280 px (po wykonaniu, robię ja z działającą instancją dev i fałszywym `http`; ekran: brak konta, `pending_otp`, połączono).
4. Commit; `stage-end`.

## Rollback
`zepp_enabled: false` (ukrywa UI, trasy 404; dane zostają) albo `git revert`. Nic nie jest zapisane poza `zepp_account`/`body_measurement`/`weight` (addytywne, v12).

## Do potwierdzenia na żywo (E8-release, nie w tym etapie)
Kształt odpowiedzi `0106` i `securityStatus 16`; `send_ticket` przy `code` ≠ 0; czy logowanie z serwera wylogowuje aplikację w telefonie.

## Wykonawca
**Ryzyko → `sonnet-worker`** (logowanie, sekrety). **WORKTREE:** `wt new dieta-e8c`. Katalog roboczy `dieta/app`. Venv: `/config/addons/ha-dieta-app/dieta/app/.venv/bin/*` z `PYTHONPATH=src` (sprawdź, że `dieta.__file__` wskazuje na worktree).

**Pliki:**
1. `dieta/app/src/dieta/zepp_service.py` — nowy (`ZeppService`, `ZeppView`, wyjątki wewnętrzne niepotrzebne: wyniki jako enum/`Literal` kodów powyżej).
2. `dieta/app/src/dieta/web/zepp.py` — nowy router.
3. `dieta/app/src/dieta/web/app.py` — tylko: parametry `zepp_http`/`zepp_now` w `create_app`, budowa `ZeppService`, `include_router` gdy `settings.zepp_enabled`, dane `zepp` do kontekstu `index`, rozszerzenie mapowania `?zepp=` (bez ruszania istniejących tras).
4. `dieta/app/src/dieta/web/templates/_zepp.html` — nowy; `profiles.html` — tylko `{% include %}` w karcie osoby.
5. `dieta/app/src/dieta/web/static/<istniejący plik CSS>` — tylko niezbędne style.
6. `dieta/app/src/dieta/settings.py` + `dieta/config.yaml` — opcja `zepp_enabled` (wzorzec `cheaper_mode`: jak jest ładowana i walidowana w `settings.py`; `config.yaml` options + schema `bool`).
7. `dieta/app/tests/test_zepp_service.py`, `dieta/app/tests/test_web_zepp.py` — nowe; `tests/test_settings.py` — test opcji `zepp_enabled` (domyślnie true, `false` z options.json).

**Testy (najpierw czerwone):** serwis: start→`NeedsOtp` zapisuje pending i konto `pending_otp`, nie zapisuje hasła (grep po całej bazie SQLite i `repr`); `send` bez pending → `expired`; pending po TTL → `expired`; `verify` zły kod → `wrong_code`, pending zostaje; `verify` ok → konto `linked` z tokenem; 1 członek → import od razu (`body_measurement` + `weight`), >1 → `fuid=NULL`; `member` z fuid spoza listy odrzucony; `sync` przyrostowy (`since_ts=last_sync_ts`), 401 → jeden `refresh` i ponowna próba, `refresh` → `ReconnectNeeded` → status `reconnect`; `disconnect` zeruje tokeny, historia zostaje; stały `device_id` między `start` a ponownym `start`.
Web: każda trasa 303 z kodem `?zepp=`; 404 dla `person_id=3` i przy `zepp_enabled=False`; HTML strony głównej nie zawiera `app_token`, ciasteczek ani hasła (także po błędnym logowaniu — hasło nie wraca w odpowiedzi ani w `Location`); stany sekcji renderują się (brak konta / `pending_otp` / `linked` / `reconnect`); `?zepp=ok&n=284` pokazuje liczbę.

**Komenda testu:** `cd dieta/app && PYTHONPATH=src <venv>/ruff check . && <venv>/ruff format --check . && <venv>/mypy && <venv>/pytest -q` (pełny ~200 s, `timeout 600000`; w trakcie celuj w nowe pliki testów).

**DONE:** nowe testy zielone; pełny pytest/ruff/format/mypy czyste; `zepp.py` i `store.py` bez zmian (jeśli worker uzna, że brakuje metody w `Store` — np. licznik pomiarów — dopisuje ją minimalnie i zgłasza w raporcie); żadne hasło, token ani ciasteczko w HTML, URL-ach, logach i `last_error`.

**NIE RÓB:** edycji `zepp.py`, `body.py`, `targets.py`, `nutrition.py`, `chart.py`, istniejących migracji, CHANGELOG, wersji w `config.yaml`; pętli w tle (`lifespan`); logowania wartości formularza; sieci w testach (zawsze fałszywy `http`); osłabiania istniejących asercji; commitów.
