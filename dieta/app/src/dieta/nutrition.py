"""Obliczenia żywieniowe: BMR (Mifflin-St Jeor, Katch-McArdle), TDEE, cel kcal, makroskładniki, trend wagi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Sex = Literal["m", "f"]
Goal = Literal["lose", "maintain", "gain"]

PAL = {1: 1.2, 2: 1.375, 3: 1.55, 4: 1.725, 5: 1.9}

KCAL_PER_KG_FAT = 7700.0
DAYS_PER_WEEK = 7


def bmr_mifflin(sex: Sex, age: int, height_cm: float, weight_kg: float) -> float:
    if age < 10:
        raise ValueError("age must be >= 10")
    if weight_kg <= 0:
        raise ValueError("weight_kg must be > 0")
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex == "m" else -161)


def tdee(bmr: float, pal_level: int) -> float:
    if pal_level not in PAL:
        raise ValueError(f"unknown PAL level: {pal_level}")
    return bmr * PAL[pal_level]


def target_kcal(tdee_kcal: float, goal: Goal, pace_kg_week: float) -> float:
    daily_delta = pace_kg_week * KCAL_PER_KG_FAT / DAYS_PER_WEEK
    if goal == "lose":
        return tdee_kcal - daily_delta
    if goal == "gain":
        return tdee_kcal + daily_delta
    return tdee_kcal


@dataclass(frozen=True)
class Macros:
    protein_g: int
    fat_g: int
    carb_g: int


def macros(
    kcal: float,
    weight_kg: float,
    goal: Goal,
    protein_g_kg: float | None = None,
    fat_pct: float = 0.30,
) -> Macros:
    if weight_kg <= 0:
        raise ValueError("weight_kg must be > 0")
    if protein_g_kg is None:
        protein_g_kg = 1.8 if goal == "lose" else 1.6
    protein = protein_g_kg * weight_kg
    fat = fat_pct * kcal / 9
    carb = max(0.0, (kcal - protein * 4 - fat * 9) / 4)
    return Macros(protein_g=round(protein), fat_g=round(fat), carb_g=round(carb))


def trend_7d(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    ordered = sorted(points, key=lambda p: p[0])
    result: list[tuple[date, float]] = []
    for d, _ in ordered:
        start = d - timedelta(days=6)
        window = [w for dd, w in ordered if start <= dd <= d]
        result.append((d, sum(window) / len(window)))
    return result


def bmr_katch(lbm_kg: float) -> float:
    if lbm_kg <= 0:
        raise ValueError("lbm_kg must be > 0")
    return 370 + 21.6 * lbm_kg


def trend_rate_kg_week(points: list[tuple[date, float]], days: int = 21) -> float | None:
    """Nachylenie regresji liniowej wagi z ostatnich ``days`` dni (kg/tydz.).

    ``None``, gdy w oknie jest < 4 punktów albo rozpiętość dat < 14 dni.
    """
    if not points:
        return None
    latest = max(d for d, _ in points)
    window = [(d.toordinal(), w) for d, w in points if d >= latest - timedelta(days=days - 1)]
    if len(window) < 4:
        return None
    xs = [x for x, _ in window]
    if max(xs) - min(xs) < 14:
        return None
    n = len(window)
    mx = sum(xs) / n
    my = sum(w for _, w in window) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (w - my) for x, w in window)
    return sxy / sxx * DAYS_PER_WEEK
