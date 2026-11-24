from datetime import date

from dieta.dates import parse_iso_date


def test_parse_iso_date_plain() -> None:
    assert parse_iso_date("2026-11-24") == date(2026, 11, 24)


def test_parse_iso_date_with_time_takes_first_ten_chars() -> None:
    assert parse_iso_date("2026-11-24T08:15:00") == date(2026, 11, 24)


def test_parse_iso_date_empty_and_none() -> None:
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None


def test_parse_iso_date_garbage() -> None:
    assert parse_iso_date("nie-data") is None
    assert parse_iso_date("2026-13-45") is None
