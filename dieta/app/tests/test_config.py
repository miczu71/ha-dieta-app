import json
from pathlib import Path

import pytest
import yaml

from dieta.settings import Settings, load_settings

ADDON = Path(__file__).resolve().parents[2]


@pytest.fixture
def config() -> dict[str, object]:
    return yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_options_schema_and_translations_agree(config: dict[str, object]) -> None:
    options = set(config["options"])  # type: ignore[call-overload]
    assert options == set(config["schema"])  # type: ignore[call-overload]
    for lang in ("pl", "en"):
        tr = yaml.safe_load((ADDON / "translations" / f"{lang}.yaml").read_text(encoding="utf-8"))
        assert set(tr["configuration"]) == options, lang


def test_config_defaults_match_settings(
    config: dict[str, object], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = tmp_path / "options.json"
    options.write_text(json.dumps(config["options"]), encoding="utf-8")
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(options))
    monkeypatch.setenv("DIETA_DATA_DIR", str(tmp_path))
    loaded = load_settings()
    defaults = Settings(data_dir=tmp_path, log_level="info", dev=False)
    for key, value in config["options"].items():  # type: ignore[attr-defined]
        assert hasattr(loaded, key), f"Settings nie czyta opcji {key}"
        if key == "sources":
            assert loaded.sources == tuple(value)
        elif isinstance(value, int):
            assert getattr(loaded, key) == value
            assert getattr(defaults, key) == value
    assert loaded.sources == defaults.sources


def test_new_options_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    options = tmp_path / "options.json"
    options.write_text(
        json.dumps({"crawl_daily_limit": 0, "familiar_threshold": 101, "sources": ["lidl", "x"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DIETA_OPTIONS_PATH", str(options))
    monkeypatch.setenv("DIETA_DATA_DIR", str(tmp_path))
    loaded = load_settings()
    assert (loaded.crawl_daily_limit, loaded.familiar_threshold) == (150, 70)
    assert loaded.sources == ("lidl",)
