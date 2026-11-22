"""Lokalny magazyn SQLite: profile osób i historia wagi.

Połączenie otwierane na każdą operację (krótkie, bez współdzielenia między wątkami FastAPI).
Schemat wersjonowany przez ``PRAGMA user_version``; kolejne migracje dopisujemy na końcu listy.
"""

import json
import sqlite3
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

from dieta.dictionary import Durability, normalize_name
from dieta.match import Familiarity, familiarity
from dieta.profile import IngredientStat
from dieta.sources.base import Recipe
from dieta.zakupy_client import Purchase

PERSON_IDS = (1, 2)
MappingStatus = Literal["rule", "seed", "ai", "pending", "approved", "excluded"]
MetaStatus = Literal["rule", "ai", "pending", "approved"]
RecipeFlag = Literal["liked", "dismissed"]
MEAL_CATEGORIES = ("breakfast", "lunch", "dinner", "snack", "dessert")

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
    """
    CREATE TABLE recipe (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        source_id TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        image TEXT,
        servings REAL,
        kcal REAL,
        protein REAL,
        carbs REAL,
        fat REAL,
        minutes INTEGER,
        tags_json TEXT NOT NULL,
        meal_category TEXT CHECK(meal_category IN ('breakfast','lunch','dinner','snack','dessert')),
        storage_days INTEGER,
        freezable INTEGER,
        has_nutrition INTEGER NOT NULL,
        meta_status TEXT NOT NULL CHECK(meta_status IN ('rule','ai','pending','approved')),
        fetched_at TEXT NOT NULL,
        UNIQUE(source, source_id)
    );
    CREATE TABLE recipe_ingredient (
        recipe_id INTEGER NOT NULL REFERENCES recipe(id),
        pos INTEGER NOT NULL,
        text TEXT NOT NULL,
        amount REAL,
        unit TEXT,
        name TEXT NOT NULL,
        ingredient_id INTEGER REFERENCES ingredient(id),
        PRIMARY KEY (recipe_id, pos)
    );
    CREATE TABLE recipe_flag (
        recipe_id INTEGER PRIMARY KEY REFERENCES recipe(id),
        liked INTEGER NOT NULL DEFAULT 0,
        dismissed INTEGER NOT NULL DEFAULT 0
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


@dataclass(frozen=True)
class RecipeMeta:
    """Metadane planera przepisu; ``None`` = brak (do uzupełnienia przez AI albo ręcznie)."""

    category: str | None
    storage_days: int | None
    freezable: bool | None
    status: MetaStatus


@dataclass(frozen=True)
class RecipeRow:
    id: int
    source: str
    url: str
    title: str
    image: str | None
    servings: float | None
    kcal: float | None
    protein: float | None
    carbs: float | None
    fat: float | None
    minutes: int | None
    tags: tuple[str, ...]
    meal_category: str | None
    storage_days: int | None
    freezable: bool | None
    has_nutrition: bool
    meta_status: MetaStatus
    liked: bool
    dismissed: bool
    familiarity: Familiarity


@dataclass(frozen=True)
class PendingRecipeMeta:
    id: int
    title: str
    meal_category: str | None
    storage_days: int | None
    freezable: bool | None


_RECIPE_COLUMNS = (
    "r.id, r.source, r.url, r.title, r.image, r.servings, r.kcal, r.protein, r.carbs, r.fat, r.minutes, "
    "r.tags_json, r.meal_category, r.storage_days, r.freezable, r.has_nutrition, r.meta_status, "
    "COALESCE(f.liked, 0), COALESCE(f.dismissed, 0)"
)


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

    # --- Katalog przepisów (migracja v3) ---

    def recipe_source_ids(self, source: str) -> set[str]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT source_id FROM recipe WHERE source = ?", (source,)).fetchall()
        return {row[0] for row in rows}

    def upsert_recipe(self, recipe: Recipe, meta: RecipeMeta, fetched_at: str | None = None) -> int:
        """Dodaje albo odświeża przepis; metadane ze statusem ``approved`` nigdy nie są nadpisywane."""
        stamp = fetched_at or datetime.now().isoformat(timespec="seconds")
        values = (
            recipe.title,
            recipe.url,
            recipe.image,
            recipe.servings,
            recipe.kcal,
            recipe.protein,
            recipe.carbs,
            recipe.fat,
            recipe.minutes,
            json.dumps(list(recipe.tags), ensure_ascii=False),
            int(recipe.kcal is not None),
            stamp,
        )
        freezable = None if meta.freezable is None else int(meta.freezable)
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT id, meta_status FROM recipe WHERE source = ? AND source_id = ?",
                (recipe.source, recipe.source_id),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO recipe (title, url, image, servings, kcal, protein, carbs, fat, minutes, "
                    "tags_json, has_nutrition, fetched_at, source, source_id, meal_category, storage_days, "
                    "freezable, meta_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *values,
                        recipe.source,
                        recipe.source_id,
                        meta.category,
                        meta.storage_days,
                        freezable,
                        meta.status,
                    ),
                )
                recipe_id = int(cur.lastrowid or 0)
            else:
                recipe_id = int(row[0])
                conn.execute(
                    "UPDATE recipe SET title=?, url=?, image=?, servings=?, kcal=?, protein=?, carbs=?, "
                    "fat=?, minutes=?, tags_json=?, has_nutrition=?, fetched_at=? WHERE id=?",
                    (*values, recipe_id),
                )
                if row[1] != "approved":
                    conn.execute(
                        "UPDATE recipe SET meal_category=?, storage_days=?, freezable=?, meta_status=? "
                        "WHERE id=?",
                        (meta.category, meta.storage_days, freezable, meta.status, recipe_id),
                    )
                conn.execute("DELETE FROM recipe_ingredient WHERE recipe_id = ?", (recipe_id,))
            conn.executemany(
                "INSERT INTO recipe_ingredient (recipe_id, pos, text, amount, unit, name) "
                "VALUES (?,?,?,?,?,?)",
                [
                    (recipe_id, pos, ing.text, ing.amount, ing.unit, ing.name)
                    for pos, ing in enumerate(recipe.ingredients)
                ],
            )
        return recipe_id

    def unlinked_ingredient_names(self) -> list[str]:
        """Różne nazwy składników przepisów, które nie mają jeszcze powiązania ze słownikiem."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT name FROM recipe_ingredient WHERE ingredient_id IS NULL ORDER BY name"
            ).fetchall()
        return [row[0] for row in rows]

    def link_recipe_ingredient(
        self, raw_name: str, ingredient_name: str, durability: Durability | None
    ) -> None:
        ingredient_id = self.upsert_ingredient(normalize_name(ingredient_name), durability)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE recipe_ingredient SET ingredient_id = ? WHERE name = ? AND ingredient_id IS NULL",
                (ingredient_id, raw_name),
            )

    def recipe_ingredient_names(self, recipe_id: int) -> list[tuple[str, str | None]]:
        """(tekst składnika, nazwa ze słownika albo None) w kolejności przepisu."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ri.text, i.name FROM recipe_ingredient ri "
                "LEFT JOIN ingredient i ON i.id = ri.ingredient_id WHERE ri.recipe_id = ? ORDER BY ri.pos",
                (recipe_id,),
            ).fetchall()
        return [(text, name) for text, name in rows]

    def _recipe_rows(
        self,
        where: str,
        params: tuple[object, ...],
        stats: Mapping[str, IngredientStat],
        basics: frozenset[str],
    ) -> list[RecipeRow]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_RECIPE_COLUMNS} FROM recipe r LEFT JOIN recipe_flag f ON f.recipe_id = r.id "
                f"{where}",
                params,
            ).fetchall()
            ing_rows = conn.execute(
                "SELECT ri.recipe_id, i.name FROM recipe_ingredient ri "
                "LEFT JOIN ingredient i ON i.id = ri.ingredient_id ORDER BY ri.recipe_id, ri.pos"
            ).fetchall()
        by_recipe: dict[int, list[str | None]] = {}
        for recipe_id, name in ing_rows:
            by_recipe.setdefault(recipe_id, []).append(name)
        result: list[RecipeRow] = []
        for r in rows:
            result.append(
                RecipeRow(
                    id=r[0],
                    source=r[1],
                    url=r[2],
                    title=r[3],
                    image=r[4],
                    servings=r[5],
                    kcal=r[6],
                    protein=r[7],
                    carbs=r[8],
                    fat=r[9],
                    minutes=r[10],
                    tags=tuple(json.loads(r[11])),
                    meal_category=r[12],
                    storage_days=r[13],
                    freezable=None if r[14] is None else bool(r[14]),
                    has_nutrition=bool(r[15]),
                    meta_status=cast(MetaStatus, r[16]),
                    liked=bool(r[17]),
                    dismissed=bool(r[18]),
                    familiarity=familiarity(by_recipe.get(r[0], []), stats, basics=basics),
                )
            )
        return result

    def recipe(
        self, recipe_id: int, stats: Mapping[str, IngredientStat], basics: frozenset[str]
    ) -> RecipeRow | None:
        rows = self._recipe_rows("WHERE r.id = ?", (recipe_id,), stats, basics)
        return rows[0] if rows else None

    def recipes(
        self,
        stats: Mapping[str, IngredientStat],
        basics: frozenset[str],
        *,
        min_familiarity: float = 0.0,
        category: str | None = None,
        source: str | None = None,
        liked_only: bool = False,
        show_dismissed: bool = False,
    ) -> list[RecipeRow]:
        """Katalog posortowany po znajomości (malejąco), potem po tytule; ``min_familiarity`` w 0..1."""
        clauses: list[str] = []
        params: list[object] = []
        if category is not None:
            clauses.append("r.meal_category = ?")
            params.append(category)
        if source is not None:
            clauses.append("r.source = ?")
            params.append(source)
        if liked_only:
            clauses.append("COALESCE(f.liked, 0) = 1")
        if not show_dismissed:
            clauses.append("COALESCE(f.dismissed, 0) = 0")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._recipe_rows(where, tuple(params), stats, basics)
        rows = [r for r in rows if r.familiarity.ratio >= min_familiarity]
        rows.sort(key=lambda r: (-r.familiarity.ratio, -r.familiarity.known, r.title.lower()))
        return rows

    def set_flag(self, recipe_id: int, flag: RecipeFlag, value: bool = True) -> None:
        if flag not in ("liked", "dismissed"):
            raise ValueError(f"unknown flag: {flag}")
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO recipe_flag (recipe_id) VALUES (?)", (recipe_id,))
            conn.execute(f"UPDATE recipe_flag SET {flag} = ? WHERE recipe_id = ?", (int(value), recipe_id))

    def pending_recipe_meta(self) -> list[PendingRecipeMeta]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, title, meal_category, storage_days, freezable FROM recipe "
                "WHERE meta_status = 'pending' ORDER BY id"
            ).fetchall()
        return [
            PendingRecipeMeta(id, title, category, days, None if frz is None else bool(frz))
            for id, title, category, days, frz in rows
        ]

    def set_recipe_meta(self, recipe_id: int, meta: RecipeMeta) -> None:
        """Zapis metadanych; nie rusza przepisów ``approved``."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE recipe SET meal_category=?, storage_days=?, freezable=?, meta_status=? "
                "WHERE id = ? AND meta_status != 'approved'",
                (
                    meta.category,
                    meta.storage_days,
                    None if meta.freezable is None else int(meta.freezable),
                    meta.status,
                    recipe_id,
                ),
            )

    def approve_recipe_meta(self, recipe_id: int, category: str, storage_days: int, freezable: bool) -> None:
        if category not in MEAL_CATEGORIES:
            raise ValueError(f"unknown meal category: {category}")
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE recipe SET meal_category=?, storage_days=?, freezable=?, meta_status='approved' "
                "WHERE id = ?",
                (category, storage_days, int(freezable), recipe_id),
            )
