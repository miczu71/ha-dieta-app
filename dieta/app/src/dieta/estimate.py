"""Kalkulator kcal i makroskładników przepisu ze składników.

Czysty moduł: bez store, sieci i LLM. Przelicza ilości (g, kg, łyżki, szklanki,
szczypty, sztuki, puszki) na gramy, dopasowuje składniki do słownika wartości
na 100 g (seed `nutrition_basic.json`) i sumuje wartości na porcję.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from statistics import median
from typing import Literal

from dieta.dictionary import normalize_name

SEED_DIR = Path(__file__).parent / "seed"
DEFAULT_NUTRITION = SEED_DIR / "nutrition_basic.json"
DEFAULT_MEASURES = SEED_DIR / "measures.json"

NOMINAL_MASS_G = 50.0
ASSUMED_WEIGHT = 0.5
SERVING_G = 400.0
COVERAGE_OK = 0.85

Method = Literal["exact", "measure", "piece", "assumed", "none"]


@dataclass(frozen=True)
class Per100:
    """Wartości odżywcze na 100 g składnika."""

    kcal: float
    protein: float
    carbs: float
    fat: float
    piece_g: float | None = None
    density_g_ml: float | None = None
    unit_g: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class _Entry:
    name: str
    keys: tuple[str, ...]
    values: Per100


@cache
def _phrase_re(key: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(key)}(?!\w)")


class NutritionSeed:
    """Słownik składników z wartościami na 100 g."""

    def __init__(self, entries: Sequence[_Entry]) -> None:
        self._entries = tuple(entries)

    def find(self, name: str) -> tuple[str, Per100] | None:
        """(kanoniczna nazwa, wartości) dla nazwy; najpierw dokładne dopasowanie, potem najdłuższa fraza."""
        norm = normalize_name(name)
        if not norm:
            return None
        for entry in self._entries:
            if norm in entry.keys:
                return entry.name, entry.values
        best: _Entry | None = None
        best_len = 0
        for entry in self._entries:
            for key in entry.keys:
                if len(key) > best_len and _phrase_re(key).search(norm):
                    best, best_len = entry, len(key)
        if best is None:
            return None
        return best.name, best.values


@dataclass(frozen=True)
class Measures:
    """Miary domowe z `measures.json`."""

    mass_g: Mapping[str, float]
    volume_ml: Mapping[str, float]
    small_g: Mapping[str, float]
    count_units: tuple[str, ...]
    unit_aliases: Mapping[str, str]
    default_unit_g: Mapping[str, float]
    free_words: tuple[str, ...]
    noise_words: tuple[str, ...] = ()
    assumed_caps: tuple[tuple[float, tuple[str, ...]], ...] = ()
    free_re: re.Pattern[str] | None = field(init=False, default=None, repr=False, compare=False)
    cap_res: tuple[tuple[float, tuple[re.Pattern[str], ...]], ...] = field(
        init=False, default=(), repr=False, compare=False
    )

    def __post_init__(self) -> None:
        words = sorted(self.free_words, key=len, reverse=True)
        if words:
            alternation = "|".join(re.escape(w) for w in words)
            object.__setattr__(self, "free_re", re.compile(rf"(?<!\w)(?:{alternation})(?!\w)"))
        object.__setattr__(
            self,
            "cap_res",
            tuple((cap, tuple(_phrase_re(w) for w in words_)) for cap, words_ in self.assumed_caps),
        )


def load_nutrition_seed(path: Path | None = None) -> NutritionSeed:
    data = json.loads((path or DEFAULT_NUTRITION).read_text(encoding="utf-8"))
    entries: list[_Entry] = []
    for item in data["items"]:
        names = [item["name"], *item.get("aliases", [])]
        keys = tuple(dict.fromkeys(normalize_name(n) for n in names))
        values = Per100(
            kcal=float(item["kcal"]),
            protein=float(item["protein"]),
            carbs=float(item["carbs"]),
            fat=float(item["fat"]),
            piece_g=float(item["piece_g"]) if "piece_g" in item else None,
            density_g_ml=float(item["density_g_ml"]) if "density_g_ml" in item else None,
            unit_g={str(k): float(v) for k, v in item.get("unit_g", {}).items()},
        )
        entries.append(_Entry(name=item["name"], keys=keys, values=values))
    return NutritionSeed(entries)


def load_measures(path: Path | None = None) -> Measures:
    data = json.loads((path or DEFAULT_MEASURES).read_text(encoding="utf-8"))
    return Measures(
        mass_g={str(k): float(v) for k, v in data["mass_g"].items()},
        volume_ml={str(k): float(v) for k, v in data["volume_ml"].items()},
        small_g={str(k): float(v) for k, v in data["small_g"].items()},
        count_units=tuple(data["count_units"]),
        unit_aliases={str(k): str(v) for k, v in data["unit_aliases"].items()},
        default_unit_g={str(k): float(v) for k, v in data["default_unit_g"].items()},
        free_words=tuple(data["free_words"]),
        noise_words=tuple(normalize_name(w) for w in data.get("noise_words", [])),
        assumed_caps=tuple(
            (float(c["max_g"]), tuple(normalize_name(w) for w in c["words"]))
            for c in data.get("assumed_caps", {}).values()
        ),
    )


def is_noise(name: str, text: str, measures: Measures) -> bool:
    """Nagłówek (``Ciasto:``) albo słowo szumu (``tutaj``) zamiast składnika."""
    if name.strip().endswith(":") or text.strip().endswith(":"):
        return True
    return normalize_name(name).rstrip(":").strip() in measures.noise_words


def assumed_cap(name: str, measures: Measures) -> float | None:
    """Sufit ilości zgadywanej (g) dla kategorii (tłuszcze, przyprawy) wg słów kluczowych nazwy."""
    norm = normalize_name(name)
    caps = [cap for cap, res in measures.cap_res if any(rx.search(norm) for rx in res)]
    return min(caps) if caps else None


def is_free(name: str, measures: Measures) -> bool:
    """True, gdy nazwa zawiera słowo z listy składników darmowych jako całą frazę."""
    return measures.free_re is not None and measures.free_re.search(normalize_name(name)) is not None


def to_grams(
    amount: float | None,
    unit: str | None,
    *,
    measures: Measures,
    piece_g: float | None,
    density_g_ml: float | None,
    unit_g: Mapping[str, float] | None,
) -> tuple[float | None, Method]:
    """Ilość w gramach i sposób przeliczenia; (None, "none") gdy się nie da."""
    if amount is None or amount <= 0:
        return None, "none"
    u = unit.strip().lower() if unit else None
    if u:
        if u in measures.mass_g:
            return amount * measures.mass_g[u], "exact"
        if u in measures.volume_ml:
            density = density_g_ml if density_g_ml is not None else 1.0
            return amount * measures.volume_ml[u] * density, "measure"
        if u in measures.small_g:
            return amount * measures.small_g[u], "measure"
        base = measures.unit_aliases.get(u, u)
        if unit_g and base in unit_g:
            return amount * unit_g[base], "piece"
        if base in measures.default_unit_g:
            return amount * measures.default_unit_g[base], "piece"
        if base != "szt":
            return None, "none"
    if piece_g is not None:
        return amount * piece_g, "piece"
    return None, "none"


@dataclass(frozen=True)
class Line:
    name: str
    amount: float | None
    unit: str | None
    ingredient_id: int | None
    grams_assumed: float | None = None
    text: str = ""


@dataclass(frozen=True)
class Estimate:
    kcal: float
    protein: float
    carbs: float
    fat: float
    coverage: float
    known: int
    total: int
    unknown_names: tuple[str, ...]
    servings: float
    servings_estimated: bool
    total_g: float
    assumed_lines: int


@dataclass(frozen=True)
class _Row:
    line: Line
    grams: float | None
    method: Method
    per: Per100 | None


def estimate_recipe(
    lines: Sequence[Line],
    per100: Mapping[int, Per100],
    servings: float | None,
    *,
    measures: Measures,
) -> Estimate:
    rows: list[_Row] = []
    for line in lines:
        if is_free(line.name, measures):
            continue
        if line.amount is None and is_noise(line.name, line.text, measures):
            continue
        per = per100.get(line.ingredient_id) if line.ingredient_id is not None else None
        grams, kind = to_grams(
            line.amount,
            line.unit,
            measures=measures,
            piece_g=per.piece_g if per else None,
            density_g_ml=per.density_g_ml if per else None,
            unit_g=per.unit_g if per else None,
        )
        method: Method = kind
        if grams is None and line.grams_assumed is not None:
            grams, method = line.grams_assumed, "assumed"
        rows.append(_Row(line=line, grams=grams, method=method, per=per))

    total = len(rows)
    with_grams = [r.grams for r in rows if r.grams is not None]
    nominal = median(with_grams) if with_grams else NOMINAL_MASS_G

    covered = [(r.grams, r.per, r.method) for r in rows if r.grams is not None and r.per is not None]
    known = len(covered)

    denom = sum(r.grams if r.grams is not None else nominal for r in rows)
    numer = sum(grams * (ASSUMED_WEIGHT if method == "assumed" else 1.0) for grams, _, method in covered)
    coverage = numer / denom if total and denom > 0 else 0.0

    unknown_names: list[str] = []
    for r in rows:
        if (r.grams is None or r.per is None) and r.line.name not in unknown_names:
            unknown_names.append(r.line.name)

    total_g = sum(with_grams)
    if servings is None or servings <= 0:
        servings_value = max(1.0, total_g / SERVING_G)
        servings_estimated = True
    else:
        servings_value = servings
        servings_estimated = False

    kcal = protein = carbs = fat = 0.0
    for grams, per, _ in covered:
        factor = grams / 100.0
        kcal += factor * per.kcal
        protein += factor * per.protein
        carbs += factor * per.carbs
        fat += factor * per.fat

    return Estimate(
        kcal=float(round(kcal / servings_value)),
        protein=round(protein / servings_value, 1),
        carbs=round(carbs / servings_value, 1),
        fat=round(fat / servings_value, 1),
        coverage=coverage,
        known=known,
        total=total,
        unknown_names=tuple(unknown_names),
        servings=servings_value,
        servings_estimated=servings_estimated,
        total_g=total_g,
        assumed_lines=sum(1 for r in rows if r.method == "assumed"),
    )
