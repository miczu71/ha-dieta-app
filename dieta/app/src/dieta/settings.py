"""Ustawienia add-onu: opcje z /data/options.json (Supervisor) + zmienne środowiskowe dev."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dieta.dictionary import Durability

DEFAULT_REFRESH_TIME = "03:30"
_REFRESH_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    log_level: str
    dev: bool
    person_1_name: str = ""
    person_2_name: str = ""
    zakupy_url: str = ""
    zakupy_token: str = field(default="", repr=False)
    llm_url: str = ""
    llm_key: str = field(default="", repr=False)
    refresh_time: str = DEFAULT_REFRESH_TIME
    shelf_fresh_days: int = 4
    shelf_chilled_days: int = 10
    shelf_dry_days: int = 30
    shelf_frozen_days: int = 60

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dieta.db"

    @property
    def shelf_days(self) -> dict[Durability, int]:
        return {
            "fresh": self.shelf_fresh_days,
            "chilled": self.shelf_chilled_days,
            "dry": self.shelf_dry_days,
            "frozen": self.shelf_frozen_days,
        }

    def person_name(self, person_id: int) -> str:
        name = self.person_1_name if person_id == 1 else self.person_2_name
        return name or f"Osoba {person_id}"


def _clock(value: object) -> str:
    text = str(value or "").strip()
    return text if _REFRESH_RE.match(text) else DEFAULT_REFRESH_TIME


def _days(options: dict[str, object], key: str, default: int) -> int:
    try:
        value = int(str(options.get(key, default)))
    except ValueError:
        return default
    return value if 1 <= value <= 365 else default


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
        zakupy_url=str(options.get("zakupy_url") or "").strip(),
        zakupy_token=str(options.get("zakupy_token") or "").strip(),
        llm_url=str(options.get("llm_url") or "").strip(),
        llm_key=str(options.get("llm_key") or "").strip(),
        refresh_time=_clock(options.get("refresh_time")),
        shelf_fresh_days=_days(options, "shelf_fresh_days", 4),
        shelf_chilled_days=_days(options, "shelf_chilled_days", 10),
        shelf_dry_days=_days(options, "shelf_dry_days", 30),
        shelf_frozen_days=_days(options, "shelf_frozen_days", 60),
    )
