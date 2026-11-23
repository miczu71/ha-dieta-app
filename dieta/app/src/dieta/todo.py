"""Wysyłka pozycji listy zakupów do listy todo HA (stdlib urllib, bez nowych zależności).

Wzorzec jak w ``push``: Supervisor, token z ``SUPERVISOR_TOKEN``, tryb dev bez tokenu = pominięcie.
Token nie trafia do logów ani do wyniku.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any, Literal

from .settings import Settings

_log = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor/core/api/services/todo/add_item"
TIMEOUT_S = 15

TodoResult = Literal["ok", "failed", "skipped"]
Opener = Callable[..., Any]


def send_items(
    settings: Settings,
    items: Sequence[tuple[str, str]],
    opener: Opener | None = None,
) -> TodoResult:
    """Dodaje pozycje ``(tekst, opis)`` do ``todo_entity``; pierwszy błąd przerywa wysyłkę."""
    if not settings.todo_entity:
        return "skipped"
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        _log.info("todo pominięte: brak SUPERVISOR_TOKEN (tryb dev)")
        return "skipped"
    send = opener or urllib.request.urlopen
    for text, description in items:
        payload = {"entity_id": settings.todo_entity, "item": text, "description": description}
        request = urllib.request.Request(
            SUPERVISOR_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        try:
            with send(request, timeout=TIMEOUT_S):
                pass
        except urllib.error.HTTPError as exc:
            _log.warning("todo nieudane: todo.add_item, HTTP %s", exc.code)
            return "failed"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            _log.warning("todo nieudane: todo.add_item, %s", type(exc).__name__)
            return "failed"
    return "ok"
