import asyncio
import json
import logging
import urllib.error
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from test_web_plan import _demo

from dieta import plan_service as svc
from dieta import push
from dieta.realization import DayLog, MealLog, MealPerson, PersonDayLog
from dieta.settings import Settings
from dieta.store import Store

WEEK = date(2026, 11, 23)  # poniedziałek
TODAY = date(2026, 11, 25)  # środa
LABELS = {1: "Ala", 2: "Olek"}


def _day_log(message_meals: tuple[MealLog, ...] | None = None) -> DayLog:
    meals = (
        message_meals
        if message_meals is not None
        else (
            MealLog(
                "lunch",
                "Obiad",
                "Owsianka",
                1,
                1,
                (MealPerson(1.4, 520.0, None, None, None), MealPerson(1.0, 370.0, None, None, None)),
            ),
        )
    )
    people = (
        PersonDayLog("Ala", 2000.0, 0.0, 0.0, 0.0, 0.0, 0),
        PersonDayLog("Olek", 2400.0, 0.0, 0.0, 0.0, 0.0, 0),
    )
    return DayLog(TODAY, True, "approved", people, meals)


def _settings(tmp_path: Path, push_notify: str = "family") -> Settings:
    return Settings(
        data_dir=tmp_path,
        log_level="info",
        dev=True,
        person_1_name="Ala",
        person_2_name="Olek",
        push_notify=push_notify,
        push_time="07:00",
    )


def _store(tmp_path: Path, *, approve: bool = True) -> tuple[Store, Settings]:
    settings = _settings(tmp_path)
    store = Store(settings.db_path)
    _demo(store)
    svc.generate(store, settings, WEEK, seed=1)
    if approve:
        svc.approve(store, WEEK)
    return store, settings


def test_compose_formats_message() -> None:
    title, message = push.compose(_day_log(), {1: 2, 2: 1}, LABELS)
    assert title == "Plan na dziś"
    assert "Obiad: Owsianka — Ala 1,4 (520 kcal), Olek 1,0 (370 kcal)" in message
    assert "Cel: Ala 2000 kcal, Olek 2400 kcal" in message
    assert "Wczoraj bez odpowiedzi: Ala 2, Olek 1" in message


def test_compose_skips_missing_person_and_zero_unanswered() -> None:
    meals = (
        MealLog(
            "dinner",
            "Kolacja",
            "Zupa",
            2,
            1,
            (MealPerson(1.0, 300.0, None, None, None), None),
        ),
    )
    day = DayLog(
        TODAY,
        True,
        "approved",
        (PersonDayLog("Ala", 2000.0, 0.0, 0.0, 0.0, 0.0, 0), None),
        meals,
    )
    _, message = push.compose(day, {1: 0, 2: 0}, LABELS)
    assert "Kolacja: Zupa — Ala 1,0 (300 kcal)" in message
    assert "Olek" not in message
    assert "Wczoraj" not in message


def test_compose_truncates_long_message() -> None:
    long_title = "Z" * 1500
    meals = (MealLog("lunch", "Obiad", long_title, 1, 1, (MealPerson(1.0, 500.0, None, None, None), None)),)
    _, message = push.compose(_day_log(meals), {1: 0, 2: 0}, LABELS)
    assert len(message) == push.MAX_CHARS
    assert message.endswith("…")


def test_run_once_sends_once_per_day(tmp_path: Path) -> None:
    store, settings = _store(tmp_path)
    calls: list[tuple[str, str, str, str]] = []

    def sender(service: str, title: str, message: str, path: str) -> bool:
        calls.append((service, title, message, path))
        return True

    assert push.run_once(settings, store, TODAY, sender=sender) == "sent"
    assert push.run_once(settings, store, TODAY, sender=sender) == "already_sent"
    assert len(calls) == 1
    service, title, message, path = calls[0]
    assert service == "family"
    assert title == "Plan na dziś"
    assert "Cel:" in message
    assert path.startswith("/app/")
    assert store.get_meta("push_last") == TODAY.isoformat()


def test_run_once_failed_does_not_record(tmp_path: Path) -> None:
    store, settings = _store(tmp_path)
    assert push.run_once(settings, store, TODAY, sender=lambda *_: False) == "failed"
    assert store.get_meta("push_last") is None
    assert push.run_once(settings, store, TODAY, sender=lambda *_: True) == "sent"


def test_run_once_disabled_without_service(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    settings = _settings(tmp_path, push_notify="")

    def sender(*_: str) -> bool:
        raise AssertionError("nie powinno wysyłać")

    assert push.run_once(settings, store, TODAY, sender=sender) == "disabled"


def test_run_once_no_plan_for_draft_or_missing_week(tmp_path: Path) -> None:
    store, settings = _store(tmp_path, approve=False)

    def sender(*_: str) -> bool:
        raise AssertionError("nie powinno wysyłać")

    assert push.run_once(settings, store, TODAY, sender=sender) == "no_plan"
    assert push.run_once(settings, store, date(2026, 12, 7), sender=sender) == "no_plan"


class _Response:
    status = 200

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_send_without_token_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("brak sieci w trybie dev")

    monkeypatch.setattr(push.urllib.request, "urlopen", fail)
    assert push.send("family", "t", "m", "/app/x") is True


def test_send_rejects_bad_service_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")

    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("nie powinno wysyłać")

    monkeypatch.setattr(push.urllib.request, "urlopen", fail)
    assert push.send("Family!", "t", "m", "/app/x") is False


def test_send_posts_json_with_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok-123")
    seen: dict[str, Any] = {}

    def fake(request: Any, timeout: float) -> _Response:
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(push.urllib.request, "urlopen", fake)
    assert push.send("family", "Plan na dziś", "linia", "/app/b969c720_dieta") is True
    assert seen["url"] == "http://supervisor/core/api/services/notify/family"
    assert seen["auth"] == "Bearer tok-123"
    assert seen["timeout"] == 15
    assert seen["body"]["title"] == "Plan na dziś"
    assert seen["body"]["message"] == "linia"
    assert seen["body"]["data"]["tag"] == "dieta-plan-dnia"
    assert seen["body"]["data"]["clickAction"] == "/app/b969c720_dieta"


def test_send_http_error_returns_false_without_secrets(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "SECRET-TOKEN-XYZ")
    caplog.set_level(logging.INFO)

    def fake(request: Any, timeout: float) -> None:
        raise urllib.error.HTTPError(request.full_url, 500, "boom", None, None)

    monkeypatch.setattr(push.urllib.request, "urlopen", fake)
    assert push.send("family", "Tytuł-sekretny", "Treść-sekretna", "/app/x") is False
    assert "HTTP 500" in caplog.text
    assert "SECRET-TOKEN-XYZ" not in caplog.text
    assert "Treść-sekretna" not in caplog.text
    assert "Tytuł-sekretny" not in caplog.text


def test_start_returns_none_when_disabled(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert push.start(_settings(tmp_path, push_notify=""), store) is None


def test_start_creates_task_and_cancels(tmp_path: Path) -> None:
    store, settings = _store(tmp_path)

    async def scenario() -> bool:
        task = push.start(settings, store)
        assert task is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    assert asyncio.run(scenario()) is True
