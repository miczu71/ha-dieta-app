from datetime import date

from dieta.match import familiarity
from dieta.profile import IngredientStat


def _stat(name: str, at_home: bool = False) -> IngredientStat:
    return IngredientStat(name, 3, 10.0, date(2026, 11, 1), 1.0, at_home, "dry")


STATS = {
    n: _stat(n, at_home=n in {"ryż", "cebula"})
    for n in ("ciecierzyca", "ryż", "cebula", "czosnek", "oliwa", "cytryny")
}
BASICS = frozenset({"sól", "pieprz"})


def test_five_of_six_missing_tahini_basics_skipped() -> None:
    names: list[str | None] = [
        "ciecierzyca",
        "ryż",
        "cebula",
        "czosnek",
        "oliwa",
        "tahini",
        "sól",
        "pieprz",
    ]
    result = familiarity(names, STATS, basics=BASICS)
    assert (result.known, result.total) == (5, 6)
    assert result.missing == ("tahini",)
    assert round(result.ratio, 3) == 0.833


def test_unknown_counts_in_denominator_only() -> None:
    result = familiarity(["ryż", None, "cebula"], STATS, basics=BASICS)
    assert (result.known, result.total) == (2, 3)
    assert result.missing == ()


def test_only_basics_is_zero_total() -> None:
    result = familiarity(["sól"], STATS, basics=BASICS)
    assert (result.known, result.total, result.ratio) == (0, 0, 0.0)


def test_duplicates_counted_once() -> None:
    result = familiarity(["ryż", "ryż", "tahini", "tahini"], STATS, basics=BASICS)
    assert (result.known, result.total) == (1, 2)
