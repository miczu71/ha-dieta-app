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
