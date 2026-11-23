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
EstStatus = Literal["auto", "pending", "approved"]
NutritionSource = Literal["table", "off", "llm", "manual"]
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
    CREATE INDEX idx_recipe_ingredient_name ON recipe_ingredient(name);
    CREATE INDEX idx_recipe_source ON recipe(source);
    CREATE TABLE recipe_flag (
        recipe_id INTEGER PRIMARY KEY REFERENCES recipe(id),
        liked INTEGER NOT NULL DEFAULT 0,
        dismissed INTEGER NOT NULL DEFAULT 0
    );
    """,
    # v4 (E3c): szacunek kcal ze składników. Tylko dodaje tabelę i kolumny (stary kod je ignoruje).
    """
    CREATE TABLE ingredient_nutrition (
        ingredient_id INTEGER PRIMARY KEY REFERENCES ingredient(id),
        kcal REAL,
        protein REAL,
        carbs REAL,
        fat REAL,
        piece_g REAL,
        source TEXT CHECK(source IN ('table','off','llm','manual')),
        confidence TEXT CHECK(confidence IN ('high','low')),
        status TEXT CHECK(status IN ('auto','pending','approved')),
        updated_at TEXT
    );
    ALTER TABLE recipe ADD COLUMN kcal_source TEXT CHECK(kcal_source IN ('source','estimate'));
    ALTER TABLE recipe ADD COLUMN kcal_100g REAL;
    ALTER TABLE recipe ADD COLUMN est_coverage REAL;
    ALTER TABLE recipe ADD COLUMN est_status TEXT CHECK(est_status IN ('auto','pending','approved'));
    ALTER TABLE recipe ADD COLUMN servings_estimated INTEGER;
    ALTER TABLE recipe ADD COLUMN est_unknown_json TEXT;
    ALTER TABLE recipe ADD COLUMN est_assumed INTEGER;
    ALTER TABLE recipe_ingredient ADD COLUMN grams_assumed REAL;
    UPDATE recipe SET kcal_source = 'source' WHERE has_nutrition = 1;
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
    kcal_source: Literal["source", "estimate"] | None = None
    kcal_100g: float | None = None
    est_coverage: float | None = None
    est_status: EstStatus | None = None
    servings_estimated: bool = False
    est_unknown: tuple[str, ...] = ()
    est_assumed: int = 0


@dataclass(frozen=True)
class IngredientNutrition:
    """Wartości na 100 g składnika słownika (E3c)."""

    ingredient_id: int
    name: str
    kcal: float | None
    protein: float | None
    carbs: float | None
    fat: float | None
    piece_g: float | None
    source: NutritionSource | None
    confidence: Literal["high", "low"] | None
    status: EstStatus | None
    updated_at: str | None


@dataclass(frozen=True)
class RecipeLine:
    """Linia przepisu do estymacji; ``ingredient_name`` = nazwa słownika (None, gdy niepowiązana)."""

    recipe_id: int
    pos: int
    text: str
    amount: float | None
    unit: str | None
    name: str
    ingredient_id: int | None
    ingredient_name: str | None
    grams_assumed: float | None
    recipe_title: str = ""


@dataclass(frozen=True)
class RecipeToEstimate:
    id: int
    title: str
    servings: float | None
    kcal_100g: float | None


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
    "COALESCE(f.liked, 0), COALESCE(f.dismissed, 0), "
    "r.kcal_source, r.kcal_100g, r.est_coverage, r.est_status, r.servings_estimated, r.est_unknown_json, "
    "r.est_assumed"
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

    def recipe_count(self, source: str) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM recipe WHERE source = ?", (source,)).fetchone()[0])

    def meta_keys(self, prefix: str) -> set[str]:
        """Wszystkie klucze ``meta`` zaczynające się od ``prefix`` (jedno zapytanie)."""
        pattern = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT key FROM meta WHERE key LIKE ? ESCAPE '\\'", (pattern,)).fetchall()
        return {row[0] for row in rows}

    def recipe_flags(self, recipe_id: int) -> tuple[bool, bool] | None:
        """(liked, dismissed) przepisu albo None, gdy przepis nie istnieje."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT COALESCE(f.liked, 0), COALESCE(f.dismissed, 0) FROM recipe r "
                "LEFT JOIN recipe_flag f ON f.recipe_id = r.id WHERE r.id = ?",
                (recipe_id,),
            ).fetchone()
        return None if row is None else (bool(row[0]), bool(row[1]))

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
        kcal_source = "source" if recipe.kcal is not None else None
        with closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT id, meta_status, est_status FROM recipe WHERE source = ? AND source_id = ?",
                (recipe.source, recipe.source_id),
            ).fetchone()
            if row is None:
                cur = conn.execute(
                    "INSERT INTO recipe (title, url, image, servings, kcal, protein, carbs, fat, minutes, "
                    "tags_json, has_nutrition, fetched_at, source, source_id, meal_category, storage_days, "
                    "freezable, meta_status, kcal_source, kcal_100g) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        *values,
                        recipe.source,
                        recipe.source_id,
                        meta.category,
                        meta.storage_days,
                        freezable,
                        meta.status,
                        kcal_source,
                        recipe.kcal_100g,
                    ),
                )
                recipe_id = int(cur.lastrowid or 0)
            else:
                recipe_id = int(row[0])
                if recipe.kcal is None and row[2] == "approved":
                    # zatwierdzony szacunek przeżywa ponowny crawl bez kcal ze źródła
                    conn.execute(
                        "UPDATE recipe SET title=?, url=?, image=?, servings=?, minutes=?, tags_json=?, "
                        "fetched_at=?, kcal_100g=? WHERE id=?",
                        (
                            recipe.title,
                            recipe.url,
                            recipe.image,
                            recipe.servings,
                            recipe.minutes,
                            values[9],
                            stamp,
                            recipe.kcal_100g,
                            recipe_id,
                        ),
                    )
                else:
                    conn.execute(
                        "UPDATE recipe SET title=?, url=?, image=?, servings=?, kcal=?, protein=?, carbs=?, "
                        "fat=?, minutes=?, tags_json=?, has_nutrition=?, fetched_at=?, kcal_source=?, "
                        "kcal_100g=?, est_coverage=NULL, est_status=NULL, servings_estimated=NULL, "
                        "est_unknown_json=NULL, est_assumed=NULL WHERE id=?",
                        (*values, kcal_source, recipe.kcal_100g, recipe_id),
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
        recipe_id: int | None = None,
    ) -> list[RecipeRow]:
        ing_where = "" if recipe_id is None else "WHERE ri.recipe_id = ? "
        ing_params = () if recipe_id is None else (recipe_id,)
        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT {_RECIPE_COLUMNS} FROM recipe r LEFT JOIN recipe_flag f ON f.recipe_id = r.id "
                f"{where}",
                params,
            ).fetchall()
            ing_rows = conn.execute(
                "SELECT ri.recipe_id, i.name FROM recipe_ingredient ri "
                "LEFT JOIN ingredient i ON i.id = ri.ingredient_id "
                f"{ing_where}ORDER BY ri.recipe_id, ri.pos",
                ing_params,
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
                    kcal_source=cast("Literal['source', 'estimate'] | None", r[19]),
                    kcal_100g=r[20],
                    est_coverage=r[21],
                    est_status=cast("EstStatus | None", r[22]),
                    servings_estimated=bool(r[23]),
                    est_unknown=tuple(json.loads(r[24])) if r[24] else (),
                    est_assumed=int(r[25] or 0),
                )
            )
        return result

    def recipe(
        self, recipe_id: int, stats: Mapping[str, IngredientStat], basics: frozenset[str]
    ) -> RecipeRow | None:
        rows = self._recipe_rows("WHERE r.id = ?", (recipe_id,), stats, basics, recipe_id)
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

    # --- Szacunek kcal ze składników (migracja v4, E3c) ---

    def save_ingredient_nutrition(
        self,
        ingredient_id: int,
        *,
        kcal: float | None,
        protein: float | None,
        carbs: float | None,
        fat: float | None,
        piece_g: float | None,
        source: NutritionSource,
        confidence: Literal["high", "low"],
        status: EstStatus,
        updated_at: str | None = None,
    ) -> None:
        """Upsert wartości składnika; wpis ``approved`` nie jest nadpisywany przez automat."""
        stamp = updated_at or datetime.now().isoformat(timespec="seconds")
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO ingredient_nutrition (ingredient_id, kcal, protein, carbs, fat, piece_g, "
                "source, confidence, status, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(ingredient_id) DO UPDATE SET kcal=excluded.kcal, protein=excluded.protein, "
                "carbs=excluded.carbs, fat=excluded.fat, piece_g=excluded.piece_g, source=excluded.source, "
                "confidence=excluded.confidence, status=excluded.status, updated_at=excluded.updated_at "
                "WHERE ingredient_nutrition.status IS NOT 'approved' OR excluded.source = 'manual'",
                (ingredient_id, kcal, protein, carbs, fat, piece_g, source, confidence, status, stamp),
            )

    def _nutrition_rows(self, where: str = "") -> list[IngredientNutrition]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT n.ingredient_id, i.name, n.kcal, n.protein, n.carbs, n.fat, n.piece_g, n.source, "
                "n.confidence, n.status, n.updated_at FROM ingredient_nutrition n "
                f"JOIN ingredient i ON i.id = n.ingredient_id {where} ORDER BY i.name"
            ).fetchall()
        return [
            IngredientNutrition(
                ingredient_id=r[0],
                name=r[1],
                kcal=r[2],
                protein=r[3],
                carbs=r[4],
                fat=r[5],
                piece_g=r[6],
                source=cast("NutritionSource | None", r[7]),
                confidence=cast("Literal['high', 'low'] | None", r[8]),
                status=cast("EstStatus | None", r[9]),
                updated_at=r[10],
            )
            for r in rows
        ]

    def ingredient_nutrition(self) -> dict[int, IngredientNutrition]:
        """Wszystkie wpisy po ``ingredient_id``."""
        return {n.ingredient_id: n for n in self._nutrition_rows()}

    def pending_ingredient_nutrition(self) -> list[IngredientNutrition]:
        return self._nutrition_rows("WHERE n.status = 'pending'")

    def ingredients_needing_nutrition(self) -> list[tuple[int, str]]:
        """(id, nazwa) składników bez wpisu, użytych w przepisach bez kcal ze źródła i niezatwierdzonych."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT DISTINCT i.id, i.name FROM recipe_ingredient ri "
                "JOIN recipe r ON r.id = ri.recipe_id "
                "JOIN ingredient i ON i.id = ri.ingredient_id "
                "LEFT JOIN ingredient_nutrition n ON n.ingredient_id = i.id "
                "WHERE n.ingredient_id IS NULL AND r.kcal_source IS NOT 'source' "
                "AND r.est_status IS NOT 'approved' ORDER BY i.name"
            ).fetchall()
        return [(int(r[0]), str(r[1])) for r in rows]

    def recipes_to_estimate(self) -> list[RecipeToEstimate]:
        """Przepisy bez kcal ze źródła i bez ręcznie zatwierdzonego szacunku."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT id, title, servings, kcal_100g FROM recipe "
                "WHERE kcal_source IS NOT 'source' AND est_status IS NOT 'approved' ORDER BY id"
            ).fetchall()
        return [RecipeToEstimate(r[0], r[1], r[2], r[3]) for r in rows]

    def recipe_lines(self, recipe_id: int) -> list[RecipeLine]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ri.recipe_id, ri.pos, ri.text, ri.amount, ri.unit, ri.name, ri.ingredient_id, "
                "i.name, ri.grams_assumed, r.title FROM recipe_ingredient ri "
                "JOIN recipe r ON r.id = ri.recipe_id "
                "LEFT JOIN ingredient i ON i.id = ri.ingredient_id "
                "WHERE ri.recipe_id = ? ORDER BY ri.pos",
                (recipe_id,),
            ).fetchall()
        return [RecipeLine(*r) for r in rows]

    def lines_without_amount(self) -> list[RecipeLine]:
        """Linie bez ilości i bez ``grams_assumed`` w przepisach do estymacji."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ri.recipe_id, ri.pos, ri.text, ri.amount, ri.unit, ri.name, ri.ingredient_id, "
                "i.name, ri.grams_assumed, r.title FROM recipe_ingredient ri "
                "JOIN recipe r ON r.id = ri.recipe_id "
                "LEFT JOIN ingredient i ON i.id = ri.ingredient_id "
                "WHERE ri.amount IS NULL AND ri.grams_assumed IS NULL "
                "AND r.kcal_source IS NOT 'source' AND r.est_status IS NOT 'approved' "
                "ORDER BY ri.recipe_id, ri.pos"
            ).fetchall()
        return [RecipeLine(*r) for r in rows]

    def set_grams_assumed(self, recipe_id: int, pos: int, grams: float) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE recipe_ingredient SET grams_assumed = ? WHERE recipe_id = ? AND pos = ?",
                (grams, recipe_id, pos),
            )

    def save_recipe_estimate(
        self,
        recipe_id: int,
        *,
        kcal: float | None,
        protein: float | None,
        carbs: float | None,
        fat: float | None,
        servings_estimated: bool,
        coverage: float,
        status: EstStatus,
        unknown: Iterable[str],
        assumed: int,
    ) -> bool:
        """Zapis szacunku (kcal_source='estimate'); ``has_nutrition`` = 1 tylko dla auto/approved.

        Nie rusza przepisów z kcal ze źródła ani zatwierdzonych ręcznie; zwraca, czy zapisano.
        """
        with closing(self._connect()) as conn, conn:
            cur = conn.execute(
                "UPDATE recipe SET kcal=?, protein=?, carbs=?, fat=?, kcal_source='estimate', "
                "est_coverage=?, est_status=?, servings_estimated=?, est_unknown_json=?, est_assumed=?, "
                "has_nutrition=? WHERE id=? AND kcal_source IS NOT 'source' AND est_status IS NOT 'approved'",
                (
                    kcal,
                    protein,
                    carbs,
                    fat,
                    coverage,
                    status,
                    int(servings_estimated),
                    json.dumps(list(unknown), ensure_ascii=False),
                    assumed,
                    int(status in ("auto", "approved")),
                    recipe_id,
                ),
            )
        return cur.rowcount > 0

    def set_recipe_estimate_status(self, recipe_id: int, status: EstStatus) -> None:
        """Zmiana statusu szacunku (np. zatwierdzenie); ``has_nutrition`` podąża za statusem."""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE recipe SET est_status=?, has_nutrition=? "
                "WHERE id=? AND kcal_source = 'estimate' AND kcal IS NOT NULL",
                (status, int(status in ("auto", "approved")), recipe_id),
            )
