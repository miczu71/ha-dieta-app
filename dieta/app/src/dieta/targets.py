"""Cele dzienne osoby (kcal, makro) — wspólne dla karty profilu i planera."""

from __future__ import annotations

from dataclasses import dataclass

from . import nutrition
from .settings import Settings
from .store import Store


@dataclass(frozen=True)
class PersonTarget:
    bmr: int
    tdee: int
    auto_kcal: int
    kcal: int
    protein_g: int
    fat_g: int
    carb_g: int

    @property
    def macros(self) -> nutrition.Macros:
        return nutrition.Macros(self.protein_g, self.fat_g, self.carb_g)


def person_targets(store: Store, settings: Settings, person_id: int) -> PersonTarget | None:
    """Cele osoby albo ``None``, gdy brak profilu lub wagi. ``settings`` zarezerwowane na przyszłe opcje."""
    del settings
    profile = store.profile(person_id)
    weight = store.latest_weight(person_id)
    if not (profile and weight):
        return None
    bmr = nutrition.bmr_mifflin(profile.sex, profile.age, profile.height_cm, weight)
    total = nutrition.tdee(bmr, profile.pal_level)
    auto = nutrition.target_kcal(total, profile.goal, profile.pace_kg_week)
    kcal = profile.kcal_override or round(auto)
    m = nutrition.macros(kcal, weight, profile.goal, profile.protein_g_kg, profile.fat_pct)
    return PersonTarget(round(bmr), round(total), round(auto), kcal, m.protein_g, m.fat_g, m.carb_g)
