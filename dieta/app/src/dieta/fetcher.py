"""Prawdziwy ``fetch`` dla konektorów: urllib (stdlib), ≤ 1 zapytanie/s, robots.txt, timeout.

User-Agent nie zawiera danych użytkownika. Zegar i sleep są wstrzykiwalne (testy bez sieci).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections.abc import Callable
from contextlib import closing
from typing import Any

from dieta.sources.base import Fetch

USER_AGENT = "dieta-addon/0.1"
TIMEOUT_S = 20
MIN_INTERVAL_S = 1.0
MAX_BYTES = 5_000_000

Opener = Callable[[urllib.request.Request], Any]


class FetchError(Exception):
    """Błąd pobierania; komunikat jest bezpieczny do zapisu w meta/logu."""


def _open(req: urllib.request.Request) -> Any:
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


def make_fetch(
    *,
    opener: Opener = _open,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    min_interval: float = MIN_INTERVAL_S,
) -> Fetch:
    """Zwraca ``fetch(url) -> html``: dławiony do ``min_interval`` s między zapytaniami, z robots.txt."""
    last = [float("-inf")]
    robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def get(url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise FetchError("niedozwolony adres")
        wait = min_interval - (clock() - last[0])
        if wait > 0:
            sleep(wait)
        last[0] = clock()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with closing(opener(req)) as resp:
                raw = resp.read(MAX_BYTES + 1)
                charset = resp.headers.get_content_charset() or "utf-8"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise FetchError(f"{type(exc).__name__}") from None
        if len(raw) > MAX_BYTES:
            raise FetchError("odpowiedź zbyt duża")
        text: str = raw.decode(charset, errors="replace")
        return text

    def allowed(url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                parser.parse(get(f"{host}/robots.txt").splitlines())
                robots[host] = parser
            except FetchError:
                robots[host] = None  # brak robots.txt albo błąd sieci → brak ograniczeń
        known = robots[host]
        return known is None or known.can_fetch(USER_AGENT, url)

    def fetch(url: str) -> str:
        if not url.endswith("/robots.txt") and not allowed(url):
            raise FetchError("zablokowane przez robots.txt")
        return get(url)

    return fetch
