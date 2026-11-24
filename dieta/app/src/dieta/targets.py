"""Cele dzienne osoby (kcal, makro) — wspólne dla karty profilu i planera."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from . import nutrition
from .settings import Settings
from .store import Store

BODY_MAX_AGE_DAYS = 14
SUGGESTION_THRESHOLD_KG_WEEK = 0.25
KCAL_MIN, KCAL_MAX = 800, 6000  # zakres celu kcal (ręczny i proponowany)
FAT_PCT_RANGE = (3.0, 60.0)  # wiarygodny % tłuszczu; poza zakresem (np. brak impedancji) → Mifflin


@dataclass(frozen=True)
class PersonTarget:
    bmr: int
    tdee: int
    auto_kcal: int
    kcal: int
    protein_g: int
    fat_g: int
    carb_g: int
    bmr_formula: Literal["katch", "mifflin"] = "mifflin"
    body_age_days: int | None = None

    @property
    def macros(self) -> nutrition.Macros:
        return nutrition.Macros(self.protein_g, self.fat_g, self.carb_g)


def person_targets(
    store: Store, settings: Settings, person_id: int, today: date | None = None
) -> PersonTarget | None:
    """Cele osoby albo ``None``, gdy brak profilu lub wagi. ``settings`` zarezerwowane na przyszłe opcje.

    BMR liczony wg Katch-McArdle, gdy najnowszy pomiar składu ma ≤ 14 dni; inaczej Mifflin-St Jeor.
    """
    del settings
    profile = store.profile(person_id)
    weight = store.latest_weight(person_id)
    if not (profile and weight):
        return None
    today = today or date.today()
    bmr = nutrition.bmr_mifflin(profile.sex, profile.age_on(today), profile.height_cm, weight)
    formula: Literal["katch", "mifflin"] = "mifflin"
    body_age: int | None = None
    body = store.latest_composition(person_id)
    if body is not None and body.fat_pct is not None and FAT_PCT_RANGE[0] <= body.fat_pct <= FAT_PCT_RANGE[1]:
        age = (today - datetime.fromtimestamp(body.ts).date()).days
        if age <= BODY_MAX_AGE_DAYS:
            bmr = nutrition.bmr_katch(weight * (1 - body.fat_pct / 100))
            formula, body_age = "katch", age
    total = nutrition.tdee(bmr, profile.pal_level)
    auto = nutrition.target_kcal(total, profile.goal, profile.pace_kg_week)
    kcal = profile.kcal_override or round(auto)
    m = nutrition.macros(kcal, weight, profile.goal, profile.protein_g_kg, profile.fat_pct)
    return PersonTarget(
        round(bmr), round(total), round(auto), kcal, m.protein_g, m.fat_g, m.carb_g, formula, body_age
    )


def kcal_suggestion(
    goal: nutrition.Goal, pace_kg_week: float, current_kcal: int, trend_kg_week: float | None
) -> int | None:
    """Proponowany cel kcal wg trendu wagi; ``None`` bez trendu albo gdy odchylenie ≤ 0,25 kg/tydz.

    Trend wyższy niż oczekiwany (za mało chudnie) → mniej kcal; niższy → więcej.
    """
    if trend_kg_week is None:
        return None
    expected = {"lose": -pace_kg_week, "gain": pace_kg_week}.get(goal, 0.0)
    delta = trend_kg_week - expected
    if abs(delta) <= SUGGESTION_THRESHOLD_KG_WEEK:
        return None
    daily = delta * nutrition.KCAL_PER_KG_FAT / nutrition.DAYS_PER_WEEK
    proposal = min(max(round((current_kcal - daily) / 10) * 10, KCAL_MIN), KCAL_MAX)
    return None if proposal == current_kcal else proposal
