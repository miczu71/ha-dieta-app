import logging
from datetime import date
from pathlib import Path

import pytest

from dieta.pipeline import run_catalog
from dieta.settings import Settings
from dieta.store import Store


def test_run_catalog_logs_phases(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = Store(tmp_path / "d.db")
    settings = Settings(data_dir=tmp_path, log_level="INFO", dev=True, sources=("policzona",))
    caplog.set_level(logging.INFO, logger="dieta.pipeline")

    def fetch(url: str) -> str:
        raise RuntimeError("offline")

    run_catalog(
        store,
        settings,
        source="policzona",
        fetch=fetch,
        ask_ingredients=None,
        ask_meta=None,
        today=date(2026, 11, 22),
    )
    text = caplog.text
    assert "start crawla policzona" in text
    assert "koniec crawla policzona" in text
    assert "mapowanie składników" in text
    assert "metadane AI" in text


def test_known_sources_have_factory_and_label() -> None:
    from dieta.pipeline import SOURCE_FACTORIES, SOURCE_LABELS
    from dieta.settings import KNOWN_SOURCES

    assert set(KNOWN_SOURCES) == set(SOURCE_FACTORIES) == set(SOURCE_LABELS)
