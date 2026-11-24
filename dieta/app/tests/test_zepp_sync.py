"""Testy synchronizacji Zepp w tle: fałszywy zegar, ``zepp`` i sender (bez sieci i sleep)."""

import asyncio
import logging
from pathlib import Path

import pytest
from test_zepp_service import EMAIL, NOW, PW, Clock, FakeZepp

from dieta import zepp_sync
from dieta.settings import Settings
from dieta.store import Store
from dieta.zepp import Linked, Member, ReconnectNeeded, ZeppToken
from dieta.zepp_service import LOGIN_MIN_INTERVAL_S, ZeppService

HOUR = LOGIN_MIN_INTERVAL_S


class Sender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str, str]] = []

    def __call__(self, service: str, title: str, message: str, path: str) -> bool:
        self.sent.append((service, title, message, path))
        return True


def _settings(tmp_path: Path, *, push_notify: str = "family", **kw: bool) -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_level="info",
        dev=True,
        person_1_name="Ala",
        person_2_name="Olek",
        push_notify=push_notify,
        **kw,
    )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeZepp:
    return FakeZepp(monkeypatch)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "dieta.db")


@pytest.fixture
def svc(store: Store, clock: Clock, fake: FakeZepp) -> ZeppService:
    return ZeppService(store, http=lambda *a, **k: pytest.fail("sieć"), now=clock)


@pytest.fixture
def sender() -> Sender:
    return Sender()


def _link(svc: ZeppService, fake: FakeZepp, pid: int = 1) -> None:
    fake.start_result = Linked(ZeppToken("1000001", "TOK-0"), {"passToken": "COOKIE-SECRET"})
    assert svc.start(pid, EMAIL, PW).code == "ok"


def _refreshes(fake: FakeZepp) -> int:
    return [c[0] for c in fake.calls].count("refresh")


def test_syncs_only_linked_accounts_with_fuid(
    svc: ZeppService, fake: FakeZepp, store: Store, tmp_path: Path, sender: Sender
) -> None:
    _link(svc, fake, 1)
    fake.member_list = [Member(7, "A", None), Member(8, "B", None)]
    _link(svc, fake, 2)  # dwóch członków → brak fuid
    assert store.zepp_account(2) is not None and store.zepp_account(2).fuid is None  # type: ignore[union-attr]
    fake.calls.clear()
    results = zepp_sync.run_once(svc, store, _settings(tmp_path), sender)
    assert results == {1: "ok"}
    assert [c[0] for c in fake.calls] == ["records"]
    assert sender.sent == []


def test_401_refreshes_exactly_once(
    svc: ZeppService, fake: FakeZepp, store: Store, tmp_path: Path, sender: Sender
) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("token")]
    assert zepp_sync.run_once(svc, store, _settings(tmp_path), sender) == {1: "ok"}
    assert _refreshes(fake) == 1


def test_login_limit_one_per_hour(
    svc: ZeppService, fake: FakeZepp, store: Store, clock: Clock, tmp_path: Path, sender: Sender
) -> None:
    _link(svc, fake)
    settings = _settings(tmp_path)
    fake.record_excs = [ReconnectNeeded("a")]
    assert zepp_sync.run_once(svc, store, settings, sender) == {1: "ok"}
    assert _refreshes(fake) == 1
    clock.t = NOW + 600
    fake.record_excs = [ReconnectNeeded("b")]
    assert zepp_sync.run_once(svc, store, settings, sender) == {1: "expired"}
    assert _refreshes(fake) == 1  # limit: brak drugiego refresh
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "linked" and acc.last_error == "limit logowań"
    assert sender.sent == []
    clock.t = NOW + HOUR + 1  # po godzinie można znów
    fake.record_excs = [ReconnectNeeded("c")]
    assert zepp_sync.run_once(svc, store, settings, sender) == {1: "ok"}
    assert _refreshes(fake) == 2


def test_refresh_failure_pushes_once_then_silent_then_relink_rearms(
    svc: ZeppService, fake: FakeZepp, store: Store, tmp_path: Path, sender: Sender
) -> None:
    _link(svc, fake)
    settings = _settings(tmp_path)
    fake.record_excs = [ReconnectNeeded("a")]
    fake.refresh_excs = [ReconnectNeeded("sesja Xiaomi wygasła")]
    assert zepp_sync.run_once(svc, store, settings, sender) == {1: "expired"}
    acc = store.zepp_account(1)
    assert acc is not None and acc.status == "reconnect"
    assert len(sender.sent) == 1
    service, title, message, _path = sender.sent[0]
    assert service == "family" and title == "Dieta" and message.startswith("Połącz ponownie konto Zepp — ")
    assert zepp_sync.run_once(svc, store, settings, sender) == {}
    assert len(sender.sent) == 1
    _link(svc, fake)  # ponowne połączenie czyści znacznik
    fake.record_excs = [ReconnectNeeded("a")]
    fake.refresh_excs = [ReconnectNeeded("znowu")]
    zepp_sync.run_once(svc, store, settings, sender)
    assert len(sender.sent) == 2


def test_empty_push_notify_means_no_push_and_no_marker(
    svc: ZeppService, fake: FakeZepp, store: Store, tmp_path: Path, sender: Sender
) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("a")]
    fake.refresh_excs = [ReconnectNeeded("x")]
    zepp_sync.run_once(svc, store, _settings(tmp_path, push_notify=""), sender)
    assert sender.sent == []
    zepp_sync.run_once(svc, store, _settings(tmp_path), sender)  # po włączeniu push idzie
    assert len(sender.sent) == 1


def test_failed_send_does_not_set_marker(
    svc: ZeppService, fake: FakeZepp, store: Store, tmp_path: Path
) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("a")]
    fake.refresh_excs = [ReconnectNeeded("x")]
    calls: list[str] = []

    def failing(service: str, title: str, message: str, path: str) -> bool:
        calls.append(message)
        return False

    settings = _settings(tmp_path)
    zepp_sync.run_once(svc, store, settings, failing)
    zepp_sync.run_once(svc, store, settings, failing)
    assert len(calls) == 2


def test_start_respects_options(svc: ZeppService, store: Store, tmp_path: Path) -> None:
    async def go() -> None:
        assert zepp_sync.start(svc, store, _settings(tmp_path, zepp_sync=False)) is None
        assert zepp_sync.start(svc, store, _settings(tmp_path, zepp_enabled=False)) is None
        task = zepp_sync.start(svc, store, _settings(tmp_path))
        assert task is not None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(go())


def test_no_secrets_in_logs(
    svc: ZeppService,
    fake: FakeZepp,
    store: Store,
    tmp_path: Path,
    sender: Sender,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _link(svc, fake)
    fake.record_excs = [ReconnectNeeded("a")]
    fake.refresh_excs = [ReconnectNeeded("x")]
    with caplog.at_level(logging.DEBUG):
        zepp_sync.run_once(svc, store, _settings(tmp_path), sender)
    text = caplog.text + repr(sender.sent)
    for secret in ("TOK-0", "TOK-1", "COOKIE-SECRET", PW, EMAIL):
        assert secret not in text
