"""Prawdziwy ``fetch`` dla konektorów: urllib (stdlib), ≤ 1 zapytanie/s, robots.txt, timeout.

User-Agent nie zawiera danych użytkownika. Zegar i sleep są wstrzykiwalne (testy bez sieci).
"""

from __future__ import annotations

import codecs
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
    """Błąd pobierania; komunikat jest bezpieczny do zapisu w meta/logu.

    ``status`` to kod HTTP (gdy był); ``blocked`` = blokada robots.txt.
    """

    def __init__(self, message: str, status: int | None = None, blocked: bool = False):
        super().__init__(message)
        self.status = status
        self.blocked = blocked

    @property
    def permanent(self) -> bool:
        """Błąd trwały (4xx poza 429, blokada robots) — nie ma sensu ponawiać co noc."""
        client_error = self.status is not None and 400 <= self.status < 500 and self.status != 429
        return client_error or self.blocked


class SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Przekierowania tylko w obrębie tego samego hosta (http/https) i za zgodą robots.txt celu."""

    def __init__(self, allowed: Callable[[str], bool]):
        self._allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        origin = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(urllib.parse.urljoin(req.full_url, newurl))
        if target.scheme not in ("http", "https") or target.netloc != origin.netloc:
            raise FetchError("przekierowanie poza host", status=code)
        if not self._allowed(target.geturl()):
            raise FetchError("zablokowane przez robots.txt", blocked=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_fetch(
    *,
    opener: Opener | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    min_interval: float = MIN_INTERVAL_S,
) -> Fetch:
    """Zwraca ``fetch(url) -> html``: dławiony do ``min_interval`` s między zapytaniami, z robots.txt."""
    last = [float("-inf")]
    robots: dict[str, urllib.robotparser.RobotFileParser | None | bool] = {}  # False = host zablokowany

    def default_open(req: urllib.request.Request) -> Any:
        director = urllib.request.build_opener(SameHostRedirect(allowed))
        return director.open(req, timeout=TIMEOUT_S)

    open_fn: Opener = opener or default_open

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
            with closing(open_fn(req)) as resp:
                raw = resp.read(MAX_BYTES + 1)
                charset = resp.headers.get_content_charset() or "utf-8"
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTPError {exc.code}", status=exc.code) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise FetchError(f"{type(exc).__name__}") from None
        if len(raw) > MAX_BYTES:
            raise FetchError("odpowiedź zbyt duża")
        try:
            codecs.lookup(charset)
        except LookupError:
            charset = "utf-8"
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
            except FetchError as exc:
                # 4xx = brak robots.txt, brak ograniczeń; 5xx/sieć = host zablokowany na ten przebieg
                client_error = exc.status is not None and 400 <= exc.status < 500
                robots[host] = None if client_error else False
        known = robots[host]
        if known is False:
            raise FetchError("robots niedostępny")
        return known is None or known.can_fetch(USER_AGENT, url)  # type: ignore[union-attr]

    def fetch(url: str) -> str:
        parts = urllib.parse.urlsplit(url)
        if url != f"{parts.scheme}://{parts.netloc}/robots.txt" and not allowed(url):
            raise FetchError("zablokowane przez robots.txt", blocked=True)
        return get(url)

    return fetch
