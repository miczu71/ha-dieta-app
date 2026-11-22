"""Profil zakupowy: składniki z wagą świeżości zakupów i flagą „prawdopodobnie w domu"."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date

from dieta.dictionary import Durability
from dieta.zakupy_client import Purchase

HALF_LIFE_DAYS = 90


@dataclass(frozen=True)
class IngredientStat:
    ingredient: str
    purchases: int
    spend: float
    last: date
    weight_score: float
    at_home: bool
    durability: Durability | None


@dataclass
class _Group:
    dates: set[date]
    spend: float
    last: date
    durability: Durability | None


def build_stats(
    purchases: Iterable[Purchase],
    mapping: Mapping[str, tuple[str, Durability | None]],
    today: date,
    shelf_days: Mapping[Durability, int],
) -> list[IngredientStat]:
    """Agreguje zakupy po składniku.

    ``mapping``: code → (składnik, trwałość); kody spoza mapowania są pomijane.

    ``weight_score`` = Σ 0.5 ** (wiek_dni / 90) po unikalnych datach zakupu składnika.
    ``at_home`` = dni od ostatniego zakupu ≤ próg trwałości składnika (bez trwałości → False).
    Wynik posortowany malejąco po ``weight_score``, potem po nazwie.
    """
    groups: dict[str, _Group] = {}
    for purchase in purchases:
        if purchase.code not in mapping:
            continue
        ingredient, durability = mapping[purchase.code]
        group = groups.get(ingredient)
        if group is None:
            group = _Group(dates=set(), spend=0.0, last=purchase.last, durability=None)
            groups[ingredient] = group
        group.dates.update(purchase.dates)
        group.spend += purchase.spend
        group.last = max(group.last, purchase.last)
        if group.durability is None:
            group.durability = durability

    stats = [
        IngredientStat(
            ingredient=ingredient,
            purchases=len(group.dates),
            spend=round(group.spend, 2),
            last=group.last,
            weight_score=sum(0.5 ** ((today - d).days / HALF_LIFE_DAYS) for d in group.dates),
            at_home=group.durability is not None
            and (today - group.last).days <= shelf_days[group.durability],
            durability=group.durability,
        )
        for ingredient, group in groups.items()
    ]
    stats.sort(key=lambda s: (-s.weight_score, s.ingredient))
    return stats
