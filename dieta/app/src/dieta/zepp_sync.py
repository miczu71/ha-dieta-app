"""Synchronizacja Zepp w tle: co 6 h odświeża wagę, a utratę sesji zgłasza jednym pushem."""

from __future__ import annotations

import asyncio
import logging

from . import push
from .names import person_name
from .settings import Settings
from .store import PERSON_IDS, Store
from .zepp_service import ZeppService, reconnect_pushed_key

log = logging.getLogger(__name__)

FIRST_DELAY_S = 5 * 60
INTERVAL_S = 6 * 3600


def run_once(
    service: ZeppService,
    store: Store,
    settings: Settings,
    sender: push.Sender = push.send,
) -> dict[int, str]:
    """Jeden przebieg: sync kont ``linked`` z ``fuid``; kod wyniku na osobę; push raz na utratę sesji."""
    results: dict[int, str] = {}
    for pid in PERSON_IDS:
        acc = store.zepp_account(pid)
        if acc is None:
            continue
        if acc.status == "linked" and acc.fuid is not None:
            try:
                results[pid] = service.sync(pid).code
            except Exception:
                log.exception("zepp sync: nieoczekiwany błąd (osoba %s)", pid)
                continue
            if results[pid] not in ("ok", "expired"):
                log.warning("zepp sync: osoba %s → %s", pid, results[pid])
            acc = store.zepp_account(pid)
        if acc is None or acc.status != "reconnect" or not settings.push_notify:
            continue
        if store.get_meta(reconnect_pushed_key(pid)):
            continue
        message = f"Połącz ponownie konto Zepp — {person_name(store, settings, pid)}"
        if sender(settings.push_notify, "Dieta", message, push._panel_path()):
            store.set_meta(reconnect_pushed_key(pid), "1")
    return results


async def _loop(service: ZeppService, store: Store, settings: Settings) -> None:
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            await asyncio.to_thread(run_once, service, store, settings)
        except Exception as exc:  # noqa: BLE001 — pętla tła ma przeżyć; logujemy tylko typ (bez tokenów)
            log.warning("sync Zepp zakończony błędem: %s", type(exc).__name__)
        await asyncio.sleep(INTERVAL_S)


def start(service: ZeppService, store: Store, settings: Settings) -> asyncio.Task[None] | None:
    """Pętla tła; ``None``, gdy konto Zepp albo ``zepp_sync`` wyłączone."""
    if not (settings.zepp_enabled and settings.zepp_sync):
        return None
    return asyncio.create_task(_loop(service, store, settings))
