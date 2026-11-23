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
    unit_price: float | None = None  # mediana płaconej ceny (zł/kg gdy is_weight, inaczej zł/szt.)
    is_weight: bool | None = None  # None = stary Zakupy bez pól cen
    last_unit_price: float | None = None


@dataclass(frozen=True)
class CatalogItem:
    ean: str
    name: str
    brand: str | None
    size: float | None
    size_unit: str | None
    price: float
    observed_at: str


@dataclass(frozen=True)
class ShelfItem:
    """Cena z półki Biedronki (cache Zakupów, `/api/v1/shelf`)."""

    ean: str
    name: str | None
    size: float | None
    size_unit: str | None
    price: float
    regular: float | None
    promo_from: str | None
    promo_to: str | None
    is_promotion: bool
    card_only: bool
    fetched_at: str


Opener = Callable[[urllib.request.Request], Any]


def _open(req: urllib.request.Request) -> Any:
    return urllib.request.urlopen(req, timeout=TIMEOUT_S)


def _opt_float(value: object) -> float | None:
    return None if value is None else float(str(value))


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
        unit_price=_opt_float(row.get("unit_price")),
        is_weight=None if row.get("is_weight") is None else bool(row["is_weight"]),
        last_unit_price=_opt_float(row.get("last_unit_price")),
    )


def _get_json(base_url: str, token: str, path: str, opener: Opener | None) -> Any:
    if not token:
        raise ZakupyError("Brak tokena API Zakupów w opcjach Diety.")
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ZakupyError("Nieprawidłowy adres Zakupów (wymagany http:// lub https://).")
    url = f"{base_url.strip().rstrip('/')}{path}"
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
        return json.loads(raw)
    except ValueError:
        raise ZakupyError("Nieprawidłowa odpowiedź Zakupów.") from None


def fetch_purchases(
    base_url: str, token: str, months: int = 12, *, opener: Opener | None = None
) -> list[Purchase]:
    payload = _get_json(base_url, token, f"/api/v1/purchases?months={int(months)}", opener)
    try:
        return [_parse_product(row) for row in payload["products"]]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ZakupyError("Nieprawidłowa odpowiedź Zakupów.") from None


def _parse_catalog_item(row: dict[str, Any], source: str) -> CatalogItem | None:
    observations = [
        o for o in row.get("observations") or [] if o.get("source") == source and o.get("price") is not None
    ]
    if not observations:
        return None
    last = max(observations, key=lambda o: str(o["observed_at"]))
    price = float(last["price"])
    if price <= 0:
        return None
    return CatalogItem(
        ean=str(row["ean"]),
        name=str(row["name"]),
        brand=None if row.get("brand") is None else str(row["brand"]),
        size=_opt_float(row.get("size")),
        size_unit=None if row.get("size_unit") is None else str(row["size_unit"]),
        price=price,
        observed_at=str(last["observed_at"]),
    )


def fetch_catalog(
    base_url: str, token: str, source: str = "frisco", *, opener: Opener | None = None
) -> list[CatalogItem]:
    """Katalog cen rynkowych (`/api/v1/catalog`); cena z ostatniej obserwacji `source`."""
    query = urllib.parse.urlencode({"source": source})
    payload = _get_json(base_url, token, f"/api/v1/catalog?{query}", opener)
    try:
        items = (_parse_catalog_item(row, source) for row in payload["products"])
        return [item for item in items if item is not None]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ZakupyError("Nieprawidłowa odpowiedź Zakupów.") from None


def _opt_str(value: object) -> str | None:
    return None if value is None else str(value)


def _parse_shelf_item(row: dict[str, Any]) -> ShelfItem | None:
    price = _opt_float(row.get("price"))
    if price is None or price <= 0:
        return None
    product = row.get("product") or {}
    return ShelfItem(
        ean=str(row["ean"]),
        name=_opt_str(product.get("name")),
        size=_opt_float(product.get("size")),
        size_unit=_opt_str(product.get("size_unit")),
        price=price,
        regular=_opt_float(row.get("regular")),
        promo_from=_opt_str(row.get("promo_from")),
        promo_to=_opt_str(row.get("promo_to")),
        is_promotion=bool(row.get("is_promotion")),
        card_only=bool(row.get("card_only")),
        fetched_at=str(row["fetched_at"]),
    )


def fetch_shelf(base_url: str, token: str, *, opener: Opener | None = None) -> list[ShelfItem]:
    """Cache cen Biedronki (`/api/v1/shelf`); wiersze bez ceny > 0 są pomijane."""
    payload = _get_json(base_url, token, "/api/v1/shelf", opener)
    try:
        items = (_parse_shelf_item(row) for row in payload["items"])
        return [item for item in items if item is not None]
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ZakupyError("Nieprawidłowa odpowiedź Zakupów.") from None
