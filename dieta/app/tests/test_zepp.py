"""Testy klienta Zepp. Cały ruch sieciowy jest fałszywy (szew: parametr ``http``)."""

import hashlib
import io
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Any

import pytest

from dieta import zepp
from dieta.zepp import (
    BodyRecord,
    HttpResult,
    Linked,
    Member,
    NeedsOtp,
    ReconnectNeeded,
    WrongCode,
    ZeppError,
    ZeppToken,
)

PW = "PW-SECRET"
COOKIE = "COOKIE-SECRET"
TOKEN_SECRET = "TOKEN-SECRET"
TICKET = "TICKET-SECRET"
SECRETS = (PW, COOKIE, TOKEN_SECRET, TICKET)

DEVICE = "abcdef0123456789"
EMAIL = "test@example.com"
LOGIN_URL = "https://account.xiaomi.com/pass/serviceLogin?sid=miothealth"
NOTIFY_URL = "https://account.xiaomi.com/identity/authStart?sid=x&context=CTX123&_locale=en_US"
LOC_URL = "https://account.xiaomi.com/sts?d=1"
CODE_URL = "https://api-mifit-cn.huami.com/huami.health.loginview.do?code=CODE1&state=x"
TOKEN = ZeppToken("1000001", "FAKE-APP-TOKEN")


def R(  # noqa: N802
    body: Any = None,
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    prefix: bool = True,
) -> HttpResult:
    text = ""
    if isinstance(body, str):
        text = body
    elif body is not None:
        text = ("&&&START&&&" if prefix else "") + json.dumps(body)
    return HttpResult(status, {k.lower(): v for k, v in (headers or {}).items()}, text, dict(cookies or {}))


class Fake:
    """Skrypt oczekiwanych wywołań: (metoda, adres bez zapytania, odpowiedź)."""

    def __init__(self, *steps: tuple[str, str, HttpResult]) -> None:
        self.steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        data: Any = None,
        headers: Any = None,
        cookies: Any = None,
    ) -> HttpResult:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "data": data,
                "headers": headers,
                "cookies": cookies,
            }
        )
        assert self.steps, f"nieoczekiwane wywołanie {method} {url}"
        exp_method, exp_url, result = self.steps.pop(0)
        assert (method, url.split("?")[0]) == (exp_method, exp_url)
        return result


def _zepp_ok(**extra: Any) -> HttpResult:
    return R({"token_info": {"user_id": "1000001", "app_token": "FAKE-APP-TOKEN"}, **extra}, prefix=False)


ZEPP_LOGIN = ("POST", zepp.URL_ZEPP_LOGIN)
AUTHORIZE = ("GET", zepp.URL_AUTHORIZE)


def _login_steps(auth_response: HttpResult) -> list[tuple[str, str, HttpResult]]:
    """authorize → oauthLoginUrl → formularz → serviceLoginAuth2 (odpowiedź zadana)."""
    return [
        (*AUTHORIZE, R({"code": 0, "data": {"oauthLoginUrl": LOGIN_URL}})),
        ("GET", LOGIN_URL.split("?")[0], R({"sid": "SID1", "callback": "CB", "_sign": "SIGN", "qs": "QS"})),
        ("POST", zepp.URL_LOGIN_AUTH, auth_response),
    ]


def _otp(cookies: dict[str, str] | None = None) -> NeedsOtp:
    return NeedsOtp(DEVICE, "SID1", "CTX123", cookies if cookies is not None else {"deviceId": DEVICE})


# --- 1. parsowanie ----------------------------------------------------------------------------------


def test_parse_strips_start_prefix() -> None:
    assert zepp._parse('&&&START&&&{"a": 1}') == {"a": 1}
    assert zepp._parse('{"a": 1}') == {"a": 1}


@pytest.mark.parametrize("text", ["", "&&&START&&&", "<html>", "&&&START&&&nie json", "[1, 2]"])
def test_parse_invalid_raises(text: str) -> None:
    with pytest.raises(ZeppError, match="nieoczekiwana odpowiedź"):
        zepp._parse(text)


# --- 2. start_login ---------------------------------------------------------------------------------


def test_start_login_trusted_device_returns_linked() -> None:
    http = Fake(
        (*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}}, cookies={"passToken": "PT1"})),
        (*ZEPP_LOGIN, _zepp_ok()),
    )
    result = zepp.start_login(EMAIL, "pw", DEVICE, http=http)
    assert isinstance(result, Linked)
    assert result.token == TOKEN
    assert result.cookies["deviceId"] == DEVICE
    assert result.cookies["passToken"] == "PT1"
    form = http.calls[1]["data"]
    assert form["code"] == "CODE1"
    assert form["grant_type"] == "request_token"
    assert form["third_name"] == "xiaomi-hm-mifit"


def test_start_login_security_status_16_returns_needs_otp() -> None:
    steps = _login_steps(
        R({"securityStatus": 16, "notificationUrl": NOTIFY_URL}, cookies={"serviceToken": "ST1"})
    )
    steps += [
        ("GET", NOTIFY_URL.split("?")[0], R("<html>", cookies={"identity_session": "IS1"})),
        ("GET", zepp.URL_IDENTITY_LIST, R({"code": 0})),
    ]
    http = Fake(*steps)
    result = zepp.start_login(EMAIL, "pw", DEVICE, http=http)
    assert isinstance(result, NeedsOtp)
    assert (result.device_id, result.sid, result.context) == (DEVICE, "SID1", "CTX123")
    assert result.cookies["identity_session"] == "IS1"
    assert result.cookies["serviceToken"] == "ST1"
    assert http.calls[-1]["params"]["context"] == "CTX123"


def test_start_login_wrong_password_raises() -> None:
    http = Fake(*_login_steps(R({"code": 70016, "desc": "bad"})))
    with pytest.raises(ZeppError, match="logowanie odrzucone"):
        zepp.start_login(EMAIL, "pw", DEVICE, http=http)


def test_start_login_hash_is_uppercase_md5() -> None:
    http = Fake(*_login_steps(R({"code": 70016})))
    with pytest.raises(ZeppError):
        zepp.start_login(EMAIL, "pw-test", DEVICE, http=http)
    form = http.calls[2]["data"]
    assert form["hash"] == hashlib.md5(b"pw-test").hexdigest().upper()
    assert form["hash"].isupper()
    assert form["user"] == EMAIL
    assert (form["sid"], form["callback"], form["_sign"], form["qs"]) == ("SID1", "CB", "SIGN", "QS")


def test_start_login_sends_device_id_cookie_to_every_xiaomi_request() -> None:
    steps = _login_steps(R({"notificationUrl": NOTIFY_URL}))
    steps += [("GET", NOTIFY_URL.split("?")[0], R("x")), ("GET", zepp.URL_IDENTITY_LIST, R({}))]
    http = Fake(*steps)
    zepp.start_login(EMAIL, "pw", DEVICE, http=http)
    assert len(http.calls) == 5
    for call in http.calls:
        assert call["cookies"]["deviceId"] == DEVICE


def test_start_login_location_followed_to_code() -> None:
    steps = _login_steps(R({"location": LOC_URL}, cookies={"userId": "U1"}))
    steps += [
        ("GET", "https://account.xiaomi.com/sts", R(status=302, headers={"Location": CODE_URL})),
        (*ZEPP_LOGIN, _zepp_ok()),
    ]
    http = Fake(*steps)
    result = zepp.start_login(EMAIL, "pw", DEVICE, http=http)
    assert isinstance(result, Linked)
    assert result.cookies["userId"] == "U1"
    assert http.calls[-1]["data"]["code"] == "CODE1"


def test_start_login_location_without_code_raises() -> None:
    steps = _login_steps(R({"location": LOC_URL}))
    steps += [("GET", "https://account.xiaomi.com/sts", R("ok"))]
    with pytest.raises(ZeppError, match="brak kodu"):
        zepp.start_login(EMAIL, "pw", DEVICE, http=Fake(*steps))


def test_start_login_missing_login_url_raises() -> None:
    http = Fake((*AUTHORIZE, R({"data": {}})))
    with pytest.raises(ZeppError, match="nieoczekiwana odpowiedź"):
        zepp.start_login(EMAIL, "pw", DEVICE, http=http)


def test_start_login_redirect_url_without_code_raises() -> None:
    http = Fake((*AUTHORIZE, R({"data": {"redirectUrl": "https://api-mifit-cn.huami.com/x"}})))
    with pytest.raises(ZeppError, match="brak kodu"):
        zepp.start_login(EMAIL, "pw", DEVICE, http=http)


def test_start_login_zepp_without_token_raises() -> None:
    http = Fake(
        (*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}})),
        (*ZEPP_LOGIN, R({"result": "fail"}, prefix=False)),
    )
    with pytest.raises(ZeppError, match="nie zwrócił tokena"):
        zepp.start_login(EMAIL, "pw", DEVICE, http=http)


# --- 3. send_ticket / verify ------------------------------------------------------------------------


def test_send_ticket_merges_cookies() -> None:
    http = Fake(("POST", zepp.URL_SEND_EMAIL, R({"code": 0}, cookies={"ticket_ctx": "T1"})))
    state = zepp.send_ticket(_otp({"deviceId": DEVICE, "identity_session": "IS1"}), http=http)
    assert state.cookies == {"deviceId": DEVICE, "identity_session": "IS1", "ticket_ctx": "T1"}
    assert (state.sid, state.context, state.device_id) == ("SID1", "CTX123", DEVICE)
    call = http.calls[0]
    assert call["data"]["retry"] == 0
    assert (call["data"]["sid"], call["data"]["context"]) == ("SID1", "CTX123")
    assert call["cookies"]["identity_session"] == "IS1"


def test_send_ticket_error_code_raises() -> None:
    http = Fake(("POST", zepp.URL_SEND_EMAIL, R({"code": 70022})))
    with pytest.raises(ZeppError, match="nie wysłało"):
        zepp.send_ticket(_otp(), http=http)


def test_verify_visits_location_and_returns_trust_cookies() -> None:
    http = Fake(
        ("POST", zepp.URL_VERIFY_EMAIL, R({"code": 0, "location": LOC_URL}, cookies={"a": "1"})),
        ("GET", "https://account.xiaomi.com/sts", R("ok", cookies={"passToken": "PT1", "userId": "U1"})),
    )
    cookies = zepp.verify(_otp(), "123456", http=http)
    assert http.calls[1]["method"] == "GET"
    assert http.calls[1]["url"] == LOC_URL
    assert cookies == {"deviceId": DEVICE, "a": "1", "passToken": "PT1", "userId": "U1"}
    post = http.calls[0]
    assert post["params"]["_flag"] == 8
    assert post["data"]["ticket"] == "123456"
    assert post["data"]["trust"] == "true"


def test_verify_uses_location_header_when_body_empty() -> None:
    http = Fake(
        ("POST", zepp.URL_VERIFY_EMAIL, R(status=302, headers={"Location": LOC_URL})),
        ("GET", "https://account.xiaomi.com/sts", R("ok", cookies={"passToken": "PT1"})),
    )
    assert zepp.verify(_otp(), "1", http=http)["passToken"] == "PT1"


def test_verify_follows_redirect_chain() -> None:
    http = Fake(
        ("POST", zepp.URL_VERIFY_EMAIL, R({"location": LOC_URL})),
        (
            "GET",
            "https://account.xiaomi.com/sts",
            R(status=302, headers={"Location": "/next"}, cookies={"a": "1"}),
        ),
        ("GET", "https://account.xiaomi.com/next", R("ok", cookies={"b": "2"})),
    )
    cookies = zepp.verify(_otp(), "1", http=http)
    assert cookies["a"] == "1" and cookies["b"] == "2"


@pytest.mark.parametrize(
    "body",
    [
        {"result": "0106"},
        {"code": "0106"},
        {"error_code": 106},
        {"code": 106, "desc": "wrong"},
    ],
)
def test_verify_wrong_code(body: dict[str, Any]) -> None:
    http = Fake(("POST", zepp.URL_VERIFY_EMAIL, R(body)))
    with pytest.raises(WrongCode):
        zepp.verify(_otp(), "000000", http=http)


def test_verify_without_location_raises() -> None:
    http = Fake(("POST", zepp.URL_VERIFY_EMAIL, R({"code": 70016})))
    with pytest.raises(ZeppError, match="weryfikacja odrzucona") as info:
        zepp.verify(_otp(), "1", http=http)
    assert not isinstance(info.value, WrongCode)


# --- 4. refresh -------------------------------------------------------------------------------------


def test_refresh_returns_linked_and_uses_cookies() -> None:
    http = Fake((*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}})), (*ZEPP_LOGIN, _zepp_ok()))
    result = zepp.refresh({"passToken": "PT1"}, DEVICE, http=http)
    assert result.token == TOKEN
    assert http.calls[0]["cookies"] == {"passToken": "PT1", "deviceId": DEVICE}
    assert http.calls[1]["cookies"] is None  # do Zepp nie idą ciasteczka Xiaomi
    assert result.cookies["passToken"] == "PT1"


def test_refresh_ignores_numeric_data_code() -> None:
    http = Fake((*AUTHORIZE, R({"data": {"code": 70016, "redirectUrl": ""}})))
    with pytest.raises(ReconnectNeeded):
        zepp.refresh({}, DEVICE, http=http)


def test_refresh_without_redirect_url_needs_reconnect() -> None:
    http = Fake((*AUTHORIZE, R({"code": 0, "data": {"oauthLoginUrl": LOGIN_URL}})))
    with pytest.raises(ReconnectNeeded):
        zepp.refresh({}, DEVICE, http=http)


def test_refresh_without_app_token_raises_zepp_error() -> None:
    http = Fake(
        (*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}})),
        (*ZEPP_LOGIN, R({"token_info": {}}, prefix=False)),
    )
    with pytest.raises(ZeppError) as info:
        zepp.refresh({}, DEVICE, http=http)
    assert not isinstance(info.value, ReconnectNeeded)


def test_refresh_uses_fresh_zepp_device_id() -> None:
    ids = []
    for _ in range(2):
        http = Fake((*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}})), (*ZEPP_LOGIN, _zepp_ok()))
        zepp.refresh({}, DEVICE, http=http)
        ids.append(http.calls[1]["data"]["device_id"])
    assert ids[0] != ids[1]
    assert DEVICE not in ids


def test_new_device_id_is_16_hex() -> None:
    a, b = zepp.new_device_id(), zepp.new_device_id()
    assert len(a) == 16 and int(a, 16) >= 0
    assert a != b


# --- 5. members -------------------------------------------------------------------------------------


def test_members_parses_list() -> None:
    body = {
        "data": {"list": [{"fuid": -1, "nickname": "Ja", "height": 180}, {"fuid": "7", "nickname": None}]}
    }
    http = Fake(("POST", zepp.URL_MEMBERS, R(body, prefix=False)))
    assert zepp.members(TOKEN, http=http) == [Member(-1, "Ja", 180.0), Member(7, "", None)]
    call = http.calls[0]
    assert call["headers"]["apptoken"] == "FAKE-APP-TOKEN"
    assert call["data"] == {"fuid": "all", "userid": "1000001"}


def test_members_empty() -> None:
    http = Fake(("POST", zepp.URL_MEMBERS, R({"data": {}}, prefix=False)))
    assert zepp.members(TOKEN, http=http) == []


def test_members_401_needs_reconnect() -> None:
    http = Fake(("POST", zepp.URL_MEMBERS, R(status=401)))
    with pytest.raises(ReconnectNeeded):
        zepp.members(TOKEN, http=http)


def test_members_500_raises() -> None:
    http = Fake(("POST", zepp.URL_MEMBERS, R(status=500)))
    with pytest.raises(ZeppError, match="members: http 500"):
        zepp.members(TOKEN, http=http)


def test_members_malformed_row_raises() -> None:
    http = Fake(("POST", zepp.URL_MEMBERS, R({"data": {"list": [{"nickname": "x"}]}}, prefix=False)))
    with pytest.raises(ZeppError, match="nieoczekiwana odpowiedź"):
        zepp.members(TOKEN, http=http)


# --- 6. weight_records ------------------------------------------------------------------------------

WEIGHTS_URL = "https://api-mifit.zepp.com/users/1000001/members/-1/weightRecords"


def _item(ts: int, weight: Any = 70.5, *, weight_type: int = 0, **summary: Any) -> dict[str, Any]:
    return {
        "generatedTime": ts,
        "weightType": weight_type,
        "deviceId": "DEV1",
        "summary": {"weight": weight, **summary},
    }


class Pages:
    """Fałszywy ``http`` serwujący strony weightRecords wg ``toTime``."""

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.to_times: list[int] = []

    def __call__(self, method: str, url: str, *, params: Any = None, **_: Any) -> HttpResult:
        assert (method, url) == ("GET", WEIGHTS_URL)
        assert params["limit"] == 200
        self.to_times.append(params["toTime"])
        return R(self.pages.get(params["toTime"], {"items": []}), prefix=False)


def test_weight_records_paginates_three_pages_sorted() -> None:
    http = Pages(
        {
            1000: {"items": [_item(900), _item(800)], "next": 700},
            700: {"items": [_item(650)], "next": 500},
            500: {"items": [_item(400)], "next": 0},
        }
    )
    records = zepp.weight_records(TOKEN, -1, now_ts=1000, http=http)
    assert [r.ts for r in records] == [400, 650, 800, 900]
    assert http.to_times == [1000, 700, 500]


def test_weight_records_filters_non_scale_weight_type() -> None:
    http = Pages({100: {"items": [_item(90), _item(80, weight_type=1), _item(70, weight_type=2)]}})
    assert [r.ts for r in zepp.weight_records(TOKEN, -1, now_ts=100, http=http)] == [90]


def test_weight_records_empty() -> None:
    assert zepp.weight_records(TOKEN, -1, now_ts=100, http=Pages({})) == []


def test_weight_records_since_ts_cuts_older_and_stops() -> None:
    http = Pages(
        {
            1000: {"items": [_item(900), _item(600)], "next": 500},
            500: {"items": [_item(400)], "next": 100},
        }
    )
    records = zepp.weight_records(TOKEN, -1, since_ts=600, now_ts=1000, http=http)
    assert [r.ts for r in records] == [900]  # ts == since_ts odrzucony
    assert http.to_times == [1000]  # next (500) <= since_ts → bez kolejnej strony


def test_weight_records_no_progress_stops() -> None:
    http = Pages({1000: {"items": [_item(900)], "next": 1000}})
    assert len(zepp.weight_records(TOKEN, -1, now_ts=1000, http=http)) == 1
    assert http.to_times == [1000]


def test_weight_records_next_greater_than_ts_stops() -> None:
    http = Pages({1000: {"items": [_item(900)], "next": 5000}})
    zepp.weight_records(TOKEN, -1, now_ts=1000, http=http)
    assert http.to_times == [1000]


def test_weight_records_deduplicates_ts() -> None:
    http = Pages(
        {
            1000: {"items": [_item(900, 70.0), _item(900, 99.0)], "next": 800},
            800: {"items": [_item(900, 1.0)]},
        }
    )
    records = zepp.weight_records(TOKEN, -1, now_ts=1000, http=http)
    assert [(r.ts, r.weight_kg) for r in records] == [(900, 70.0)]


def test_weight_records_maps_fields_and_missing_to_none() -> None:
    full = _item(
        900,
        "70.5",
        fatRate=20.1,
        bodyWaterRate="55",
        boneMass=3.1,
        muscleRate=40,
        visceralFat=8,
        metabolism=1600,
        bmi=22.5,
        impedance=480,
        bodyScore=77,
        muscleAge=35,
    )
    sparse = {"generatedTime": 800, "weightType": 0, "summary": {"weight": "", "fatRate": None}}
    http = Pages({1000: {"items": [full, sparse]}})
    sparse_rec, full_rec = zepp.weight_records(TOKEN, -1, now_ts=1000, http=http)
    assert full_rec == BodyRecord(
        ts=900,
        device_id="DEV1",
        weight_kg=70.5,
        fat_pct=20.1,
        water_pct=55.0,
        bone_kg=3.1,
        muscle_rate=40.0,
        visceral=8.0,
        bmr_kcal=1600.0,
        bmi=22.5,
        impedance=480.0,
        body_score=77.0,
        metabolic_age=35.0,
        raw=full["summary"],
    )
    assert sparse_rec.device_id is None
    assert sparse_rec.weight_kg is None
    assert sparse_rec.fat_pct is None and sparse_rec.bmi is None and sparse_rec.metabolic_age is None
    assert sparse_rec.raw == {"weight": "", "fatRate": None}


def test_weight_records_safety_limit_200_pages() -> None:
    class Endless:
        calls = 0

        def __call__(self, method: str, url: str, *, params: Any = None, **_: Any) -> HttpResult:
            self.calls += 1
            ts = params["toTime"]
            return R({"items": [_item(ts - 1)], "next": ts - 10}, prefix=False)

    http = Endless()
    records = zepp.weight_records(TOKEN, -1, now_ts=10_000, http=http)
    assert http.calls == 200
    assert len(records) == 200


def test_weight_records_401_needs_reconnect_and_500_raises() -> None:
    with pytest.raises(ReconnectNeeded):
        zepp.weight_records(TOKEN, -1, now_ts=10, http=Fake(("GET", WEIGHTS_URL, R(status=401))))
    with pytest.raises(ZeppError, match="weightRecords: http 500"):
        zepp.weight_records(TOKEN, -1, now_ts=10, http=Fake(("GET", WEIGHTS_URL, R(status=500))))


def test_weight_records_sends_apptoken_and_fuid_in_path() -> None:
    http = Fake(
        (
            "GET",
            "https://api-mifit.zepp.com/users/1000001/members/42/weightRecords",
            R({"items": []}, prefix=False),
        )
    )
    zepp.weight_records(TOKEN, 42, now_ts=77, http=http)
    assert http.calls[0]["headers"]["apptoken"] == "FAKE-APP-TOKEN"
    assert http.calls[0]["params"] == {"limit": 200, "toTime": 77}


# --- 7. SSRF ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location",
    [
        "http://evil.example/",
        "https://evil.example/",
        "https://account.xiaomi.com.evil.example/",
        "https://evilxiaomi.com/",
        "http://account.xiaomi.com/",
        "https://account.xiaomi.com:8443/",
        "ftp://account.xiaomi.com/",
    ],
)
def test_ssrf_redirect_location_rejected(location: str) -> None:
    http = Fake(
        ("POST", zepp.URL_VERIFY_EMAIL, R({"location": LOC_URL})),
        ("GET", "https://account.xiaomi.com/sts", R(status=302, headers={"Location": location})),
    )
    with pytest.raises(ZeppError, match="^niedozwolony adres$") as info:
        zepp.verify(_otp(), "1", http=http)
    assert "evil" not in str(info.value)
    assert len(http.calls) == 2


def test_ssrf_initial_location_and_login_url_rejected() -> None:
    http = Fake(("POST", zepp.URL_VERIFY_EMAIL, R({"location": "https://evil.example/"})))
    with pytest.raises(ZeppError, match="niedozwolony adres"):
        zepp.verify(_otp(), "1", http=http)
    http = Fake((*AUTHORIZE, R({"data": {"oauthLoginUrl": "https://evil.example/login"}})))
    with pytest.raises(ZeppError, match="niedozwolony adres"):
        zepp.start_login(EMAIL, "pw", DEVICE, http=http)
    assert len(http.calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://xiaomi.com/",
        "https://account.xiaomi.com/x",
        "https://zepp.com/",
        "https://api-mifit.zepp.com/x",
        "https://account.zepp.com:443/x",
    ],
)
def test_allowed_hosts(url: str) -> None:
    assert zepp._host_allowed(url)


def test_too_many_redirects_raises() -> None:
    steps: list[tuple[str, str, HttpResult]] = [("POST", zepp.URL_VERIFY_EMAIL, R({"location": LOC_URL}))]
    steps += [("GET", "https://account.xiaomi.com/sts", R(status=302, headers={"Location": LOC_URL}))] * 6
    with pytest.raises(ZeppError, match="zbyt wiele przekierowań"):
        zepp.verify(_otp(), "1", http=Fake(*steps))


# --- 8. brak wycieku sekretów -----------------------------------------------------------------------

Path_ = Callable[[], None]


def _leak_paths() -> dict[str, Path_]:
    secret_cookies = {"passToken": COOKIE}
    evil = f"https://evil.example/?t={TOKEN_SECRET}&c={COOKIE}"
    secret_token = ZeppToken("1000001", TOKEN_SECRET)
    return {
        "login-bad-json": lambda: zepp.start_login(
            EMAIL, PW, DEVICE, http=Fake((*AUTHORIZE, R(f"<html>{PW}</html>")))
        ),
        "login-rejected": lambda: zepp.start_login(
            EMAIL, PW, DEVICE, http=Fake(*_login_steps(R({"code": 1}, cookies=secret_cookies)))
        ),
        "login-no-code": lambda: zepp.start_login(
            EMAIL,
            PW,
            DEVICE,
            http=Fake(
                *_login_steps(R({"location": LOC_URL})),
                ("GET", "https://account.xiaomi.com/sts", R("x")),
            ),
        ),
        "login-zepp-no-token": lambda: zepp.start_login(
            EMAIL,
            PW,
            DEVICE,
            http=Fake(
                (*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}}, cookies=secret_cookies)),
                (*ZEPP_LOGIN, R({"token_info": {}, "x": TOKEN_SECRET}, prefix=False)),
            ),
        ),
        "send-error": lambda: zepp.send_ticket(
            _otp(secret_cookies), http=Fake(("POST", zepp.URL_SEND_EMAIL, R({"code": 5, "x": TICKET})))
        ),
        "verify-wrong-code": lambda: zepp.verify(
            _otp(secret_cookies), TICKET, http=Fake(("POST", zepp.URL_VERIFY_EMAIL, R({"result": "0106"})))
        ),
        "verify-rejected": lambda: zepp.verify(
            _otp(secret_cookies), TICKET, http=Fake(("POST", zepp.URL_VERIFY_EMAIL, R({"code": 9})))
        ),
        "verify-ssrf": lambda: zepp.verify(
            _otp(secret_cookies),
            TICKET,
            http=Fake(("POST", zepp.URL_VERIFY_EMAIL, R({"location": evil}, cookies=secret_cookies))),
        ),
        "refresh-reconnect": lambda: zepp.refresh(
            secret_cookies, DEVICE, http=Fake((*AUTHORIZE, R({"data": {}}, cookies=secret_cookies)))
        ),
        "refresh-no-token": lambda: zepp.refresh(
            secret_cookies,
            DEVICE,
            http=Fake(
                (*AUTHORIZE, R({"data": {"redirectUrl": CODE_URL}})),
                (*ZEPP_LOGIN, R({"token_info": {"user_id": "1"}}, prefix=False)),
            ),
        ),
        "members-401": lambda: zepp.members(
            secret_token, http=Fake(("POST", zepp.URL_MEMBERS, R(status=401)))
        ),
        "members-500": lambda: zepp.members(
            secret_token, http=Fake(("POST", zepp.URL_MEMBERS, R(status=500)))
        ),
        "members-malformed": lambda: zepp.members(
            secret_token,
            http=Fake(("POST", zepp.URL_MEMBERS, R({"data": {"list": [{"x": TOKEN_SECRET}]}}, prefix=False))),
        ),
        "weights-401": lambda: zepp.weight_records(
            secret_token, -1, now_ts=10, http=Fake(("GET", WEIGHTS_URL, R(status=401)))
        ),
        "weights-500": lambda: zepp.weight_records(
            secret_token, -1, now_ts=10, http=Fake(("GET", WEIGHTS_URL, R(status=500)))
        ),
    }


@pytest.mark.parametrize("name", sorted(_leak_paths()))
def test_error_paths_do_not_leak_secrets(name: str) -> None:
    with pytest.raises(ZeppError) as info:
        _leak_paths()[name]()
    exc = info.value
    assert exc.__cause__ is None
    for text in (str(exc), repr(exc)):
        for secret in SECRETS:
            assert secret not in text
        assert "http" not in text.replace("members: http", "").replace("weightRecords: http", "")


def test_state_and_token_repr_hide_secrets() -> None:
    token = ZeppToken("1000001", TOKEN_SECRET)
    state = NeedsOtp(DEVICE, "SID1", "CTX123", {"passToken": COOKIE})
    linked = Linked(token, {"passToken": COOKIE})
    for obj in (token, state, linked):
        for secret in SECRETS:
            assert secret not in repr(obj)
    assert "1000001" in repr(token)


# --- 9. default_http --------------------------------------------------------------------------------


class _Resp:
    def __init__(self, status: int, headers: Message, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self._body = body
        self.closed = False

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        self.closed = True


class _Opener:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list[urllib.request.Request] = []
        self.timeout: float | None = None

    def open(self, req: urllib.request.Request, timeout: float | None = None) -> Any:
        self.requests.append(req)
        self.timeout = timeout
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _headers(*pairs: tuple[str, str]) -> Message:
    msg = Message()
    for key, value in pairs:
        msg.add_header(key, value)
    return msg


def test_default_http_success_parses_headers_and_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _headers(
        ("Content-Type", "text/plain"),
        ("Set-Cookie", "a=1; Path=/; HttpOnly"),
        ("Set-Cookie", "b=two=2; Secure"),
    )
    resp = _Resp(200, headers, "zażółć".encode())
    opener = _Opener(resp)
    monkeypatch.setattr(zepp, "_OPENER", opener)
    result = zepp.default_http(
        "POST",
        "https://account.xiaomi.com/x",
        params={"p": "1"},
        data={"k": "v w"},
        headers={"User-Agent": "UA"},
        cookies={"deviceId": "D", "c": "3"},
    )
    assert result == HttpResult(
        200,
        {"content-type": "text/plain", "set-cookie": "b=two=2; Secure"},
        "zażółć",
        {"a": "1", "b": "two=2"},
    )
    assert "content-type" in result.headers
    assert resp.closed
    req = opener.requests[0]
    assert req.full_url == "https://account.xiaomi.com/x?p=1"
    assert req.data == b"k=v+w"
    assert req.get_method() == "POST"
    assert req.get_header("Cookie") == "deviceId=D; c=3"
    assert req.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert opener.timeout == 20


def test_default_http_get_has_no_body(monkeypatch: pytest.MonkeyPatch) -> None:
    opener = _Opener(_Resp(200, _headers(), b"{}"))
    monkeypatch.setattr(zepp, "_OPENER", opener)
    zepp.default_http("GET", "https://api-mifit.zepp.com/x?a=1", params={"b": 2})
    req = opener.requests[0]
    assert req.data is None
    assert req.full_url == "https://api-mifit.zepp.com/x?a=1&b=2"
    assert not req.has_header("Cookie")


def test_default_http_does_not_follow_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    headers = _headers(("Location", "https://account.xiaomi.com/next"), ("Set-Cookie", "t=1; Path=/"))
    err = urllib.error.HTTPError("https://account.xiaomi.com/x", 302, "Found", headers, io.BytesIO(b"moved"))
    monkeypatch.setattr(zepp, "_OPENER", _Opener(err))
    result = zepp.default_http("GET", "https://account.xiaomi.com/x")
    assert result.status == 302
    assert result.headers["location"] == "https://account.xiaomi.com/next"
    assert result.set_cookies == {"t": "1"}
    assert result.body == "moved"


def test_opener_has_no_redirect_handler() -> None:
    assert any(isinstance(h, zepp._NoRedirect) for h in zepp._OPENER.handlers)
    req = urllib.request.Request("https://account.xiaomi.com/x")
    handler = zepp._NoRedirect()
    assert (
        handler.redirect_request(req, None, 302, "Found", Message(), "https://account.xiaomi.com/y") is None
    )


@pytest.mark.parametrize("status", [400, 401, 403, 500, 503])
def test_default_http_returns_http_errors_as_result(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    err = urllib.error.HTTPError(
        "https://api-mifit.zepp.com/x", status, "err", _headers(), io.BytesIO(b"boom")
    )
    monkeypatch.setattr(zepp, "_OPENER", _Opener(err))
    result = zepp.default_http("GET", "https://api-mifit.zepp.com/x")
    assert (result.status, result.body) == (status, "boom")


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("dns fail https://secret.example/"),
        TimeoutError("timed out"),
        ConnectionResetError(),
    ],
)
def test_default_http_network_error(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    monkeypatch.setattr(zepp, "_OPENER", _Opener(exc))
    with pytest.raises(ZeppError, match="^błąd sieci$") as info:
        zepp.default_http("GET", "https://api-mifit.zepp.com/x")
    assert info.value.__cause__ is None
    assert "secret" not in repr(info.value)
