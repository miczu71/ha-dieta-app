"""Pomiar metody szacunku kcal na przepisach z kcal ze źródła (``python -m dieta estimate --validate``).

Baza otwierana tylko do odczytu; liczy wyłącznie tabela seedu (bez OFF i LLM), nic nie zapisuje.
"""

from __future__ import annotations

import sqlite3
import urllib.parse
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from statistics import median
from typing import TextIO

from dieta.dictionary import normalize_name
from dieta.estimate import COVERAGE_OK, Line, Per100, estimate_recipe, load_measures, load_nutrition_seed

WITHIN = 0.30


def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def _stats(errors: list[float]) -> str:
    if not errors:
        return "brak danych"
    within = sum(1 for e in errors if e <= WITHIN)
    share = _pct(within, len(errors))
    return f"mediana |błędu| {100 * median(errors):.1f}%, w ±30%: {within}/{len(errors)} ({share})"


def validate(db_path: Path, out: TextIO) -> int:
    """Wypisuje raport; zwraca kod wyjścia (0 = ok, 1 = brak bazy/danych)."""
    if not db_path.is_file():
        print(f"Brak pliku bazy: {db_path}", file=out)
        return 1
    uri = f"file:{urllib.parse.quote(str(db_path))}?mode=ro"
    measures = load_measures()
    seed = load_nutrition_seed()
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(recipe)")}
        flag = "kcal_source = 'source'" if "kcal_source" in columns else "has_nutrition = 1"
        recipes = conn.execute(
            f"SELECT id, source, servings, kcal FROM recipe WHERE {flag} AND kcal > 0 ORDER BY id"
        ).fetchall()
        lines = defaultdict(list)
        for recipe_id, _pos, amount, unit, name, ing_name in conn.execute(
            "SELECT ri.recipe_id, ri.pos, ri.amount, ri.unit, ri.name, i.name FROM recipe_ingredient ri "
            "LEFT JOIN ingredient i ON i.id = ri.ingredient_id ORDER BY ri.recipe_id, ri.pos"
        ):
            lines[recipe_id].append((amount, unit, ing_name or name))

    ids: dict[str, int] = {}
    per100: dict[int, Per100] = {}

    def ingredient_id(name: str) -> int | None:
        key = normalize_name(name)
        if key not in ids:
            found = seed.find(key)
            ids[key] = len(ids)
            if found is not None:
                per100[ids[key]] = found[1]
        return ids[key] if ids[key] in per100 else None

    all_err: list[float] = []
    good_err: list[float] = []
    by_source: dict[str, list[float]] = defaultdict(list)
    covered = 0
    for recipe_id, source, servings, kcal in recipes:
        est_lines = [Line(name, amount, unit, ingredient_id(name)) for amount, unit, name in lines[recipe_id]]
        if not est_lines:
            continue
        est = estimate_recipe(est_lines, per100, servings, measures=measures)
        error = abs(est.kcal - kcal) / kcal
        all_err.append(error)
        by_source[source].append(error)
        if est.coverage >= COVERAGE_OK:
            covered += 1
            good_err.append(error)

    print(f"Przepisy z kcal ze źródła: {len(all_err)}", file=out)
    print(f"Pokrycie ≥ {COVERAGE_OK:.2f}: {covered} ({_pct(covered, len(all_err))})", file=out)
    print(f"Wszystkie: {_stats(all_err)}", file=out)
    print(f"Tylko z pokryciem ≥ {COVERAGE_OK:.2f}: {_stats(good_err)}", file=out)
    print("Per źródło:", file=out)
    for source in sorted(by_source):
        print(f"  {source}: {len(by_source[source])} przepisów, {_stats(by_source[source])}", file=out)
    return 0 if all_err else 1
