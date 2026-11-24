"""Klient Zepp Life: logowanie przez konto Xiaomi (stały deviceId) i pobieranie pomiarów wagi.

Przepływ 1:1 z prototypem sprawdzonym na żywo 2026-11-24. Tylko stdlib; wywoływane w wątku.

Jedyną funkcją dotykającą sieci jest ``default_http`` (szew testowy: parametr ``http``). Ciasteczka Xiaomi
moduł prowadzi sam jako ``dict`` (są ważne w całym przepływie), przekierowania też obsługuje sam.

Sekrety: hasło tylko jako argument ``start_login`` (MD5 wielkimi literami to wymóg protokołu Xiaomi).
Komunikaty wyjątków nie zawierają hasła, ciasteczek, tokenów ani adresów URL.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any

TIMEOUT_S = 20
MAX_REDIRECTS = 5
MAX_PAGES = 200  # bezpiecznik paginacji weightRecords

Cookies = dict[str, str]

OAUTH = (
    "_json=true&client_id=428135909242707968&pt=1&"
    "redirect_uri=https://api-mifit-cn.huami.com/huami.health.loginview.do&response_type=code"
)
UA = "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"

URL_AUTHORIZE = "https://account.xiaomi.com/oauth2/authorize"
URL_LOGIN_AUTH = "https://account.xiaomi.com/pass/serviceLoginAuth2"
URL_IDENTITY_LIST = "https://account.xiaomi.com/identity/list"
URL_SEND_EMAIL = "https://account.xiaomi.com/identity/auth/sendEmailTicket"
URL_VERIFY_EMAIL = "https://account.xiaomi.com/identity/auth/verifyEmail"
URL_ZEPP_LOGIN = "https://account.zepp.com/v2/client/login"
URL_MEMBERS = "https://api-mifit.zepp.com/huami.health.scale.familymember.get.json"
URL_WEIGHTS = "https://api-mifit.zepp.com/users/{uid}/members/{fuid}/weightRecords"

_START_PREFIX = "&&&START&&&"
_ALLOWED_DOMAINS = ("xiaomi.com", "zepp.com")
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
# Kod „zły kod weryfikacyjny” Xiaomi. Kształt odpowiedzi znamy z notatki i prototypu, nie z zapisu —
# rozpoznajemy go w polach result/code/error_code (napis "0106" lub liczba 106).
# Potwierdzenie na żywo dopiero w E8c.
_WRONG_CODE = "0106"


class ZeppError(Exception):
    """Błąd klienta Zepp. Komunikat jest bezpieczny do zapisu w meta/logu."""


class WrongCode(ZeppError):
    """Zły kod weryfikacyjny (OTP)."""


class ReconnectNeeded(ZeppError):
    """Sesja Xiaomi/Zepp unieważniona — wymagane ponowne połączenie konta."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: dict[str, str]  # klucze małymi literami (np. "location")
    body: str
    set_cookies: dict[str, str]  # nazwa → wartość z nagłówków Set-Cookie


Http = Callable[..., HttpResult]  # http(method, url, *, params=None, data=None, headers=None, cookies=None)


@dataclass(frozen=True)
class ZeppToken:
    user_id: str
    app_token: str = field(repr=False)


@dataclass(frozen=True)
class NeedsOtp:
    """Stan między ``start_login`` → ``send_ticket`` → ``verify``."""

    device_id: str
    sid: str
    context: str
    cookies: Cookies = field(repr=False)


@dataclass(frozen=True)
class Linked:
    token: ZeppToken
    cookies: Cookies = field(repr=False)


@dataclass(frozen=True)
class Member:
    fuid: int
    nickname: str
    height: float | None


@dataclass(frozen=True)
class BodyRecord:
    ts: int  # generatedTime (s)
    device_id: str | None
    weight_kg: float | None
    fat_pct: float | None
    water_pct: float | None
    bone_kg: float | None
    muscle_rate: float | None
    visceral: float | None
    bmr_kcal: float | None
    bmi: float | None
    impedance: float | None
    body_score: float | None
    metabolic_age: float | None
    raw: dict[str, Any]  # cały `summary` (do raw_json)


def new_device_id() -> str:
    return uuid.uuid4().hex[:16]


# --- warstwa sieciowa -------------------------------------------------------------------------------


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Przekierowanie kończy się ``HTTPError`` 3xx — moduł sam je obsługuje (zbiera ciasteczka)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _cookie_pairs(headers: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in headers.get_all("Set-Cookie") or []:
        name, sep, rest = str(raw).partition("=")
        if sep and name.strip():
            pairs[name.strip()] = rest.split(";", 1)[0].strip()
    return pairs


def _lower_headers(headers: Any) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def default_http(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> HttpResult:
    """Jedyne miejsce dotykające sieci. Bez automatycznych przekierowań; 4xx/5xx zwraca jako wynik."""
    if params:
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
    hdrs = dict(headers or {})
    if body is not None:
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    if cookies:
        hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with closing(_OPENER.open(req, timeout=TIMEOUT_S)) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            return HttpResult(resp.status, _lower_headers(resp.headers), text, _cookie_pairs(resp.headers))
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read().decode("utf-8", errors="replace")
        except OSError:
            text = ""
        result = HttpResult(exc.code, _lower_headers(exc.headers), text, _cookie_pairs(exc.headers))
        exc.close()
        return result
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError):
        raise ZeppError("błąd sieci") from None


def _host_allowed(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or port not in (None, 443):
        return False
    return any(host == d or host.endswith("." + d) for d in _ALLOWED_DOMAINS)


def _is_xiaomi(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == "xiaomi.com" or host.endswith(".xiaomi.com")


def _parse(text: str) -> dict[str, Any]:
    """Obcina prefiks ``&&&START&&&`` i parsuje JSON (obiekt); inaczej ``ZeppError``."""
    if text.startswith(_START_PREFIX):
        text = text[len(_START_PREFIX) :]
    try:
        value = json.loads(text)
    except ValueError:
        raise ZeppError("nieoczekiwana odpowiedź") from None
    if not isinstance(value, dict):
        raise ZeppError("nieoczekiwana odpowiedź")
    return value


def _extract_code(url: str) -> str | None:
    values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("code")
    return values[0] if values and values[0] else None


class _Session:
    """Wywołania ``http`` z kontrolą adresów (SSRF) i prowadzeniem ciasteczek Xiaomi."""

    def __init__(self, http: Http, cookies: Cookies, device_id: str = "") -> None:
        self.http = http
        self.cookies: Cookies = dict(cookies)
        if device_id:
            self.cookies["deviceId"] = device_id

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResult:
        if not _host_allowed(url):
            raise ZeppError("niedozwolony adres")
        xiaomi = _is_xiaomi(url)
        res = self.http(
            method,
            url,
            params=params,
            data=data,
            headers={"User-Agent": UA, **(headers or {})},
            cookies=dict(self.cookies) if xiaomi else None,
        )
        if xiaomi:
            self.cookies.update(res.set_cookies)
        return res

    def follow(self, url: str) -> tuple[HttpResult, str | None]:
        """GET z ręcznym podążaniem za przekierowaniami (maks. 5), zbiera ciasteczka z każdego kroku.

        Zatrzymuje się (bez odwiedzania) na adresie z ``code=`` — to kod OAuth do wymiany w Zepp.
        """
        for _ in range(MAX_REDIRECTS + 1):
            res = self.request("GET", url)
            location = res.headers.get("location", "")
            if res.status not in _REDIRECT_STATUSES or not location:
                return res, None
            url = urllib.parse.urljoin(url, location)
            code = _extract_code(url)
            if code:
                return res, code
        raise ZeppError("zbyt wiele przekierowań")


# --- logowanie --------------------------------------------------------------------------------------


def _zepp_login(session: _Session, code: str) -> Linked:
    form = {
        "app_name": "com.xiaomi.hm.health",
        "app_version": "6.14.0",
        "code": code,
        "country_code": "CN",
        "device_id": str(uuid.uuid4()),
        "device_model": "phone",
        "dn": "api-mifit.zepp.com",
        "grant_type": "request_token",
        "third_name": "xiaomi-hm-mifit",
    }
    payload = _parse(session.request("POST", URL_ZEPP_LOGIN, data=form).body)
    info = payload.get("token_info")
    if not isinstance(info, dict) or not info.get("app_token") or not info.get("user_id"):
        raise ZeppError("Zepp nie zwrócił tokena")
    token = ZeppToken(user_id=str(info["user_id"]), app_token=str(info["app_token"]))
    return Linked(token=token, cookies=dict(session.cookies))


def _data(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("data")
    return value if isinstance(value, dict) else {}


def start_login(email: str, password: str, device_id: str, *, http: Http = default_http) -> NeedsOtp | Linked:
    session = _Session(http, {}, device_id)
    authorize = _parse(session.request("GET", f"{URL_AUTHORIZE}?{OAUTH}").body)
    data = _data(authorize)
    redirect = data.get("redirectUrl")
    if redirect:  # zaufane urządzenie: kod OAuth w przekierowaniu
        code = _extract_code(str(redirect))
        if not code:
            raise ZeppError("brak kodu w odpowiedzi")
        return _zepp_login(session, code)
    login_url = data.get("oauthLoginUrl")
    if not login_url:
        raise ZeppError("nieoczekiwana odpowiedź")
    step = _parse(session.request("GET", str(login_url)).body)
    try:
        form = {
            "_json": "true",
            "hash": hashlib.md5(password.encode()).hexdigest().upper(),
            "sid": str(step["sid"]),
            "callback": str(step["callback"]),
            "_sign": str(step["_sign"]),
            "qs": str(step["qs"]),
            "user": email,
        }
    except KeyError:
        raise ZeppError("nieoczekiwana odpowiedź") from None
    auth = _parse(session.request("POST", URL_LOGIN_AUTH, data=form).body)
    location = auth.get("location")
    if location:
        _, code = session.follow(str(location))
        if not code:
            raise ZeppError("brak kodu w przekierowaniu")
        return _zepp_login(session, code)
    notification = auth.get("notificationUrl")
    if not notification:
        raise ZeppError("logowanie odrzucone")
    context_values = urllib.parse.parse_qs(urllib.parse.urlsplit(str(notification)).query).get("context")
    if not context_values or not context_values[0]:
        raise ZeppError("nieoczekiwana odpowiedź")
    context = context_values[0]
    session.request("GET", str(notification))  # ustawia ciasteczko identity_session
    session.request(
        "GET",
        URL_IDENTITY_LIST,
        params={"sid": form["sid"], "supportedMask": 0, "context": context, "_locale": "en_US"},
    )
    return NeedsOtp(device_id=device_id, sid=form["sid"], context=context, cookies=dict(session.cookies))


def _otp_form(state: NeedsOtp) -> dict[str, Any]:
    return {"_json": "true", "sid": state.sid, "context": state.context}


def send_ticket(state: NeedsOtp, *, http: Http = default_http) -> NeedsOtp:
    """Wysyła kod weryfikacyjny e-mailem (sendEmailTicket)."""
    session = _Session(http, state.cookies, state.device_id)
    res = session.request(
        "POST",
        URL_SEND_EMAIL,
        params={"_dc": "1"},
        data={"retry": 0, "icode": "", **_otp_form(state)},
    )
    payload = _parse(res.body)
    if payload.get("code") not in (None, 0, "0"):
        raise ZeppError("Xiaomi nie wysłało kodu")
    return NeedsOtp(
        device_id=state.device_id, sid=state.sid, context=state.context, cookies=dict(session.cookies)
    )


def _is_wrong_code(payload: dict[str, Any]) -> bool:
    return any(str(payload.get(key)) in (_WRONG_CODE, "106") for key in ("result", "code", "error_code"))


def verify(state: NeedsOtp, code: str, *, http: Http = default_http) -> Cookies:
    """Potwierdza kod (verifyEmail, flaga 8) i odwiedza ``location`` — ustawia ciasteczka zaufania."""
    session = _Session(http, state.cookies, state.device_id)
    res = session.request(
        "POST",
        URL_VERIFY_EMAIL,
        params={"_flag": 8, "_json": "true"},
        data={"ticket": code, "trust": "true", **_otp_form(state)},
    )
    try:
        payload = _parse(res.body)
    except ZeppError:
        payload = {}  # przy przekierowaniu treść bywa pusta; liczy się nagłówek location
    if _is_wrong_code(payload):
        raise WrongCode("zły kod weryfikacyjny")
    location = payload.get("location") or res.headers.get("location")
    if not location:
        raise ZeppError("weryfikacja odrzucona" if payload else "nieoczekiwana odpowiedź")
    session.follow(str(location))
    return dict(session.cookies)


def refresh(cookies: Cookies, device_id: str, *, http: Http = default_http) -> Linked:
    """Odnawia token Zepp z zapisanych ciasteczek Xiaomi; brak kodu → ``ReconnectNeeded``."""
    session = _Session(http, cookies, device_id)
    authorize = _parse(session.request("GET", f"{URL_AUTHORIZE}?{OAUTH}").body)
    redirect = _data(authorize).get("redirectUrl")  # uwaga: `data.code` to liczba statusu, nie kod OAuth
    code = _extract_code(str(redirect)) if redirect else None
    if not code:
        raise ReconnectNeeded("sesja Xiaomi wygasła")
    return _zepp_login(session, code)


# --- dane -------------------------------------------------------------------------------------------


def _opt_float(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(str(value))
    except ValueError:
        return None


def _api(
    session: _Session, method: str, url: str, token: ZeppToken, what: str, **kwargs: Any
) -> dict[str, Any]:
    res = session.request(method, url, headers={"apptoken": token.app_token}, **kwargs)
    if res.status == 401:
        raise ReconnectNeeded("token Zepp unieważniony")
    if res.status != 200:
        raise ZeppError(f"{what}: http {res.status}")
    return _parse(res.body)


def members(token: ZeppToken, *, http: Http = default_http) -> list[Member]:
    session = _Session(http, {})
    payload = _api(
        session,
        "POST",
        URL_MEMBERS,
        token,
        "members",
        data={"fuid": "all", "userid": token.user_id},
    )
    rows = _data(payload).get("list")
    try:
        return [
            Member(
                fuid=int(row["fuid"]),
                nickname=str(row.get("nickname") or ""),
                height=_opt_float(row.get("height")),
            )
            for row in rows or []
        ]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ZeppError("nieoczekiwana odpowiedź") from None


def _record(item: dict[str, Any]) -> BodyRecord | None:
    try:
        ts = int(item["generatedTime"])
    except (KeyError, TypeError, ValueError):
        return None
    summary = item.get("summary")
    s: dict[str, Any] = summary if isinstance(summary, dict) else {}
    device = item.get("deviceId")
    return BodyRecord(
        ts=ts,
        device_id=None if device in (None, "") else str(device),
        weight_kg=_opt_float(s.get("weight")),
        fat_pct=_opt_float(s.get("fatRate")),
        water_pct=_opt_float(s.get("bodyWaterRate")),
        bone_kg=_opt_float(s.get("boneMass")),
        muscle_rate=_opt_float(s.get("muscleRate")),
        visceral=_opt_float(s.get("visceralFat")),
        bmr_kcal=_opt_float(s.get("metabolism")),
        bmi=_opt_float(s.get("bmi")),
        impedance=_opt_float(s.get("impedance")),
        body_score=_opt_float(s.get("bodyScore")),
        metabolic_age=_opt_float(s.get("muscleAge")),
        raw=dict(s),
    )


def weight_records(
    token: ZeppToken, fuid: int, *, since_ts: int = 0, now_ts: int, http: Http = default_http
) -> list[BodyRecord]:
    """Pomiary wagi członka ``fuid`` (``-1`` = osoba główna) nowsze niż ``since_ts``, rosnąco po ``ts``."""
    session = _Session(http, {})
    url = URL_WEIGHTS.format(uid=urllib.parse.quote(token.user_id, safe=""), fuid=int(fuid))
    found: dict[int, BodyRecord] = {}
    ts = now_ts
    for _ in range(MAX_PAGES):
        page = _api(session, "GET", url, token, "weightRecords", params={"limit": 200, "toTime": ts})
        items = page.get("items")
        try:
            for item in items or []:
                if item.get("weightType", 0) != 0:
                    continue
                record = _record(item)
                if record is not None and record.ts > since_ts:
                    found.setdefault(record.ts, record)
            nxt = int(page.get("next") or 0)
        except (TypeError, ValueError, AttributeError):
            raise ZeppError("nieoczekiwana odpowiedź") from None
        if not nxt or nxt >= ts or nxt <= since_ts:
            break
        ts = nxt
    return [found[k] for k in sorted(found)]
