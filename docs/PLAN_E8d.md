# PLAN E8d — synchronizacja Zepp w tle

Roadmapa: `docs/ROADMAP_E8.md`. Wartość etapu: po połączeniu konta dane wagi/składu odświeżają się same co 6 h, a utrata sesji kończy się jednym pushem.
Bez zmian UI i bez zmian w `zepp.py`/schemacie DB (do znaczników używamy `Store.get_meta/set_meta`, `store.py:1056`).

## Decyzje
- Opcja `zepp_sync` (bool, domyślnie `true`; działa tylko przy `zepp_enabled`). `false` = pętla nie startuje (ręczne „Synchronizuj teraz” dalej działa).
- Pętla: pierwszy przebieg 5 min po starcie, potem co 6 h. Błąd przebiegu → log samego typu wyjątku (wzorzec `_nightly`, `web/app.py:102`).
- Limit logowań: `ZeppService._call` przed `zepp.refresh` sprawdza `meta zepp_login_ts:{pid}`; różnica < 3600 s → `ReconnectNeeded("limit logowań")` **bez** ustawiania statusu `reconnect`
  (konto zostaje `linked`, `last_error` ustawiony; kolejny przebieg spróbuje ponownie). Po udanym `refresh` zapis znacznika. Logowanie ręczne (`start`/`verify`) bez limitu.
- Push przy utracie sesji: `run_once` po `sync` sprawdza, czy konto ma status `reconnect` i brak `meta zepp_reconnect_pushed:{pid}`; wtedy `push.send(settings.push_notify, "Dieta", "Połącz ponownie konto Zepp — <imię>", ścieżka panelu)` i znacznik.
  Znacznik czyszczony w `_finish_link` (udane połączenie). Pusty `push_notify` → bez pushu (znacznik nie ustawiany). Imię: `names.person_name(store, settings, pid)` (jak `push.py`).
- Poprawka z E8c (nieblokujące): `start` na koncie `linked`, gdy Xiaomi zażąda OTP, **nie nadpisuje** zapisanego konta (token, ciasteczka, status); stan OTP zostaje tylko w `_pending`.
  Konto bez wcześniejszego połączenia (brak rekordu albo `reconnect`/`pending_otp`) zapisuje się jak dotąd. UI nadal pokazuje krok OTP, gdy `_live_pending` istnieje (sprawdź `view()`).

## Testy (fałszywy zegar `now`, fałszywy `http`, fałszywy `sender`)
`tests/test_zepp_sync.py`: przebieg synchronizuje tylko konta `linked` z `fuid`; limit 1 logowania/h (drugi 401 w ciągu godziny → brak `refresh`, status `linked`);
401 → dokładnie jeden `refresh`; porażka refresh → status `reconnect` + jeden push, drugi przebieg bez pushu; po ponownym połączeniu znacznik czyszczony i push znów możliwy;
pusty `push_notify` → bez pushu; `zepp_sync: false` → pętla nie startuje; brak tokenów, ciasteczek i haseł w `caplog`.
`tests/test_zepp_service.py`: porzucone logowanie (OTP bez weryfikacji) na koncie `linked` → konto nadal `linked` i `sync` działa. `tests/test_settings.py`: `zepp_sync` domyślnie true, `false` z options.json.

## Kroki
1. Testy czerwone. 2. `settings.py`, `config.yaml` (options `zepp_sync: true`, schema `bool`), `translations/pl.yaml` i `en.yaml` (wzorzec `zepp_enabled`, linia 86). 3. `zepp_sync.py`, zmiany w `zepp_service.py`, start pętli w `lifespan`
(tylko gdy `settings.zepp_enabled and settings.zepp_sync` i w tym samym warunku co `_nightly`: `sync_runner is None and crawl_runner is None`). 4. ruff/format/mypy/pytest. 5. Commit.

## Rollback
`zepp_sync: false` albo `git revert`. Dane zostają.

## Wykonawca
**Ryzyko (tokeny, push) → `sonnet-worker`.** WORKTREE: `wt new /config/addons/ha-dieta-app dieta-e8d`. Katalog roboczy `dieta/app`; venv `/config/addons/ha-dieta-app/dieta/app/.venv/bin/*` z `PYTHONPATH=src` (sprawdź, że `dieta.__file__` wskazuje na worktree).
**Pliki:** `dieta/app/src/dieta/zepp_sync.py` (nowy: `run_once`, `start`/`_loop`), `zepp_service.py`, `web/app.py` (tylko `lifespan`), `settings.py`, `dieta/config.yaml`, `dieta/translations/{pl,en}.yaml`,
`tests/test_zepp_sync.py` (nowy), `tests/test_zepp_service.py`, `tests/test_settings.py`.
**Komenda testu:** `cd dieta/app && ruff check . && ruff format --check . && mypy && pytest -q`.
**Seamy testów:** `ZeppService(store, http=fake, now=clock)`, `run_once(service, store, settings, sender=fake)`; bez prawdziwej sieci i bez `sleep`.
**DONE:** wszystkie punkty „Testy” zielone, pełny pytest/ruff/format/mypy czyste, brak sekretów w logach.
**NIE RÓB:** zmian w `zepp.py`, schemacie/migracjach `store.py`, szablonach/UI, plikach spoza listy, commitów, `.env`/`~/.secrets`.
