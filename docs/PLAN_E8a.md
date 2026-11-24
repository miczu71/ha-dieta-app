# PLAN E8a — klient Zepp (`dieta/zepp.py`)

Etap fundamentu z `docs/ROADMAP_E8.md`: sam moduł + testy, **bez UI, bez migracji, bez podpięcia do reszty kodu**.
Wartość: przetestowany klient logowania Xiaomi→Zepp i pobierania wag; E8b–E8d tylko go wołają.
Wzorzec przepływu (sprawdzony na żywo 2026-11-24): `~/dev/zepp-login/zepp_login.py`.

## Decyzje projektowe (moje, nie workera)
- **Tylko stdlib** (`urllib`, `hashlib`, `json`, `uuid`) — bez nowej zależności (`requests` nie jest w `pyproject.toml`).
- **Szew testowy = jedna funkcja `http`**, nie `opener` na `Request`: ciasteczka Xiaomi są ważne w całym przepływie,
  więc moduł zarządza nimi sam jako `dict[str, str]`, a nie przez `CookieJar`.
  ```python
  @dataclass(frozen=True)
  class HttpResult:
      status: int
      headers: dict[str, str]      # klucze małymi literami (np. "location")
      body: str
      set_cookies: dict[str, str]  # nazwa → wartość z nagłówków Set-Cookie
  Http = Callable[..., HttpResult]  # http(method, url, *, params=None, data=None, headers=None, cookies=None)
  ```
  `default_http` (urllib, bez automatycznych przekierowań, timeout 20 s) jest jedyną funkcją dotykającą sieci.
  Przekierowania obsługuje moduł (`_follow`, maks. 5), żeby zbierać ciasteczka z każdego kroku.
- **SSRF:** każdy URL (także `Location`) musi być `https` i mieć host `xiaomi.com`, `*.xiaomi.com`, `zepp.com` albo `*.zepp.com`;
  inaczej `ZeppError("niedozwolony adres")` — bez wypisywania adresu.
- **Sekrety:** hasło tylko jako argument `start_login` (hash MD5 wielkimi literami to wymóg protokołu Xiaomi, nie przechowywanie);
  żaden wyjątek, `repr` ani log nie zawiera hasła, ciasteczek, tokenów ani URL-i z zapytaniem. Klasy ze stanem z ciasteczkami/tokenem
  mają `field(repr=False)` na tych polach.
- **Delegacja `sonnet-worker`, nie `haiku-worker`:** moduł jest security-krytyczny (uwierzytelnianie, sekrety) — paczka oznaczona „ryzyko”.

## Wykonawca

**WORKTREE:** podaje rodzic (`wt new dieta-e8a`). Repo: `/config/addons/ha-dieta-app`, katalog roboczy `dieta/app`.

**Pliki (tylko te dwa, oba nowe):**
1. `dieta/app/src/dieta/zepp.py`
2. `dieta/app/tests/test_zepp.py`

**API modułu `zepp.py`:**
```python
Cookies = dict[str, str]

class ZeppError(Exception): ...            # komunikat po polsku, bezpieczny do logu/meta
class WrongCode(ZeppError): ...            # zły kod OTP
class ReconnectNeeded(ZeppError): ...      # sesja Xiaomi unieważniona → status „połącz ponownie”

@dataclass(frozen=True)
class ZeppToken: user_id: str; app_token: str = field(repr=False)

@dataclass(frozen=True)
class NeedsOtp:                            # stan między start_login → send_ticket → verify
    device_id: str; sid: str; context: str
    cookies: Cookies = field(repr=False)

@dataclass(frozen=True)
class Linked: token: ZeppToken; cookies: Cookies = field(repr=False)

@dataclass(frozen=True)
class Member: fuid: int; nickname: str; height: float | None

@dataclass(frozen=True)
class BodyRecord:
    ts: int                                # generatedTime (s)
    device_id: str | None
    weight_kg: float | None; fat_pct: float | None; water_pct: float | None; bone_kg: float | None
    muscle_rate: float | None; visceral: float | None; bmr_kcal: float | None; bmi: float | None
    impedance: float | None; body_score: float | None; metabolic_age: float | None
    raw: dict[str, Any]                    # cały `summary` (do raw_json w E8b)

def new_device_id() -> str                                  # uuid4().hex[:16]
def default_http(method, url, *, params=None, data=None, headers=None, cookies=None) -> HttpResult
def start_login(email, password, device_id, *, http=default_http) -> NeedsOtp | Linked
def send_ticket(state: NeedsOtp, *, http=default_http) -> NeedsOtp      # tylko e-mail (sendEmailTicket)
def verify(state: NeedsOtp, code: str, *, http=default_http) -> Cookies  # verifyEmail, flaga 8; ODWIEDZA `location`
def refresh(cookies: Cookies, device_id: str, *, http=default_http) -> Linked
def members(token: ZeppToken, *, http=default_http) -> list[Member]
def weight_records(token: ZeppToken, fuid: int, *, since_ts: int = 0, now_ts: int, http=default_http) -> list[BodyRecord]
```
Mapowanie `summary` → `BodyRecord`: `weight→weight_kg`, `fatRate→fat_pct`, `bodyWaterRate→water_pct`, `boneMass→bone_kg`,
`muscleRate→muscle_rate`, `visceralFat→visceral`, `metabolism→bmr_kcal`, `bmi`, `impedance`, `bodyScore→body_score`,
`muscleAge→metabolic_age`. Brak pola lub pusty napis → `None`.

**Protokół (1:1 z prototypem `~/dev/zepp-login/zepp_login.py`, przeczytaj go najpierw):**
- Stałe: `OAUTH` query, `UA`, adresy `account.xiaomi.com/oauth2/authorize`, `/pass/serviceLoginAuth2`, `/identity/list`,
  `/identity/auth/sendEmailTicket`, `/identity/auth/verifyEmail`, `account.zepp.com/v2/client/login`,
  `api-mifit.zepp.com/huami.health.scale.familymember.get.json`, `api-mifit.zepp.com/users/{uid}/members/{fuid}/weightRecords`.
- Odpowiedzi Xiaomi mają prefiks `&&&START&&&` → obetnij przed `json.loads`; niepoprawny JSON → `ZeppError("nieoczekiwana odpowiedź")`.
- Ciasteczko `deviceId` = `device_id` dodawane do każdego żądania do `xiaomi.com`.
- `start_login`: `authorize` z `deviceId` → jeśli `data.redirectUrl` ma `code=` → od razu `Linked` (przez client/login);
  inaczej `data.oauthLoginUrl` → GET → `sid, callback, _sign, qs` → POST `serviceLoginAuth2` (`hash` = MD5(hasło) wielkimi literami).
  Odpowiedź z `location` → `_follow` → `Location` z `code=` → `Linked`. Odpowiedź z `notificationUrl` (`securityStatus 16`) →
  GET `notificationUrl` (ustawia `identity_session`), wyciągnij `context` z jego parametru → `NeedsOtp`. Zły login/hasło (brak `location` i `notificationUrl`) → `ZeppError("logowanie odrzucone")`.
- `send_ticket`: POST `sendEmailTicket` z `sid`, `context`, `retry=0`; zwróć `NeedsOtp` z zaktualizowanymi ciasteczkami.
- `verify`: POST `verifyEmail` (`_flag=8`, `ticket`, `trust=true`) **bez** automatycznych przekierowań; `location` z JSON-a lub nagłówka
  **trzeba odwiedzić** (`_follow`) — ustawia ciasteczka zaufania (`passToken`, `userId`); zwróć scalone ciasteczka.
  Odpowiedź ze zbędnym kodem błędu `0106` (patrz uwaga niżej) → `WrongCode`.
- `refresh`: `authorize` z ciasteczkami → `data.redirectUrl` z `code=` (nie `data.code` — to liczba statusu!) → POST `client/login`
  (`grant_type=request_token`, `third_name=xiaomi-hm-mifit`, `device_id` = świeże uuid4, reszta pól jak w prototypie) → `token_info.user_id/app_token`.
  Brak `redirectUrl`/`code` → `ReconnectNeeded`; brak `app_token` → `ZeppError`.
- `members`: POST z `fuid=all`, nagłówek `apptoken`; HTTP 401 → `ReconnectNeeded`; inny ≠200 → `ZeppError("members: http N")`.
- `weight_records`: GET `…/weightRecords?limit=200&toTime=<ts>`; pętla od `now_ts`: zbierz `items` z `weightType == 0`,
  `next = d.get("next") or 0`; stop gdy `next` fałszywe, `next >= ts` (brak postępu) albo `next <= since_ts`; odrzuć rekordy `ts <= since_ts` (przyrostowość);
  wynik posortowany rosnąco po `ts`, bez duplikatów `ts`. HTTP 401 → `ReconnectNeeded`. Maks. 200 stron (bezpiecznik).

**Uwaga o fixture'ach:** kształt odpowiedzi `0106` i `securityStatus 16` znamy z notatki i prototypu, nie z zapisanej odpowiedzi.
Zrób fixture'y minimalne (inline w teście, zanonimizowane: `user_id="1000001"`, tokeny `FAKE-…`, e-mail `test@example.com`).
Kod `0106` rozpoznawaj w polach `result`, `code` i `error_code` (jako `"0106"` lub liczba 106). Potwierdzenie na żywo dopiero w E8c — dopisz to jako komentarz w kodzie.

**Testy (`test_zepp.py`, fałszywy `http` = lista oczekiwanych wywołań → odpowiedzi; żadnego ruchu sieciowego):**
1. `&&&START&&&` obcinany; niepoprawny JSON → `ZeppError`.
2. `start_login`: (a) zaufane urządzenie → `Linked`; (b) `securityStatus 16` + `notificationUrl` → `NeedsOtp` z `context`; (c) zły login → `ZeppError`;
   (d) hash w żądaniu = MD5 wielkimi literami; `deviceId` w ciasteczkach każdego żądania do xiaomi.
3. `send_ticket` zwraca stan ze scalonymi ciasteczkami; `verify` odwiedza `location` (asercja: wywołanie GET na `location`) i zwraca ciasteczka z kroku odwiedzin; `0106` → `WrongCode`.
4. `refresh`: `redirectUrl` z `code` → `Linked`; `data.code` (liczba) ignorowany; brak `redirectUrl` → `ReconnectNeeded`; brak `app_token` → `ZeppError`.
5. `members`: parsowanie listy; 401 → `ReconnectNeeded`; 500 → `ZeppError`.
6. `weight_records`: paginacja 3 stron (`next` malejące); filtr `weightType != 0`; pusta lista; `since_ts` ucina starsze; brak postępu `next >= ts` kończy pętlę; duplikaty `ts`; pola brakujące → `None`; bezpiecznik 200 stron.
7. SSRF: `Location` na `http://evil.example/` lub `https://evil.example/` → `ZeppError` bez adresu w komunikacie; `https://account.xiaomi.com.evil.example/` odrzucone (host kończący się na `.evil.example`).
8. **Brak wycieku:** dla każdej ścieżki błędu z testów 2–7 wywołaj z sekretami-markerami (hasło `PW-SECRET`, ciasteczko `COOKIE-SECRET`, token `TOKEN-SECRET`, `ticket`) i sprawdź, że `str(exc)`, `repr(exc)` oraz `repr(stan/token)` ich nie zawierają.
9. `default_http` z podstawionym openerem: nie podąża za przekierowaniem (3xx zwrócone jako wynik), `Set-Cookie` → `set_cookies`, nagłówki małymi literami, `HTTPError` 4xx/5xx zwracany jako `HttpResult` (nie wyjątek), błąd sieci (`URLError`) → `ZeppError("błąd sieci")`.

**Komenda testu (z `dieta/app` w worktree):** `ruff check . && ruff format --check . && mypy && pytest -q tests/test_zepp.py`
(pełne `pytest -q` robię sam po paczce).

**DONE:** oba pliki istnieją; `ruff check`, `ruff format --check`, `mypy` czyste; `pytest -q tests/test_zepp.py` zielone (≥ 30 przypadków);
`grep -nE "requests|import httpx" src/dieta/zepp.py` puste; żadna inna zmiana w `git status --short`.

**NIE RÓB:**
- nie ruszaj `store.py`, `web/`, `settings.py`, `pyproject.toml`, `config.yaml`, `CHANGELOG`, innych testów ani dokumentów;
- nie importuj `zepp` z żadnego innego modułu; nie dodawaj zależności;
- nie wykonuj żadnych prawdziwych żądań sieciowych ani nie czytaj `~/.secrets` i `~/dev/zepp-login/.state` (tam są prawdziwe tokeny);
- nie commituj (commit robię sam po `verifier`); nie dopisuj prawdziwych danych (e-mail, id konta, tokenów) do fixture'ów;
- nie wypisuj haseł/ciasteczek/tokenów w komunikatach wyjątków ani w `print`/logach.

## Rollback
`git revert` commita etapu (moduł nieużywany przez resztę kodu); nic w `/data`, nic w bazie.
