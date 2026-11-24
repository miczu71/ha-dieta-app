"""Ustawienia add-onu: opcje z /data/options.json (Supervisor) + zmienne środowiskowe dev."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dieta.dictionary import Durability

DEFAULT_REFRESH_TIME = "03:30"
DEFAULT_PUSH_TIME = "07:00"
DEFAULT_FAMILIAR_THRESHOLD = 50
KNOWN_SOURCES = ("respo", "lidl", "policzona", "aniagotuje")
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DEFAULT_COOK_DAYS = ("sat", "sun", "wed")
DEFAULT_SLOT_PCT = {
    "slot_breakfast_pct": 25,
    "slot_lunch_pct": 35,
    "slot_dinner_pct": 30,
    "slot_snack1_pct": 0,
    "slot_snack2_pct": 0,
}
log = logging.getLogger(__name__)
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
    push_notify: str = ""
    push_time: str = DEFAULT_PUSH_TIME
    shelf_fresh_days: int = 4
    shelf_chilled_days: int = 10
    shelf_dry_days: int = 30
    shelf_frozen_days: int = 60
    crawl_daily_limit: int = 150
    familiar_threshold: int = DEFAULT_FAMILIAR_THRESHOLD
    sources: tuple[str, ...] = KNOWN_SOURCES
    slot_breakfast_pct: int = 25
    slot_lunch_pct: int = 35
    slot_dinner_pct: int = 30
    slot_snack1_pct: int = 0
    slot_snack2_pct: int = 0
    cook_days: tuple[str, ...] = DEFAULT_COOK_DAYS
    max_minutes_weekday: int = 45
    session_days_max: int = 4
    discovery: int = 1
    todo_entity: str = ""
    cheaper_mode: bool = False
    zepp_enabled: bool = True

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


def _clock(value: object, default: str = DEFAULT_REFRESH_TIME) -> str:
    text = str(value or "").strip()
    return text if _REFRESH_RE.match(text) else default


def _bounded(options: dict[str, object], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(options.get(key, default)))
    except ValueError:
        return default
    return value if low <= value <= high else default


def _sources(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return KNOWN_SOURCES
    return tuple(dict.fromkeys(str(v) for v in value if str(v) in KNOWN_SOURCES))


def _cook_days(value: object) -> tuple[str, ...]:
    """Poprawne dni z listy (kolejność tygodnia, bez duplikatów); brak poprawnych → domyślne."""
    if not isinstance(value, list):
        return DEFAULT_COOK_DAYS
    chosen = {str(v).strip().lower() for v in value}
    days = tuple(d for d in WEEKDAYS if d in chosen)
    return days or DEFAULT_COOK_DAYS


def _slot_pcts(options: dict[str, object]) -> dict[str, int]:
    pcts = {key: _bounded(options, key, default, 0, 100) for key, default in DEFAULT_SLOT_PCT.items()}
    if sum(pcts.values()) > 100:
        log.warning("suma udziałów slotów > 100%% — używam domyślnych (%s)", DEFAULT_SLOT_PCT)
        return dict(DEFAULT_SLOT_PCT)
    return pcts


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
        push_notify=str(options.get("push_notify") or "").strip(),
        push_time=_clock(options.get("push_time"), DEFAULT_PUSH_TIME),
        shelf_fresh_days=_bounded(options, "shelf_fresh_days", 4, 1, 365),
        shelf_chilled_days=_bounded(options, "shelf_chilled_days", 10, 1, 365),
        shelf_dry_days=_bounded(options, "shelf_dry_days", 30, 1, 365),
        shelf_frozen_days=_bounded(options, "shelf_frozen_days", 60, 1, 365),
        crawl_daily_limit=_bounded(options, "crawl_daily_limit", 150, 1, 1000),
        familiar_threshold=_bounded(options, "familiar_threshold", DEFAULT_FAMILIAR_THRESHOLD, 0, 100),
        sources=_sources(options.get("sources")),
        **_slot_pcts(options),
        cook_days=_cook_days(options.get("cook_days")),
        max_minutes_weekday=_bounded(options, "max_minutes_weekday", 45, 10, 240),
        session_days_max=_bounded(options, "session_days_max", 4, 1, 7),
        discovery=_bounded(options, "discovery", 1, 0, 3),
        todo_entity=str(options.get("todo_entity") or "").strip(),
        cheaper_mode=options.get("cheaper_mode") is True,
        zepp_enabled=options.get("zepp_enabled") is not False,
    )
