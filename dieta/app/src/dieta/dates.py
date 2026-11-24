"""Wspólny parser dat ISO (także z czasem) używany w kilku modułach."""

from __future__ import annotations

from datetime import date


def parse_iso_date(value: str | None) -> date | None:
    """Data z ``YYYY-MM-DD`` (albo ``YYYY-MM-DDT…``); pusta lub niepoprawna wartość → None."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None
