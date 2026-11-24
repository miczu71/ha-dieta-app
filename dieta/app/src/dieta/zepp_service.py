"""Łączenie konta Zepp i synchronizacja wagi (logika nad ``zepp`` + ``body`` + ``Store``).

Stan ``NeedsOtp`` (ciasteczka sesji logowania) żyje tylko w pamięci procesu; na dysku (``zepp_account``) są
``device_id``, ciasteczka Xiaomi, token Zepp, ``fuid`` i status — nigdy hasło ani e-mail.
Metody są synchroniczne i blokują siecią: wołaj je przez ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import TypeVar

from . import body, zepp
from .store import Store, ZeppAccount
from .zepp import Http, Linked, Member, NeedsOtp, ReconnectNeeded, WrongCode, ZeppError, ZeppToken

PENDING_TTL_S = 15 * 60
LOOKBACK_S = 14 * 86400  # pomiar wgrany do chmury z opóźnieniem ma starszy generatedTime
LOGIN_MIN_INTERVAL_S = 3600  # limit automatycznych logowań (refresh) na konto

T = TypeVar("T")


def login_ts_key(person_id: int) -> str:
    return f"zepp_login_ts:{person_id}"


def reconnect_pushed_key(person_id: int) -> str:
    return f"zepp_reconnect_pushed:{person_id}"


class _LoginRateLimited(ReconnectNeeded):
    """Za częste logowanie: konto zostaje ``linked``, kolejny przebieg spróbuje ponownie."""


@dataclass(frozen=True)
class Outcome:
    """Wynik operacji: kod komunikatu (``?zepp=``) i opcjonalnie liczba nowych pomiarów."""

    code: str
    n: int | None = None


@dataclass
class _Pending:
    state: NeedsOtp = field(repr=False)
    sent: bool
    created: float


@dataclass(frozen=True)
class ZeppView:
    """Dane dla szablonu: bez ciasteczek, tokenu i hasła."""

    status: str | None
    fuid: int | None
    last_sync_ts: int | None
    last_error: str | None
    count: int
    members: tuple[Member, ...]
    pending_alive: bool
    pending_sent: bool


class ZeppService:
    def __init__(
        self,
        store: Store,
        *,
        http: Http = zepp.default_http,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._http = http
        self._now = now
        self._pending: dict[int, _Pending] = {}
        self._members: dict[int, tuple[Member, ...]] = {}

    # --- widok ---------------------------------------------------------------------------------

    def _live_pending(self, person_id: int) -> _Pending | None:
        pending = self._pending.get(person_id)
        if pending is not None and self._now() - pending.created > PENDING_TTL_S:
            del self._pending[person_id]
            return None
        return pending

    def view(self, person_id: int) -> ZeppView:
        acc = self._store.zepp_account(person_id)
        pending = self._live_pending(person_id)
        return ZeppView(
            status=acc.status if acc else None,
            fuid=acc.fuid if acc else None,
            last_sync_ts=acc.last_sync_ts if acc else None,
            last_error=acc.last_error if acc else None,
            count=self._store.count_body(person_id) if acc else 0,
            members=self._members.get(person_id, ()),
            pending_alive=pending is not None,
            pending_sent=pending is not None and pending.sent,
        )

    # --- logowanie -----------------------------------------------------------------------------

    def _save(self, person_id: int, **changes: object) -> ZeppAccount:
        acc = self._store.zepp_account(person_id)
        assert acc is not None
        acc = replace(acc, **changes)  # type: ignore[arg-type]
        self._store.save_zepp_account(acc)
        return acc

    def _store_link(self, person_id: int, device_id: str, linked: Linked, *, keep_fuid: bool) -> None:
        old = self._store.zepp_account(person_id)
        self._store.save_zepp_account(
            ZeppAccount(
                person_id=person_id,
                device_id=device_id,
                cookies_json=json.dumps(linked.cookies),
                user_id=linked.token.user_id,
                app_token=linked.token.app_token,
                fuid=old.fuid if old and keep_fuid else None,
                status="linked",
                last_sync_ts=old.last_sync_ts if old and keep_fuid else None,
                last_error=None,
            )
        )

    def start(self, person_id: int, email: str, password: str) -> Outcome:
        email = email.strip()
        if not email or not password:
            return Outcome("login_failed")
        old = self._store.zepp_account(person_id)
        device_id = old.device_id if old else zepp.new_device_id()
        try:
            result = zepp.start_login(email, password, device_id, http=self._http)
        except ZeppError:
            return Outcome("login_failed")
        if isinstance(result, Linked):
            self._pending.pop(person_id, None)
            return self._finish_link(person_id, device_id, result, keep_fuid=True)
        self._pending[person_id] = _Pending(result, sent=False, created=self._now())
        if old is not None and old.status == "linked":
            return Outcome("otp")  # porzucone logowanie nie nadpisuje działającego konta
        self._store.save_zepp_account(
            ZeppAccount(
                person_id=person_id,
                device_id=device_id,
                cookies_json="{}",
                user_id=None,
                app_token=None,
                fuid=old.fuid if old else None,
                status="pending_otp",
                last_sync_ts=old.last_sync_ts if old else None,
                last_error=None,
            )
        )
        return Outcome("otp")

    def send(self, person_id: int) -> Outcome:
        pending = self._live_pending(person_id)
        if pending is None:
            return Outcome("expired")
        try:
            state = zepp.send_ticket(pending.state, http=self._http)
        except ZeppError:
            return Outcome("error")
        self._pending[person_id] = _Pending(state, sent=True, created=pending.created)
        return Outcome("sent")

    def verify(self, person_id: int, code: str) -> Outcome:
        pending = self._live_pending(person_id)
        if pending is None:
            return Outcome("expired")
        try:
            cookies = zepp.verify(pending.state, code.strip(), http=self._http)
            linked = zepp.refresh(cookies, pending.state.device_id, http=self._http)
        except WrongCode:
            return Outcome("wrong_code")
        except ZeppError:
            return Outcome("error")
        del self._pending[person_id]
        return self._finish_link(person_id, pending.state.device_id, linked, keep_fuid=False)

    def _finish_link(self, person_id: int, device_id: str, linked: Linked, *, keep_fuid: bool) -> Outcome:
        self._store_link(person_id, device_id, linked, keep_fuid=keep_fuid)
        self._store.set_meta(reconnect_pushed_key(person_id), "")
        try:
            members = zepp.members(linked.token, http=self._http)
        except ZeppError as exc:
            self._save(person_id, last_error=str(exc))
            return Outcome("error")
        self._members[person_id] = tuple(members)
        acc = self._store.zepp_account(person_id)
        if acc is not None and acc.fuid is not None and any(m.fuid == acc.fuid for m in members):
            return self.sync(person_id)
        if len(members) == 1:
            self._save(person_id, fuid=members[0].fuid, last_sync_ts=None)
            return self.sync(person_id)
        self._save(person_id, fuid=None, last_sync_ts=None)
        return Outcome("ok")

    def member(self, person_id: int, fuid: int) -> Outcome:
        acc = self._store.zepp_account(person_id)
        if acc is None or acc.status != "linked" or acc.app_token is None:
            return Outcome("error")
        try:
            members = self._call(person_id, lambda token: zepp.members(token, http=self._http))
        except ReconnectNeeded:
            return Outcome("expired")
        except ZeppError:
            return Outcome("error")
        self._members[person_id] = tuple(members)
        if fuid not in {m.fuid for m in members}:
            return Outcome("error")
        self._save(person_id, fuid=fuid, last_sync_ts=None)
        return self.sync(person_id)

    # --- synchronizacja ------------------------------------------------------------------------

    def _check_login_limit(self, person_id: int) -> None:
        raw = self._store.get_meta(login_ts_key(person_id))
        if raw and raw.isdigit() and self._now() - int(raw) < LOGIN_MIN_INTERVAL_S:
            raise _LoginRateLimited("limit logowań")

    def _call(self, person_id: int, fn: Callable[[ZeppToken], T]) -> T:
        """Wywołanie z tokenem konta; 401 → jedno ``refresh`` i ponowna próba. Błąd zapisuje w koncie."""
        acc = self._store.zepp_account(person_id)
        if acc is None or acc.app_token is None or acc.user_id is None:
            raise ReconnectNeeded("brak tokenu")
        try:
            try:
                return fn(ZeppToken(acc.user_id, acc.app_token))
            except ReconnectNeeded:
                self._check_login_limit(person_id)
                linked = zepp.refresh(json.loads(acc.cookies_json), acc.device_id, http=self._http)
                self._store.set_meta(login_ts_key(person_id), str(int(self._now())))
                self._save(
                    person_id,
                    cookies_json=json.dumps(linked.cookies),
                    user_id=linked.token.user_id,
                    app_token=linked.token.app_token,
                )
                return fn(linked.token)
        except _LoginRateLimited as exc:
            self._store.set_zepp_status(person_id, "linked", last_error=str(exc))
            raise
        except ReconnectNeeded as exc:
            self._store.set_zepp_status(person_id, "reconnect", last_error=str(exc))
            raise
        except ZeppError as exc:
            self._store.set_zepp_status(person_id, "linked", last_error=str(exc))
            raise

    def sync(self, person_id: int) -> Outcome:
        acc = self._store.zepp_account(person_id)
        if acc is None or acc.status != "linked" or acc.fuid is None:
            return Outcome("expired" if acc is not None and acc.status == "reconnect" else "error")
        fuid = acc.fuid
        since = max(0, acc.last_sync_ts - LOOKBACK_S) if acc.last_sync_ts else 0
        now = int(self._now())
        try:
            records = self._call(
                person_id,
                lambda token: zepp.weight_records(token, fuid, since_ts=since, now_ts=now, http=self._http),
            )
        except ReconnectNeeded:
            return Outcome("expired")
        except ZeppError:
            return Outcome("error")
        result = body.import_records(self._store, person_id, records)
        self._store.set_zepp_last_sync(person_id, now)
        self._store.set_zepp_status(person_id, "linked", last_error=None)
        return Outcome("ok", result.new)

    def disconnect(self, person_id: int) -> Outcome:
        self._store.delete_zepp_tokens(person_id)
        self._pending.pop(person_id, None)
        self._members.pop(person_id, None)
        return Outcome("disconnected")
