"""Znajomość przepisu: ile składników (bez podstawowych) pojawia się w zakupach."""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

from dieta.profile import IngredientStat

# Składniki „podstawowe": zawsze w domu, nie liczą się ani do licznika, ani do mianownika.
DEFAULT_BASICS: frozenset[str] = frozenset({"sól", "pieprz", "woda", "olej", "cukier"})


class Familiarity(NamedTuple):
    known: int
    total: int
    missing: tuple[str, ...]

    @property
    def ratio(self) -> float:
        return self.known / self.total if self.total else 0.0


def familiarity(
    ingredients: list[str | None],
    stats: Mapping[str, IngredientStat],
    *,
    basics: frozenset[str],
) -> Familiarity:
    """``ingredients``: nazwy składników słownika; ``None`` = niezmapowany (nieznany).

    Podstawowe pomijamy. Nieznany liczy się do mianownika, ale nie trafia do ``missing`` (brak nazwy).
    Duplikaty składnika liczą się raz.
    """
    seen: set[str] = set()
    known = 0
    total = 0
    missing: list[str] = []
    for name in ingredients:
        if name is None:
            total += 1
            continue
        if name in basics or name in seen:
            continue
        seen.add(name)
        total += 1
        stat = stats.get(name)
        if stat is None:
            missing.append(name)
            continue
        known += 1
    return Familiarity(known=known, total=total, missing=tuple(missing))
