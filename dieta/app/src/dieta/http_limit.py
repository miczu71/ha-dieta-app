"""Limit rozmiaru odpowiedzi HTTP czytanych z cudzych API."""

from __future__ import annotations

from typing import Protocol


class ResponseTooLarge(ValueError):
    """Odpowiedź przekroczyła dozwolony rozmiar."""


class _Readable(Protocol):
    def read(self, size: int = ..., /) -> bytes: ...


def read_limited(resp: _Readable, max_bytes: int) -> bytes:
    """Czyta ciało odpowiedzi; więcej niż ``max_bytes`` → ``ResponseTooLarge``."""
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ResponseTooLarge(f"odpowiedź większa niż {max_bytes} B")
    return data
