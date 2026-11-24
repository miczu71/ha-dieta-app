"""Nazwa osoby: wpis z bazy, inaczej opcja add-onu, inaczej „Osoba N”."""

from __future__ import annotations

from .settings import Settings
from .store import Store

MAX_NAME_LEN = 30


def person_name(store: Store, settings: Settings, person_id: int) -> str:
    return store.person_names().get(person_id) or settings.person_name(person_id)


def validate_name(raw: str, other: str) -> str | None:
    """Przycięta nazwa; ``""`` kasuje nazwę; ``None`` = niepoprawna (za długa albo równa drugiej)."""
    name = raw.strip()
    if len(name) > MAX_NAME_LEN:
        return None
    if name and name.casefold() == other.strip().casefold():
        return None
    return name
