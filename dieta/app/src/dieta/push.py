"""Poranny push — szkielet (treść w paczce W3)."""

from __future__ import annotations

import asyncio

from .settings import Settings
from .store import Store


def start(settings: Settings, store: Store) -> asyncio.Task[None] | None:
    del settings, store
    return None
