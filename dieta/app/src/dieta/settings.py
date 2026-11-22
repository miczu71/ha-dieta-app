"""Ustawienia add-onu: opcje z /data/options.json (Supervisor) + zmienne środowiskowe dev."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    log_level: str
    dev: bool
    person_1_name: str = ""
    person_2_name: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dieta.db"

    def person_name(self, person_id: int) -> str:
        name = self.person_1_name if person_id == 1 else self.person_2_name
        return name or f"Osoba {person_id}"


def load_settings() -> Settings:
    options_path = Path(os.environ.get("DIETA_OPTIONS_PATH", "/data/options.json"))
    options: dict[str, object] = {}
    try:
        options = json.loads(options_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        options = {}
    return Settings(
        data_dir=Path(os.environ.get("DIETA_DATA_DIR", "/data")),
        log_level=str(options.get("log_level") or "info"),
        dev=os.environ.get("DIETA_DEV") == "1",
        person_1_name=str(options.get("person_1_name") or "").strip(),
        person_2_name=str(options.get("person_2_name") or "").strip(),
    )
