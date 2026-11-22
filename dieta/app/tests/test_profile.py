from datetime import date, timedelta

import pytest

from dieta.profile import build_stats
from dieta.zakupy_client import Purchase

TODAY = date(2026, 11, 22)
SHELF = {"fresh": 4, "chilled": 10, "dry": 30, "frozen": 60}


def _purchase(code: str, day: date, spend: float = 10.0, last: date | None = None) -> Purchase:
    return Purchase(
        code=code,
        chain="lidl",
        name=code,
        names=(code,),
        purchases=1,
        spend=spend,
        first=day,
        last=last or day,
        dates=(day,),
        category="food",
    )


def test_weight_score_is_sum_of_half_life_decay() -> None:
    today_buy = _purchase("a", TODAY)
    old_buy = _purchase("b", TODAY - timedelta(days=90))
    mapping = {"a": ("mleko", "chilled"), "b": ("mleko", "chilled")}
    (stat,) = build_stats([today_buy, old_buy], mapping, TODAY, SHELF)
    assert stat.ingredient == "mleko"
    assert stat.purchases == 2
    assert stat.weight_score == pytest.approx(1.0 + 0.5)
    assert stat.spend == pytest.approx(20.0)


def test_unmapped_codes_are_ignored() -> None:
    stats = build_stats([_purchase("a", TODAY)], {}, TODAY, SHELF)
    assert stats == []


def test_at_home_threshold_is_inclusive() -> None:
    limit = TODAY - timedelta(days=SHELF["chilled"])
    just_in = build_stats([_purchase("a", limit, last=limit)], {"a": ("mleko", "chilled")}, TODAY, SHELF)
    just_out = build_stats(
        [_purchase("a", limit - timedelta(days=1), last=limit - timedelta(days=1))],
        {"a": ("mleko", "chilled")},
        TODAY,
        SHELF,
    )
    assert just_in[0].at_home is True
    assert just_out[0].at_home is False


def test_missing_durability_is_never_at_home() -> None:
    (stat,) = build_stats([_purchase("a", TODAY)], {"a": ("kasza", None)}, TODAY, SHELF)
    assert stat.at_home is False
    assert stat.durability is None


def test_stats_sorted_by_weight_score_desc() -> None:
    buys = [_purchase("a", TODAY - timedelta(days=60)), _purchase("b", TODAY)]
    mapping = {"a": ("ryż", "dry"), "b": ("banany", "fresh")}
    stats = build_stats(buys, mapping, TODAY, SHELF)
    assert [s.ingredient for s in stats] == ["banany", "ryż"]
