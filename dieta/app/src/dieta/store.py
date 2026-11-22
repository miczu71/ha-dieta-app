"""Lokalny magazyn SQLite: profile osób i historia wagi.

Połączenie otwierane na każdą operację (krótkie, bez współdzielenia między wątkami FastAPI).
Schemat wersjonowany przez ``PRAGMA user_version``; kolejne migracje dopisujemy na końcu listy.
"""

import json
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from dieta.dictionary import Durability, normalize_name
from dieta.zakupy_client import Purchase

PERSON_IDS = (1, 2)
MappingStatus = Literal["rule", "seed", "ai", "pending", "approved", "excluded"]

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
    """
    CREATE TABLE purchase (
        code TEXT PRIMARY KEY,
        chain TEXT NOT NULL,
        name TEXT NOT NULL,
        names_json TEXT NOT NULL,
        purchases INTEGER NOT NULL,
        spend REAL NOT NULL,
        first TEXT NOT NULL,
        last TEXT NOT NULL,
        dates_json TEXT NOT NULL,
        category TEXT NOT NULL,
        fetched_at TEXT NOT NULL
    );
    CREATE TABLE ingredient (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        durability TEXT CHECK(durability IN ('fresh','chilled','dry','frozen'))
    );
    CREATE TABLE mapping (
        code TEXT PRIMARY KEY,
        ingredient_id INTEGER REFERENCES ingredient(id),
        status TEXT NOT NULL CHECK(status IN ('rule','seed','ai','pending','approved','excluded')),
        source_name TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
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


@dataclass(frozen=True)
class MappingRow:
    code: str
    name: str | None
    status: MappingStatus
    ingredient: str | None
    durability: Durability | None
    source_name: str


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

    # --- Zakupy: snapshot, słownik, mapowanie, meta (migracja v2) ---

    def replace_purchases(self, purchases: Iterable[Purchase], fetched_at: str | None = None) -> None:
        """Zastępuje cały snapshot zakupów w jednej transakcji. Mapowania nie są ruszane."""
        stamp = fetched_at or datetime.now().isoformat(timespec="seconds")
        rows = [
            (
                p.code,
                p.chain,
                p.name,
                json.dumps(list(p.names), ensure_ascii=False),
                p.purchases,
                p.spend,
                p.first.isoformat(),
                p.last.isoformat(),
                json.dumps([d.isoformat() for d in p.dates]),
                p.category,
                stamp,
            )
            for p in purchases
        ]
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM purchase")
            conn.executemany("INSERT INTO purchase VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)

    def purchases(self) -> list[Purchase]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT code, chain, name, names_json, purchases, spend, first, last, dates_json, category "
                "FROM purchase ORDER BY code"
            ).fetchall()
        return [
            Purchase(
                code=code,
                chain=chain,
                name=name,
                names=tuple(json.loads(names_json)),
                purchases=int(purchases_n),
                spend=float(spend),
                first=date.fromisoformat(first),
                last=date.fromisoformat(last),
                dates=tuple(date.fromisoformat(d) for d in json.loads(dates_json)),
                category=category,
            )
            for code, chain, name, names_json, purchases_n, spend, first, last, dates_json, category in rows
        ]

    def mapped_codes(self) -> set[str]:
        with closing(self._connect()) as conn:
            return {row[0] for row in conn.execute("SELECT code FROM mapping").fetchall()}

    def ingredient_durability(self, name: str) -> Durability | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT durability FROM ingredient WHERE name = ?", (name,)).fetchone()
        return None if row is None or row[0] is None else cast(Durability, row[0])

    def upsert_ingredient(self, name: str, durability: Durability | None) -> int:
        """Dodaje składnik; ``durability=None`` zostawia istniejącą trwałość."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ingredient (name, durability) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "durability = COALESCE(excluded.durability, ingredient.durability)",
                (name, durability),
            )
            row = conn.execute("SELECT id FROM ingredient WHERE name = ?", (name,)).fetchone()
        return int(row[0])

    def set_mapping(
        self,
        code: str,
        status: MappingStatus,
        ingredient_name: str | None,
        durability: Durability | None,
        source_name: str,
    ) -> None:
        ingredient_id = (
            self.upsert_ingredient(normalize_name(ingredient_name), durability) if ingredient_name else None
        )
        sql = (
            "INSERT INTO mapping (code, ingredient_id, status, source_name) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET ingredient_id = excluded.ingredient_id, "
            "status = excluded.status, source_name = excluded.source_name"
        )
        with closing(self._connect()) as conn, conn:
            conn.execute(sql, (code, ingredient_id, status, source_name))

    def mappings(self) -> list[MappingRow]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT m.code, p.name, m.status, i.name, i.durability, m.source_name "
                "FROM mapping m "
                "LEFT JOIN purchase p ON p.code = m.code "
                "LEFT JOIN ingredient i ON i.id = m.ingredient_id "
                "ORDER BY m.code"
            ).fetchall()
        return [
            MappingRow(
                code=code,
                name=name,
                status=cast(MappingStatus, status),
                ingredient=ingredient,
                durability=None if durability is None else cast(Durability, durability),
                source_name=source_name,
            )
            for code, name, status, ingredient, durability, source_name in rows
        ]

    def pending(self) -> list[MappingRow]:
        """Kolejka zatwierdzania: wiersze ze statusem pending, których produkt jest w snapshocie."""
        return [r for r in self.mappings() if r.status == "pending" and r.name is not None]

    def _source_of(self, code: str) -> str:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT source_name FROM mapping WHERE code = ?", (code,)).fetchone()
        return "" if row is None else str(row[0])

    def approve(self, code: str, ingredient_name: str, durability: Durability) -> None:
        self.set_mapping(code, "approved", ingredient_name, durability, self._source_of(code))

    def exclude(self, code: str) -> None:
        self.set_mapping(code, "excluded", None, None, self._source_of(code))

    def get_meta(self, key: str) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
