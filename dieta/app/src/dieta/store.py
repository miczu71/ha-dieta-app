"""Lokalny magazyn SQLite: profile osób i historia wagi.

Połączenie otwierane na każdą operację (krótkie, bez współdzielenia między wątkami FastAPI).
Schemat wersjonowany przez ``PRAGMA user_version``; kolejne migracje dopisujemy na końcu listy.
"""

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, cast

PERSON_IDS = (1, 2)

_PROFILE_COLUMNS = (
    "person_id",
    "sex",
    "age",
    "height_cm",
    "pal_level",
    "goal",
    "pace_kg_week",
    "kcal_override",
    "protein_g_kg",
    "fat_pct",
)

# Migracja i-ta (od 1) podnosi user_version do i. Nie wolno edytować istniejących wpisów.
_MIGRATIONS: list[str] = [
    """
    CREATE TABLE profile (
        person_id INTEGER PRIMARY KEY CHECK(person_id IN (1,2)),
        sex TEXT NOT NULL,
        age INTEGER NOT NULL,
        height_cm REAL NOT NULL,
        pal_level INTEGER NOT NULL,
        goal TEXT NOT NULL,
        pace_kg_week REAL NOT NULL,
        kcal_override INTEGER,
        protein_g_kg REAL,
        fat_pct REAL NOT NULL
    );
    CREATE TABLE weight (
        person_id INTEGER NOT NULL,
        day TEXT NOT NULL,
        kg REAL NOT NULL,
        PRIMARY KEY (person_id, day)
    );
    """,
]


@dataclass(frozen=True)
class Profile:
    person_id: int
    sex: Literal["m", "f"]
    age: int
    height_cm: float
    pal_level: int
    goal: Literal["lose", "maintain", "gain"]
    pace_kg_week: float
    kcal_override: int | None
    protein_g_kg: float | None
    fat_pct: float


def _check_person(person_id: int) -> None:
    if person_id not in PERSON_IDS:
        raise ValueError(f"person_id must be one of {PERSON_IDS}, got {person_id}")


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(_MIGRATIONS, start=1):
        if version <= current:
            continue
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")


class Store:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            _migrate(conn)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def profile(self, person_id: int) -> Profile | None:
        _check_person(person_id)
        sql = f"SELECT {', '.join(_PROFILE_COLUMNS)} FROM profile WHERE person_id = ?"
        with closing(self._connect()) as conn:
            row = conn.execute(sql, (person_id,)).fetchone()
        if row is None:
            return None
        return Profile(
            person_id=row[0],
            sex=cast(Literal["m", "f"], row[1]),
            age=row[2],
            height_cm=row[3],
            pal_level=row[4],
            goal=cast(Literal["lose", "maintain", "gain"], row[5]),
            pace_kg_week=row[6],
            kcal_override=row[7],
            protein_g_kg=row[8],
            fat_pct=row[9],
        )

    def save_profile(self, profile: Profile) -> None:
        _check_person(profile.person_id)
        values = (
            profile.person_id,
            profile.sex,
            profile.age,
            profile.height_cm,
            profile.pal_level,
            profile.goal,
            profile.pace_kg_week,
            profile.kcal_override,
            profile.protein_g_kg,
            profile.fat_pct,
        )
        placeholders = ", ".join("?" * len(_PROFILE_COLUMNS))
        updates = ", ".join(f"{c} = excluded.{c}" for c in _PROFILE_COLUMNS[1:])
        sql = (
            f"INSERT INTO profile ({', '.join(_PROFILE_COLUMNS)}) VALUES ({placeholders}) "
            f"ON CONFLICT(person_id) DO UPDATE SET {updates}"
        )
        with closing(self._connect()) as conn, conn:
            conn.execute(sql, values)

    def add_weight(self, person_id: int, day: date, kg: float) -> None:
        _check_person(person_id)
        sql = (
            "INSERT INTO weight (person_id, day, kg) VALUES (?, ?, ?) "
            "ON CONFLICT(person_id, day) DO UPDATE SET kg = excluded.kg"
        )
        with closing(self._connect()) as conn, conn:
            conn.execute(sql, (person_id, day.isoformat(), kg))

    def weights(self, person_id: int, days: int = 90) -> list[tuple[date, float]]:
        """Wpisy z ostatnich ``days`` dni (włącznie), liczone wstecz od najnowszego wpisu osoby."""
        _check_person(person_id)
        with closing(self._connect()) as conn:
            latest: str | None = conn.execute(
                "SELECT MAX(day) FROM weight WHERE person_id = ?", (person_id,)
            ).fetchone()[0]
            if latest is None:
                return []
            cutoff = date.fromisoformat(latest) - timedelta(days=days - 1)
            rows = conn.execute(
                "SELECT day, kg FROM weight WHERE person_id = ? AND day >= ? ORDER BY day",
                (person_id, cutoff.isoformat()),
            ).fetchall()
        return [(date.fromisoformat(day), float(kg)) for day, kg in rows]

    def latest_weight(self, person_id: int) -> float | None:
        _check_person(person_id)
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT kg FROM weight WHERE person_id = ? ORDER BY day DESC LIMIT 1",
                (person_id,),
            ).fetchone()
        return None if row is None else float(row[0])
