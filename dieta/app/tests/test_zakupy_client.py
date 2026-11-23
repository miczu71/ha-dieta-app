import json
import urllib.error
import urllib.request
from datetime import date
from typing import Any

import pytest

from dieta.zakupy_client import CatalogItem, Purchase, ZakupyError, fetch_catalog, fetch_purchases

TOKEN = "TOKEN-TESTOWY-987"

PAYLOAD: dict[str, Any] = {
    "generated_at": "2026-11-22T10:00:00",
    "months": 12,
    "products": [
        {
            "code": "n:0001497",
            "chain": "lidl",
            "name": "Twaróg klinek półtł.",
            "names": ["Twaróg klinek półtł."],
            "purchases": 2,
            "spend": 49.91,
            "first": "2026-02-03",
            "last": "2026-11-20",
            "dates": ["2026-02-03", "2026-11-20"],
            "category": "food",
        }
    ],
}


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None


class _Recorder:
    def __init__(self, body: bytes | None = None, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.requests: list[urllib.request.Request] = []

    def __call__(self, req: urllib.request.Request) -> _FakeResponse:
        self.requests.append(req)
        if self.error is not None:
            raise self.error
        assert self.body is not None
        return _FakeResponse(self.body)


def test_fetch_parses_products_and_sends_bearer_token() -> None:
    opener = _Recorder(json.dumps(PAYLOAD).encode())
    result = fetch_purchases("http://f0987e0f-lidl:8099/", TOKEN, months=12, opener=opener)

    assert result == [
        Purchase(
            code="n:0001497",
            chain="lidl",
            name="Twaróg klinek półtł.",
            names=("Twaróg klinek półtł.",),
            purchases=2,
            spend=49.91,
            first=date(2026, 2, 3),
            last=date(2026, 11, 20),
            dates=(date(2026, 2, 3), date(2026, 11, 20)),
            category="food",
        )
    ]
    req = opener.requests[0]
    assert req.full_url == "http://f0987e0f-lidl:8099/api/v1/purchases?months=12"
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"


@pytest.mark.parametrize("code", [401, 403])
def test_wrong_token_raises_zakupy_error_without_token_in_message(code: int) -> None:
    err = urllib.error.HTTPError("http://x/api/v1/purchases", code, "Unauthorized", {}, None)
    with pytest.raises(ZakupyError) as info:
        fetch_purchases("http://x:8099", TOKEN, opener=_Recorder(error=err))
    assert TOKEN not in str(info.value)


def test_disabled_api_503_raises_zakupy_error() -> None:
    err = urllib.error.HTTPError("http://x/api/v1/purchases", 503, "Unavailable", {}, None)
    with pytest.raises(ZakupyError):
        fetch_purchases("http://x:8099", TOKEN, opener=_Recorder(error=err))


def test_network_error_raises_zakupy_error() -> None:
    with pytest.raises(ZakupyError):
        fetch_purchases("http://x:8099", TOKEN, opener=_Recorder(error=urllib.error.URLError("down")))


def test_invalid_json_raises_zakupy_error() -> None:
    with pytest.raises(ZakupyError):
        fetch_purchases("http://x:8099", TOKEN, opener=_Recorder(b"<html>"))


def test_missing_fields_raise_zakupy_error() -> None:
    with pytest.raises(ZakupyError):
        fetch_purchases("http://x:8099", TOKEN, opener=_Recorder(b'{"products": [{"code": "x"}]}'))


def test_empty_token_fails_before_network() -> None:
    opener = _Recorder(json.dumps(PAYLOAD).encode())
    with pytest.raises(ZakupyError):
        fetch_purchases("http://x:8099", "", opener=opener)
    assert opener.requests == []


def test_non_http_scheme_is_rejected() -> None:
    with pytest.raises(ZakupyError):
        fetch_purchases("file:///etc/passwd", TOKEN, opener=_Recorder(b"{}"))


def test_old_zakupy_response_has_no_prices() -> None:
    rec = _Recorder(body=json.dumps(PAYLOAD).encode())
    [p] = fetch_purchases("http://z:8099", TOKEN, opener=rec)
    assert (p.unit_price, p.is_weight, p.last_unit_price) == (None, None, None)


def test_new_zakupy_response_parses_prices() -> None:
    row = dict(PAYLOAD["products"][0], unit_price=24.9, is_weight=True, last_unit_price=26.5)
    other = dict(PAYLOAD["products"][0], code="x", unit_price=None, is_weight=False, last_unit_price=None)
    body = json.dumps({"products": [row, other]}).encode()
    a, b = fetch_purchases("http://z:8099", TOKEN, opener=_Recorder(body=body))
    assert (a.unit_price, a.is_weight, a.last_unit_price) == (24.9, True, 26.5)
    assert (b.unit_price, b.is_weight, b.last_unit_price) == (None, False, None)


CATALOG_PAYLOAD: dict[str, Any] = {
    "generated_at": "2026-11-22T10:00:00",
    "products": [
        {
            "ean": "5900001",
            "name": "Mleko 2%",
            "brand": "Mlekovita",
            "size": 1.0,
            "size_unit": "Litre",
            "frisco_id": "1",
            "observations": [
                {"source": "frisco", "price": 3.2, "observed_at": "2026-11-10T08:00:00"},
                {"source": "frisco", "price": 3.5, "observed_at": "2026-11-20T08:00:00"},
                {"source": "inne", "price": 1.0, "observed_at": "2026-11-21T08:00:00"},
            ],
        },
        {"ean": "5900002", "name": "Bez obserwacji", "size": 1, "size_unit": "Kilogram", "observations": []},
        {
            "ean": "5900003",
            "name": "Zero",
            "size": 1,
            "size_unit": "Kilogram",
            "observations": [{"source": "frisco", "price": 0, "observed_at": "2026-11-20T08:00:00"}],
        },
    ],
}


def test_fetch_catalog_uses_last_observation_and_skips_empty() -> None:
    opener = _Recorder(json.dumps(CATALOG_PAYLOAD).encode())
    result = fetch_catalog("http://x:8099/", TOKEN, opener=opener)
    assert result == [
        CatalogItem("5900001", "Mleko 2%", "Mlekovita", 1.0, "Litre", 3.5, "2026-11-20T08:00:00")
    ]
    req = opener.requests[0]
    assert req.full_url == "http://x:8099/api/v1/catalog?source=frisco"
    assert req.get_header("Authorization") == f"Bearer {TOKEN}"


def test_fetch_catalog_without_token_or_bad_url() -> None:
    opener = _Recorder(b"{}")
    with pytest.raises(ZakupyError):
        fetch_catalog("http://x:8099", "", opener=opener)
    with pytest.raises(ZakupyError):
        fetch_catalog("ftp://x", TOKEN, opener=opener)
    assert opener.requests == []


def test_fetch_catalog_401_hides_token() -> None:
    err = urllib.error.HTTPError("http://x/api/v1/catalog", 401, "Unauthorized", {}, None)
    with pytest.raises(ZakupyError) as info:
        fetch_catalog("http://x:8099", TOKEN, opener=_Recorder(error=err))
    assert TOKEN not in str(info.value)


def test_fetch_catalog_invalid_payload() -> None:
    with pytest.raises(ZakupyError):
        fetch_catalog("http://x:8099", TOKEN, opener=_Recorder(b'{"nope": 1}'))
