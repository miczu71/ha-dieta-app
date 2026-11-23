import json
from pathlib import Path

import pytest

from dieta.settings import Settings, load_settings


def test_tokens_are_hidden_in_repr(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path, log_level="info", dev=True, zakupy_token="TOK-XYZ", llm_key="KEY-XYZ"
    )
    text = repr(settings)
    assert "TOK-XYZ" not in text
    assert "KEY-XYZ" not in text


def test_load_settings_reads_zakupy_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps(
            {
                "zakupy_url": " http://f0987e0f-lidl:8099 ",
                "zakupy_token": "t",
                "refresh_time": "25:99",
                "shelf_dry_days": 0,
                "shelf_frozen_days": 90,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(options))
    monkeypatch.setenv("DIETA_DATA_DIR", str(tmp_path))
    settings = load_settings()
    assert settings.zakupy_url == "http://f0987e0f-lidl:8099"
    assert settings.zakupy_token == "t"
    assert settings.refresh_time == "03:30"
    assert settings.shelf_dry_days == 30
    assert settings.shelf_days == {"fresh": 4, "chilled": 10, "dry": 30, "frozen": 90}


def test_defaults_threshold_50_and_four_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(tmp_path / "brak.json"))
    monkeypatch.setenv("DIETA_DATA_DIR", str(tmp_path))
    settings = load_settings()
    assert settings.familiar_threshold == 50
    assert settings.sources == ("respo", "lidl", "policzona", "aniagotuje")


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, options: dict[str, object]) -> Settings:
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options), encoding="utf-8")
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(path))
    monkeypatch.setenv("DIETA_DATA_DIR", str(tmp_path))
    return load_settings()


def test_planner_option_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _load(tmp_path, monkeypatch, {})
    assert (s.slot_breakfast_pct, s.slot_lunch_pct, s.slot_dinner_pct) == (25, 35, 30)
    assert (s.slot_snack1_pct, s.slot_snack2_pct) == (0, 0)
    assert s.cook_days == ("sat", "sun", "wed")
    assert (s.max_minutes_weekday, s.session_days_max, s.discovery) == (45, 4, 1)


def test_planner_options_are_read_and_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _load(
        tmp_path,
        monkeypatch,
        {
            "slot_breakfast_pct": 20,
            "slot_snack1_pct": 10,
            "max_minutes_weekday": 5,  # poza 10-240 → domyślne
            "session_days_max": 7,
            "discovery": 9,  # poza 0-3 → domyślne
        },
    )
    assert s.slot_breakfast_pct == 20 and s.slot_snack1_pct == 10
    assert s.max_minutes_weekday == 45 and s.session_days_max == 7 and s.discovery == 1


def test_slot_sum_over_100_falls_back_to_defaults_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING", logger="dieta.settings"):
        s = _load(
            tmp_path, monkeypatch, {"slot_breakfast_pct": 50, "slot_lunch_pct": 50, "slot_dinner_pct": 50}
        )
    assert (s.slot_breakfast_pct, s.slot_lunch_pct, s.slot_dinner_pct) == (25, 35, 30)
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_slot_sum_exactly_100_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _load(tmp_path, monkeypatch, {"slot_breakfast_pct": 30, "slot_lunch_pct": 40, "slot_dinner_pct": 30})
    assert (s.slot_breakfast_pct, s.slot_lunch_pct, s.slot_dinner_pct) == (30, 40, 30)


def test_cook_days_skips_bad_day_and_orders_by_week(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _load(tmp_path, monkeypatch, {"cook_days": ["sun", "xyz", "mon", "sun"]})
    assert s.cook_days == ("mon", "sun")


def test_cook_days_without_valid_entry_uses_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _load(tmp_path, monkeypatch, {"cook_days": ["x"]}).cook_days == ("sat", "sun", "wed")
    assert _load(tmp_path, monkeypatch, {"cook_days": "sat"}).cook_days == ("sat", "sun", "wed")


def test_push_options_default_and_from_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(tmp_path / "missing.json"))
    default = load_settings()
    assert default.push_notify == ""
    assert default.push_time == "07:00"

    options = tmp_path / "options.json"
    options.write_text(json.dumps({"push_notify": " family ", "push_time": "06:45"}), encoding="utf-8")
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(options))
    loaded = load_settings()
    assert loaded.push_notify == "family"
    assert loaded.push_time == "06:45"


def test_bad_push_time_falls_back_without_touching_refresh_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"push_time": "25:99", "refresh_time": "nope"}), encoding="utf-8")
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(options))
    loaded = load_settings()
    assert loaded.push_time == "07:00"
    assert loaded.refresh_time == "03:30"


def test_e6_option_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _load(tmp_path, monkeypatch, {})
    assert s.todo_entity == "" and s.cheaper_mode is False


def test_e6_options_are_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _load(tmp_path, monkeypatch, {"todo_entity": " todo.zakupy ", "cheaper_mode": True})
    assert s.todo_entity == "todo.zakupy" and s.cheaper_mode is True
