"""Trasy i sekcja „Konto Zepp”. Sieć fałszywa (podmiana funkcji ``dieta.zepp``, brak prawdziwego ``http``)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_zepp_service import EMAIL, NOW, PW, FakeZepp

from dieta.settings import Settings
from dieta.store import Store
from dieta.web.app import create_app
from dieta.zepp import Linked, Member, ReconnectNeeded, WrongCode, ZeppToken

SECRETS = ("TOK-0", "TOK-1", "COOKIE-SECRET", PW)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeZepp:
    return FakeZepp(monkeypatch)


def _client(tmp_path: Path, *, enabled: bool = True) -> TestClient:
    s = Settings(
        data_dir=tmp_path, log_level="info", dev=True, person_1_name="Ala", person_2_name="Ola",
        zepp_enabled=enabled,
    )  # fmt: skip
    app = create_app(s, zepp_http=lambda *a, **k: pytest.fail("sieć"), zepp_now=lambda: NOW)
    return TestClient(app, follow_redirects=False)


def _loc(r: object) -> str:
    return r.headers["location"]  # type: ignore[attr-defined,no-any-return]


def _start(c: TestClient, pid: int = 1):  # type: ignore[no-untyped-def]
    return c.post(f"/zepp/{pid}/start", data={"email": EMAIL, "password": PW})


def test_start_otp_redirects_with_code_and_no_secrets(tmp_path: Path, fake: FakeZepp) -> None:
    c = _client(tmp_path)
    r = _start(c)
    assert r.status_code == 303
    assert _loc(r) == "../../?zepp=otp&p=1"
    assert PW not in _loc(r) and EMAIL not in _loc(r) and PW not in r.text


def test_full_flow_otp_send_verify(tmp_path: Path, fake: FakeZepp) -> None:
    c = _client(tmp_path)
    _start(c)
    assert _loc(c.post("/zepp/1/send")) == "../../?zepp=sent&p=1"
    r = c.post("/zepp/1/verify", data={"code": "123456"})
    assert _loc(r) == "../../?zepp=ok&n=2&p=1"
    html = c.get(_loc(r).replace("../../", "/")).text
    assert "Połączono" in html and "Zaimportowano nowych pomiarów: 2" in html


def test_wrong_code_and_expired(tmp_path: Path, fake: FakeZepp) -> None:
    c = _client(tmp_path)
    assert _loc(c.post("/zepp/1/send")) == "../../?zepp=expired&p=1"
    assert _loc(c.post("/zepp/1/verify", data={"code": "1"})) == "../../?zepp=expired&p=1"
    _start(c)
    fake.verify_exc = WrongCode("zły kod weryfikacyjny")
    assert _loc(c.post("/zepp/1/verify", data={"code": "1"})) == "../../?zepp=wrong_code&p=1"
    assert 'role="alert"' in c.get("/?zepp=wrong_code&p=1").text


def test_login_failed_does_not_echo_password(tmp_path: Path, fake: FakeZepp) -> None:
    from dieta.zepp import ZeppError

    c = _client(tmp_path)
    fake.start_result = ZeppError("logowanie odrzucone")
    r = _start(c)
    assert _loc(r) == "../../?zepp=login_failed&p=1"
    html = c.get(_loc(r).replace("../../", "/")).text
    assert PW not in html and EMAIL not in html and 'role="alert"' in html
    assert _loc(c.post("/zepp/1/start", data={})) == "../../?zepp=login_failed&p=1"


def test_member_sync_disconnect_routes(tmp_path: Path, fake: FakeZepp) -> None:
    fake.member_list = [Member(7, "Ala", 170.0), Member(8, "Ola", 165.0)]
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    c = _client(tmp_path)
    assert _loc(_start(c)) == "../../?zepp=ok&p=1"
    html = c.get("/").text
    assert 'name="fuid"' in html and 'value="8"' in html and "Ola" in html
    assert _loc(c.post("/zepp/1/member", data={"fuid": "99"})) == "../../?zepp=error&p=1"
    assert _loc(c.post("/zepp/1/member", data={"fuid": "abc"})) == "../../?zepp=error&p=1"
    assert _loc(c.post("/zepp/1/member", data={"fuid": "8"})) == "../../?zepp=ok&n=2&p=1"
    assert _loc(c.post("/zepp/1/sync")) == "../../?zepp=ok&n=0&p=1"
    assert _loc(c.post("/zepp/1/disconnect")) == "../../?zepp=disconnected&p=1"


def test_sync_errors(tmp_path: Path, fake: FakeZepp) -> None:
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    c = _client(tmp_path)
    _start(c)
    fake.record_excs = [ReconnectNeeded("a")]
    fake.refresh_excs = [ReconnectNeeded("sesja Xiaomi wygasła")]
    assert _loc(c.post("/zepp/1/sync")) == "../../?zepp=expired&p=1"
    html = c.get("/").text
    assert "Połącz ponownie" in html and 'name="password"' in html


@pytest.mark.parametrize("path", ["start", "send", "verify", "member", "sync", "disconnect"])
def test_unknown_person_is_404(tmp_path: Path, fake: FakeZepp, path: str) -> None:
    c = _client(tmp_path)
    assert (
        c.post(f"/zepp/3/{path}", data={"email": "a", "password": "b", "code": "1", "fuid": "1"}).status_code
        == 404
    )


@pytest.mark.parametrize("path", ["start", "send", "verify", "member", "sync", "disconnect"])
def test_disabled_flag_gives_404_and_hides_section(tmp_path: Path, fake: FakeZepp, path: str) -> None:
    c = _client(tmp_path, enabled=False)
    assert c.post(f"/zepp/1/{path}", data={"email": "a", "password": "b"}).status_code == 404
    assert "Konto Zepp" not in c.get("/").text


def test_section_states_render(tmp_path: Path, fake: FakeZepp) -> None:
    c = _client(tmp_path)
    html = c.get("/").text
    assert "Konto Zepp" in html and 'name="email"' in html and 'type="password"' in html
    assert 'autocomplete="off"' in html
    _start(c)
    html = c.get("/").text
    assert "Wyślij kod" in html and 'name="code"' in html
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    c.post("/zepp/2/start", data={"email": EMAIL, "password": PW})
    html = c.get("/").text
    assert (
        "Synchronizuj teraz" in html
        and "Rozłącz" in html
        and 'Pomiarów: <span class="dz-num">2</span>' in html
    )


def test_pending_lost_after_restart_shows_login_form(tmp_path: Path, fake: FakeZepp) -> None:
    c = _client(tmp_path)
    _start(c)
    c2 = _client(tmp_path)  # nowy proces: pending tylko w pamięci
    html = c2.get("/").text
    assert "Wyślij kod" not in html and 'name="password"' in html


def test_ok_banner_shows_count(tmp_path: Path, fake: FakeZepp) -> None:
    html = _client(tmp_path).get("/?zepp=ok&n=284&p=1").text
    assert "284" in html and 'role="status"' in html


def test_page_never_contains_tokens_cookies_password(tmp_path: Path, fake: FakeZepp) -> None:
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    c = _client(tmp_path)
    r = _start(c)
    html = c.get("/?zepp=ok&n=2&p=1").text
    for secret in SECRETS:
        assert secret not in html and secret not in _loc(r)
    assert Store(tmp_path / "dieta.db").zepp_account(1) is not None


def test_unknown_banner_code_ignored(tmp_path: Path, fake: FakeZepp) -> None:
    html = _client(tmp_path).get("/?zepp=<script>&p=1").text
    assert "<script>" not in html


def test_banner_ignores_non_ascii_digits_and_huge_numbers() -> None:
    from dieta.web.zepp import banner

    assert banner("ok", "²", "²") is not None
    assert banner("ok", "9" * 5000, "9" * 5000) is not None  # bez ValueError


def test_weight_form_collapsed_only_when_zepp_linked_with_weight(tmp_path: Path, fake: FakeZepp) -> None:
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    c = _client(tmp_path)
    _start(c)
    html = c.get("/").text
    assert '<details class="dz-details" data-weight-form="1">' in html
    assert '<details class="dz-details" data-weight-form="2" open>' in html
    c.post("/zepp/1/disconnect")
    assert '<details class="dz-details" data-weight-form="1" open>' in c.get("/").text
