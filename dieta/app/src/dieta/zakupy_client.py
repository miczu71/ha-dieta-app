"""Klient API Zakupów (`GET /api/v1/purchases`). Tylko stdlib; wywoływane w wątku.

Token trafia wyłącznie do nagłówka Authorization. Komunikaty błędów go nie zawierają.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from typing import Any

TIMEOUT_S = 15


class ZakupyError(Exception):
    """Błąd pobierania danych z Zakupów. Komunikat jest bezpieczny do zapisu w meta/logu."""


@dataclass(frozen=True)
class Purchase:
    code: str
    chain: str
    name: str
    names: tuple[str, ...]
    purchases: int
    spend: float
    first: date
    last: date
    dates: tuple[date, ...]
    category: str


Opener = Callable[[urllib.request.Request], Any]


def _open(req: urllib.request.Request) -> Any:
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


def _parse_product(row: dict[str, Any]) -> Purchase:
    return Purchase(
        code=str(row["code"]),
        chain=str(row["chain"]),
        name=str(row["name"]),
        names=tuple(str(n) for n in row["names"]),
        purchases=int(row["purchases"]),
        spend=float(row["spend"]),
        first=date.fromisoformat(str(row["first"])),
        last=date.fromisoformat(str(row["last"])),
        dates=tuple(date.fromisoformat(str(d)) for d in row["dates"]),
        category=str(row.get("category") or "food"),
    )


def fetch_purchases(
    base_url: str, token: str, months: int = 12, *, opener: Opener | None = None
) -> list[Purchase]:
    if not token:
        raise ZakupyError("Brak tokena API Zakupów w opcjach Diety.")
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ZakupyError("Nieprawidłowy adres Zakupów (wymagany http:// lub https://).")
    url = f"{base_url.strip().rstrip('/')}/api/v1/purchases?months={int(months)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="GET")
    open_fn = opener or _open
    try:
        with closing(open_fn(req)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ZakupyError(f"Zakupy odrzuciły token (HTTP {exc.code}).") from None
        if exc.code == 503:
            raise ZakupyError("API Zakupów wyłączone (brak api_token po stronie Zakupów).") from None
        raise ZakupyError(f"Zakupy zwróciły HTTP {exc.code}.") from None
    except (urllib.error.URLError, OSError):
        raise ZakupyError("Brak połączenia z Zakupami.") from None
    try:
        payload = json.loads(raw)
        return [_parse_product(row) for row in payload["products"]]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ZakupyError("Nieprawidłowa odpowiedź Zakupów.") from None
