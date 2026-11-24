"""Testy ``ZeppService``. Sieć jest fałszywa: funkcje modułu ``dieta.zepp`` podmieniamy na skrypty."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from dieta import zepp
from dieta.store import BodyRow, Store
from dieta.zepp import BodyRecord, Linked, Member, NeedsOtp, ReconnectNeeded, WrongCode, ZeppError, ZeppToken
from dieta.zepp_service import LOOKBACK_S, PENDING_TTL_S, ZeppService

PW = "PW-SECRET-123"
EMAIL = "ala@example.com"
NOW = 1_800_000_000
DAY = 86_400


def _rec(ts: int, kg: float = 70.0) -> BodyRecord:
    return BodyRecord(
        ts, "dev", kg, 20.0, 50.0, 3.0, 40.0, 5.0, 1500.0, 22.0, 500.0, 80.0, 30.0, {"weight": kg}
    )


class FakeZepp:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.otp = NeedsOtp("DEV1", "SID", "CTX", {"deviceId": "DEV1"})
        self.start_result: NeedsOtp | Linked | Exception = self.otp
        self.verify_exc: Exception | None = None
        self.refresh_excs: list[Exception | None] = []
        self.records: list[BodyRecord] = [_rec(NOW - 2 * DAY, 71.0), _rec(NOW - DAY, 70.5)]
        self.record_excs: list[Exception] = []
        self.member_list = [Member(7, "Ala", 170.0)]
        self.calls: list[tuple[str, Any]] = []
        self.tokens = 0
        monkeypatch.setattr(zepp, "new_device_id", lambda: "NEWDEV0123456789")
        monkeypatch.setattr(zepp, "start_login", self.start_login)
        monkeypatch.setattr(zepp, "send_ticket", self.send_ticket)
        monkeypatch.setattr(zepp, "verify", self.verify)
        monkeypatch.setattr(zepp, "refresh", self.refresh)
        monkeypatch.setattr(zepp, "members", self.members)
        monkeypatch.setattr(zepp, "weight_records", self.weight_records)

    def _linked(self) -> Linked:
        self.tokens += 1
        return Linked(ZeppToken("1000001", f"TOK-{self.tokens}"), {"passToken": "COOKIE-SECRET"})

    def start_login(self, email: str, password: str, device_id: str, *, http: Any) -> NeedsOtp | Linked:
        self.calls.append(("start", device_id))
        if isinstance(self.start_result, Exception):
            raise self.start_result
        if isinstance(self.start_result, NeedsOtp):
            return NeedsOtp(device_id, "SID", "CTX", {"deviceId": device_id})
        return self.start_result

    def send_ticket(self, state: NeedsOtp, *, http: Any) -> NeedsOtp:
        self.calls.append(("send", state.sid))
        return state

    def verify(self, state: NeedsOtp, code: str, *, http: Any) -> dict[str, str]:
        self.calls.append(("verify", code))
        if self.verify_exc:
            raise self.verify_exc
        return {"passToken": "COOKIE-SECRET"}

    def refresh(self, cookies: dict[str, str], device_id: str, *, http: Any) -> Linked:
        self.calls.append(("refresh", device_id))
        exc = self.refresh_excs.pop(0) if self.refresh_excs else None
        if exc:
            raise exc
        return self._linked()

    def members(self, token: ZeppToken, *, http: Any) -> list[Member]:
        self.calls.append(("members", token.app_token))
        return list(self.member_list)

    def weight_records(
        self, token: ZeppToken, fuid: int, *, since_ts: int = 0, now_ts: int, http: Any
    ) -> list[BodyRecord]:
        self.calls.append(("records", (token.app_token, fuid, since_ts)))
        if self.record_excs:
            raise self.record_excs.pop(0)
        return [r for r in self.records if r.ts > since_ts]


class Clock:
    def __init__(self) -> None:
        self.t = NOW

    def __call__(self) -> int:
        return self.t


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeZepp:
    return FakeZepp(monkeypatch)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "dieta.db")


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def svc(store: Store, clock: Clock, fake: FakeZepp) -> ZeppService:
    return ZeppService(store, http=lambda *a, **k: pytest.fail("sieć"), now=clock)


def _link(svc: ZeppService, fake: FakeZepp) -> None:
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    assert svc.start(1, EMAIL, PW).code == "ok"


def test_start_needs_otp_saves_pending_and_no_password(
    svc: ZeppService, store: Store, tmp_path: Path
) -> None:
    out = svc.start(1, EMAIL, PW)
    assert out.code == "otp"
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "pending_otp" and acc.device_id == "NEWDEV0123456789"
    assert svc.view(1).pending_alive is True
    assert PW.encode() not in (tmp_path / "dieta.db").read_bytes()
    assert PW not in repr(acc) and PW not in repr(svc._pending) and EMAIL not in repr(svc._pending)


def test_start_failure_returns_login_failed(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    fake.start_result = ZeppError("logowanie odrzucone")
    assert svc.start(1, EMAIL, PW).code == "login_failed"
    assert store.zepp_account(1) is None
    assert svc.start(1, "", PW).code == "login_failed"


def test_device_id_is_stable_between_starts(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    svc.start(1, EMAIL, PW)
    svc.start(1, EMAIL, PW)
    assert [c[1] for c in fake.calls if c[0] == "start"] == ["NEWDEV0123456789"] * 2
    acc = store.zepp_account(1)
    assert acc is not None and acc.device_id == "NEWDEV0123456789"


def test_send_without_pending_is_expired(svc: ZeppService) -> None:
    assert svc.send(1).code == "expired"


def test_send_ok_marks_sent(svc: ZeppService, fake: FakeZepp) -> None:
    svc.start(1, EMAIL, PW)
    assert svc.send(1).code == "sent"
    assert svc.view(1).pending_sent is True
    assert ("send", "SID") in fake.calls


def test_send_error(svc: ZeppService, fake: FakeZepp, monkeypatch: pytest.MonkeyPatch) -> None:
    svc.start(1, EMAIL, PW)

    def boom(state: NeedsOtp, *, http: Any) -> NeedsOtp:
        raise ZeppError("Xiaomi nie wysłało kodu")

    monkeypatch.setattr(zepp, "send_ticket", boom)
    assert svc.send(1).code == "error"


def test_pending_expires_after_ttl(svc: ZeppService, clock: Clock) -> None:
    svc.start(1, EMAIL, PW)
    clock.t += PENDING_TTL_S + 1
    assert svc.view(1).pending_alive is False
    assert svc.send(1).code == "expired"
    assert svc.verify(1, "123456").code == "expired"


def test_verify_without_pending_is_expired(svc: ZeppService) -> None:
    assert svc.verify(1, "123456").code == "expired"


def test_verify_wrong_code_keeps_pending(svc: ZeppService, fake: FakeZepp) -> None:
    svc.start(1, EMAIL, PW)
    fake.verify_exc = WrongCode("zły kod weryfikacyjny")
    assert svc.verify(1, "000000").code == "wrong_code"
    assert svc.view(1).pending_alive is True
    fake.verify_exc = None
    assert svc.verify(1, "123456").code == "ok"


def test_verify_zepp_error(svc: ZeppService, fake: FakeZepp) -> None:
    svc.start(1, EMAIL, PW)
    fake.verify_exc = ZeppError("weryfikacja odrzucona")
    assert svc.verify(1, "1").code == "error"


def test_verify_ok_links_account_with_token_and_imports(svc: ZeppService, store: Store) -> None:
    svc.start(1, EMAIL, PW)
    out = svc.verify(1, "123456")
    assert (out.code, out.n) == ("ok", 2)
    acc = store.zepp_account(1)
    assert acc is not None
    assert (acc.status, acc.fuid, acc.user_id, acc.app_token) == ("linked", 7, "1000001", "TOK-1")
    assert json.loads(acc.cookies_json) == {"passToken": "COOKIE-SECRET"}
    assert acc.last_sync_ts == NOW and acc.last_error is None
    assert len(store.body_measurements(1)) == 2
    assert store.latest_weight(1) == 70.5
    assert svc.view(1).count == 2
    assert svc._pending == {}


def test_single_member_imports_all_history(svc: ZeppService, fake: FakeZepp) -> None:
    _link(svc, fake)
    assert ("records", ("TOK-0", 7, 0)) in fake.calls


def test_multiple_members_wait_for_choice(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    fake.member_list = [Member(7, "Ala", 170.0), Member(8, "Ola", 165.0)]
    _link(svc, fake)
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "linked" and acc.fuid is None
    assert store.body_measurements(1) == []
    view = svc.view(1)
    assert [m.fuid for m in view.members] == [7, 8]


def test_member_choice_validated_and_imports(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    fake.member_list = [Member(7, "Ala", 170.0), Member(8, "Ola", 165.0)]
    _link(svc, fake)
    assert svc.member(1, 99).code == "error"
    acc = store.zepp_account(1)
    assert acc is not None and acc.fuid is None
    out = svc.member(1, 8)
    assert (out.code, out.n) == ("ok", 2)
    acc = store.zepp_account(1)
    assert acc is not None and acc.fuid == 8
    assert ("records", ("TOK-0", 8, 0)) in fake.calls


def test_member_without_account_is_error(svc: ZeppService) -> None:
    assert svc.member(1, 7).code == "error"


def test_sync_is_incremental(svc: ZeppService, fake: FakeZepp, clock: Clock, store: Store) -> None:
    _link(svc, fake)
    fake.records.append(_rec(NOW + 10, 69.0))
    clock.t = NOW + 100
    out = svc.sync(1)
    assert (out.code, out.n) == ("ok", 1)
    assert fake.calls[-1] == ("records", ("TOK-0", 7, NOW - LOOKBACK_S))
    acc = store.zepp_account(1)
    assert acc is not None and acc.last_sync_ts == NOW + 100
    assert len(store.body_measurements(1)) == 3


def test_sync_401_refreshes_once_and_retries(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("token Zepp unieważniony")]
    out = svc.sync(1)
    assert out.code == "ok"
    assert [c[0] for c in fake.calls].count("refresh") == 1
    assert fake.calls[-1][1][0] == "TOK-1"
    acc = store.zepp_account(1)
    assert acc is not None and acc.app_token == "TOK-1" and acc.status == "linked"


def test_sync_refresh_needing_reconnect_sets_status(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("token Zepp unieważniony")]
    fake.refresh_excs = [ReconnectNeeded("sesja Xiaomi wygasła")]
    assert svc.sync(1).code == "expired"
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "reconnect" and acc.last_error == "sesja Xiaomi wygasła"
    assert svc.sync(1).code == "expired"  # bez tokenu nie próbujemy


def test_sync_second_401_after_refresh_is_reconnect(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("a"), ReconnectNeeded("b")]
    assert svc.sync(1).code == "expired"
    assert [c[0] for c in fake.calls].count("refresh") == 1
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "reconnect"


def test_sync_error_stores_only_message(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    fake.record_excs = [ZeppError("weightRecords: http 500")]
    assert svc.sync(1).code == "error"
    acc = store.zepp_account(1)
    assert acc is not None and acc.last_error == "weightRecords: http 500" and acc.status == "linked"


def test_sync_without_account_or_fuid(svc: ZeppService, fake: FakeZepp) -> None:
    assert svc.sync(1).code == "error"
    fake.member_list = [Member(7, "A", None), Member(8, "B", None)]
    _link(svc, fake)
    assert svc.sync(1).code == "error"


def test_disconnect_clears_tokens_and_purges_zepp_data(
    svc: ZeppService, fake: FakeZepp, store: Store
) -> None:
    _link(svc, fake)
    fake.start_result = fake.otp
    svc.start(2, EMAIL, PW)
    assert svc.disconnect(1).code == "disconnected"
    acc = store.zepp_account(1)
    assert acc is not None and acc.app_token is None and acc.cookies_json == "{}"
    assert acc.status == "reconnect" and acc.last_sync_ts is None
    assert store.body_measurements(1) == []
    assert 2 in svc._pending  # druga osoba nietknięta
    svc.disconnect(2)
    assert svc._pending == {}


def test_disconnect_keeps_manual_weight_and_other_person(
    svc: ZeppService, fake: FakeZepp, store: Store
) -> None:
    _link(svc, fake)
    store.add_weight(1, date(2020, 1, 1), 99.0)
    store.add_body_measurements(2, [BodyRow(ts=5, source="zepp", weight_kg=60.0)])
    store.add_weight_if_not_manual(2, date(2020, 1, 2), 60.0)
    svc.disconnect(1)
    assert store.weights(1, 36500) == [(date(2020, 1, 1), 99.0)]
    assert len(store.body_measurements(2)) == 1 and store.latest_weight(2) == 60.0


def test_member_change_purges_old_data(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    fake.member_list = [Member(7, "Ala", 170.0), Member(8, "Ola", 165.0)]
    _link(svc, fake)
    svc.member(1, 7)
    fake.records = [_rec(NOW + 5000, 61.0)]
    svc.member(1, 8)
    assert [r.weight_kg for r in store.body_measurements(1)] == [61.0]


def test_same_member_choice_keeps_data(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    fake.member_list = [Member(7, "Ala", 170.0), Member(8, "Ola", 165.0)]
    _link(svc, fake)
    svc.member(1, 7)
    before = len(store.body_measurements(1))
    svc.member(1, 7)
    assert len(store.body_measurements(1)) == before > 0


def test_relogin_other_account_purges_old_data(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    assert len(store.body_measurements(1)) > 0
    fake.member_list = [Member(9, "Inna", 160.0)]
    fake.records = [_rec(NOW + 5000, 61.0)]
    _link(svc, fake)
    assert [r.weight_kg for r in store.body_measurements(1)] == [61.0]


def test_otp_relogin_other_account_purges_old_data(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    fake.member_list = [Member(9, "Inna", 160.0)]
    fake.records = [_rec(NOW + 5000, 61.0)]
    fake.start_result = fake.otp
    assert svc.start(1, EMAIL, PW).code == "otp"
    assert svc.send(1).code == "sent"
    assert svc.verify(1, "123456").code == "ok"
    assert [r.weight_kg for r in store.body_measurements(1)] == [61.0]


def test_relogin_with_empty_member_list_keeps_data(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    before = len(store.body_measurements(1))
    fake.member_list = []
    _link(svc, fake)
    assert len(store.body_measurements(1)) == before > 0


def test_view_without_account(svc: ZeppService) -> None:
    view = svc.view(2)
    assert view.status is None and view.count == 0 and view.members == ()


def test_sync_picks_up_late_upload_within_lookback_without_duplicates(
    svc: ZeppService, fake: FakeZepp, clock: Clock, store: Store
) -> None:
    _link(svc, fake)
    assert len(store.body_measurements(1)) == 2
    fake.records.append(_rec(NOW - 3 * DAY, 72.0))  # starszy ts, wgrany po ostatniej synchronizacji
    clock.t = NOW + 100
    assert svc.sync(1).n == 1
    assert len(store.body_measurements(1)) == 3
    clock.t = NOW + 200
    assert svc.sync(1).n == 0
    assert len(store.body_measurements(1)) == 3


def test_abandoned_otp_on_linked_account_keeps_it(svc: ZeppService, fake: FakeZepp, store: Store) -> None:
    _link(svc, fake)
    before = store.zepp_account(1)
    fake.start_result = fake.otp
    assert svc.start(1, EMAIL, PW).code == "otp"
    assert store.zepp_account(1) == before
    assert svc.view(1).pending_alive is True
    assert svc.sync(1).code == "ok"


def test_start_otp_on_reconnect_account_saves_pending_otp(
    svc: ZeppService, fake: FakeZepp, store: Store
) -> None:
    _link(svc, fake)
    store.set_zepp_status(1, "reconnect")
    fake.start_result = fake.otp
    assert svc.start(1, EMAIL, PW).code == "otp"
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "pending_otp"
