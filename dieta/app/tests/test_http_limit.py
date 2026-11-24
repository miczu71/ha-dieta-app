import io

import pytest

from dieta.http_limit import ResponseTooLarge, read_limited


def test_at_limit_ok() -> None:
    assert read_limited(io.BytesIO(b"abcde"), 5) == b"abcde"


def test_over_limit_raises() -> None:
    with pytest.raises(ResponseTooLarge):
        read_limited(io.BytesIO(b"abcdef"), 5)


def test_is_value_error() -> None:
    assert issubclass(ResponseTooLarge, ValueError)
