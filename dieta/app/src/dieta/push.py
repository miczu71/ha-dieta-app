"""Poranny push: plan dnia do ``notify.<usługa>`` (stdlib urllib, bez nowych zależności)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta

from . import realization
from .realization import DayLog
from .settings import Settings
from .store import PERSON_IDS, Store
from .web.kupujemy import seconds_until

_log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor/core/api/services/notify/"
SERVICE_RE = re.compile(r"^[a-z0-9_]+$")
TIMEOUT_S = 15
MAX_CHARS = 1000
META_LAST = "push_last"
TAG = "dieta-plan-dnia"

Sender = Callable[[str, str, str, str], bool]


def _panel_path() -> str:
    """Ścieżka panelu add-onu w Ingress (host kontenera = slug z `-` zamienionym na `_`)."""
    return "/app/" + os.environ.get("HOSTNAME", "b969c720-dieta").replace("-", "_")


def _portions(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def compose(
    day: DayLog,
    yesterday_unanswered: Mapping[int, int],
    labels: Mapping[int, str],
) -> tuple[str, str]:
    """Tytuł i treść wiadomości (link do panelu idzie w ``data`` przy wysyłce)."""
    lines: list[str] = []
    for meal in day.meals:
        parts = []
        for pid, person in zip(PERSON_IDS, meal.per_person, strict=True):
            if person is None:
                continue
            parts.append(f"{labels[pid]} {_portions(person.portions)} ({round(person.planned_kcal)} kcal)")
        lines.append(f"{meal.slot_label}: {meal.title} — " + ", ".join(parts))
    targets = [
        f"{labels[pid]} {round(person.target_kcal)} kcal"
        for pid, person in zip(PERSON_IDS, day.people, strict=True)
        if person is not None
    ]
    if targets:
        lines.append("Cel: " + ", ".join(targets))
    if sum(yesterday_unanswered.get(pid, 0) for pid in PERSON_IDS) > 0:
        missed = ", ".join(f"{labels[pid]} {yesterday_unanswered.get(pid, 0)}" for pid in PERSON_IDS)
        lines.append(f"Wczoraj bez odpowiedzi: {missed}")
    message = "\n".join(lines)
    if len(message) > MAX_CHARS:
        message = message[: MAX_CHARS - 1] + "…"
    return "Plan na dziś", message


def send(service: str, title: str, message: str, path: str) -> bool:
    """POST do usługi notify przez Supervisora. Nie loguje tokenu ani treści wiadomości."""
    if not SERVICE_RE.fullmatch(service):
        _log.warning("push pominięty: nieprawidłowa nazwa usługi notify")
        return False
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        _log.info("push pominięty: brak SUPERVISOR_TOKEN (tryb dev)")
        return True
    payload = {
        "title": title,
        "message": message,
        "data": {"clickAction": path, "url": path, "tag": TAG},
    }
    request = urllib.request.Request(
        SUPERVISOR_URL + service,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S):
            return True
    except urllib.error.HTTPError as exc:
        _log.warning("push nieudany: HTTP %s", exc.code)
        return False
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _log.warning("push nieudany: %s", type(exc).__name__)
        return False


def run_once(
    settings: Settings,
    store: Store,
    today: date,
    sender: Sender = send,
) -> str:
    """Jedna próba dzienna; zwraca kod wyniku (disabled/already_sent/no_plan/sent/failed)."""
    if not settings.push_notify:
        return "disabled"
    if store.get_meta(META_LAST) == today.isoformat():
        return "already_sent"
    day = realization.day_log(store, settings, today, today)
    if day.plan_status != "approved" or not day.meals:
        return "no_plan"
    yesterday = realization.unanswered_for(store, today - timedelta(days=1), today)
    labels = {pid: settings.person_name(pid) for pid in PERSON_IDS}
    title, message = compose(day, yesterday, labels)
    if not sender(settings.push_notify, title, message, _panel_path()):
        return "failed"
    store.set_meta(META_LAST, today.isoformat())
    return "sent"


async def _loop(settings: Settings, store: Store) -> None:
    while True:
        try:
            await asyncio.sleep(seconds_until(datetime.now(), settings.push_time))
            await asyncio.to_thread(run_once, settings, store, date.today())
        except Exception as exc:  # CancelledError (BaseException) przechodzi dalej
            _log.warning("krok pushu nieudany: %s", type(exc).__name__)


def start(settings: Settings, store: Store) -> asyncio.Task[None] | None:
    """Pętla poranna; ``None``, gdy push wyłączony (pusta opcja ``push_notify``)."""
    if not settings.push_notify:
        return None
    return asyncio.create_task(_loop(settings, store))
