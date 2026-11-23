import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from dieta.settings import Settings
from dieta.todo import send_items

SECRET = "tok-SECRET-123"
URL = "http://supervisor/core/api/services/todo/add_item"
DESCRIPTION = "Dieta, tydzień od 2026-11-23"
ITEMS = [("Pierś z indyka — 900 g", DESCRIPTION), ("Ryż — 400 g", DESCRIPTION)]


class _Response:
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def _settings(tmp_path: Path, entity: str = "todo.zakupy") -> Settings:
    return Settings(data_dir=tmp_path, log_level="info", dev=True, todo_entity=entity)


def test_empty_entity_is_skipped_without_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", SECRET)

    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("nie wysyłamy bez encji")

    assert send_items(_settings(tmp_path, ""), ITEMS, opener=fail) == "skipped"


def test_missing_token_is_skipped_without_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("bez tokenu nie wysyłamy")

    assert send_items(_settings(tmp_path), ITEMS, opener=fail) == "skipped"


def test_posts_each_item_with_bearer_and_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", SECRET)
    calls: list[tuple[str, str | None, dict[str, Any], float]] = []

    def fake(request: urllib.request.Request, timeout: float) -> _Response:
        calls.append(
            (
                request.full_url,
                request.get_header("Authorization"),
                json.loads(request.data or b"{}"),
                timeout,
            )
        )
        return _Response()

    assert send_items(_settings(tmp_path), ITEMS, opener=fake) == "ok"
    assert [c[0] for c in calls] == [URL, URL]
    assert calls[0][1] == f"Bearer {SECRET}"
    assert calls[0][2] == {"entity_id": "todo.zakupy", "item": ITEMS[0][0], "description": ITEMS[0][1]}
    assert calls[1][2]["item"] == ITEMS[1][0]


def test_http_error_returns_failed_and_logs_service_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", SECRET)
    caplog.set_level(logging.INFO)
    calls: list[str] = []

    def fake(request: urllib.request.Request, timeout: float) -> None:
        calls.append(request.full_url)
        raise urllib.error.HTTPError(request.full_url, 400, "bad", None, None)

    assert send_items(_settings(tmp_path), ITEMS, opener=fake) == "failed"
    assert len(calls) == 1, "po pierwszym błędzie kolejne pozycje nie są wysyłane"
    assert "todo.add_item" in caplog.text
    assert "HTTP 400" in caplog.text
    assert SECRET not in caplog.text


def test_network_error_returns_failed_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", SECRET)
    caplog.set_level(logging.INFO)

    def fake(request: urllib.request.Request, timeout: float) -> None:
        raise urllib.error.URLError(f"boom {SECRET}")

    assert send_items(_settings(tmp_path), ITEMS, opener=fake) == "failed"
    assert "URLError" in caplog.text
    assert SECRET not in caplog.text
