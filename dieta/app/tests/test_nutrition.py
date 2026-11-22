from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from dieta.nutrition import Macros, bmr_mifflin, macros, target_kcal, tdee, trend_7d


def test_bmr_male() -> None:
    # 10*80 + 6.25*180 - 5*30 + 5 = 800 + 1125 - 150 + 5 = 1780
    assert bmr_mifflin("m", 30, 180, 80) == pytest.approx(1780)


def test_bmr_female() -> None:
    # 10*60 + 6.25*165 - 5*40 - 161 = 600 + 1031.25 - 200 - 161 = 1270.25
    assert bmr_mifflin("f", 40, 165, 60) == pytest.approx(1270.25)


def test_bmr_rejects_age_below_10() -> None:
    with pytest.raises(ValueError):
        bmr_mifflin("m", 9, 180, 80)


def test_bmr_rejects_zero_weight() -> None:
    with pytest.raises(ValueError):
        bmr_mifflin("m", 30, 180, 0)


def test_tdee_pal_3() -> None:
    # 1780 * 1.55 = 2759
    assert tdee(1780, 3) == pytest.approx(2759)


def test_tdee_rejects_unknown_pal() -> None:
    with pytest.raises(ValueError):
        tdee(1780, 6)


def test_target_lose_half_kg_week() -> None:
    # 2759 - 0.5 * 7700 / 7 = 2759 - 550 = 2209
    assert target_kcal(2759, "lose", 0.5) == pytest.approx(2209)


def test_target_gain_quarter_kg_week() -> None:
    # 2759 + 0.25 * 7700 / 7 = 2759 + 275 = 3034
    assert target_kcal(2759, "gain", 0.25) == pytest.approx(3034)


def test_target_maintain_no_change() -> None:
    assert target_kcal(2759, "maintain", 0.5) == pytest.approx(2759)


def test_macros_maintain_defaults() -> None:
    # protein 1.6 * 80 = 128 g; fat 0.30 * 2000 / 9 = 66.67 -> 67 g;
    # carb (2000 - 128*4 - 66.67*9) / 4 = 222 g
    assert macros(2000, 80, "maintain") == Macros(protein_g=128, fat_g=67, carb_g=222)


def test_macros_lose_default_protein() -> None:
    # protein 1.8 * 80 = 144 g; carb (2000 - 144*4 - 600) / 4 = 206 g
    assert macros(2000, 80, "lose") == Macros(protein_g=144, fat_g=67, carb_g=206)


def test_macros_protein_override() -> None:
    # protein 2.0 * 70 = 140 g
    result = macros(2000, 70, "gain", protein_g_kg=2.0)
    assert result.protein_g == 140


def test_macros_carb_never_negative() -> None:
    # protein 180 g = 720 kcal, fat 300 kcal -> remainder negative -> carb 0
    assert macros(1000, 100, "lose").carb_g == 0


def test_macros_rejects_zero_weight() -> None:
    with pytest.raises(ValueError):
        macros(2000, 0, "maintain")


def test_macros_is_frozen() -> None:
    m = Macros(protein_g=1, fat_g=2, carb_g=3)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.protein_g = 5  # type: ignore[misc]


def test_trend_basic_window() -> None:
    points = [(date(2024, 1, 1), 80.0), (date(2024, 1, 3), 82.0), (date(2024, 1, 10), 79.0)]
    assert trend_7d(points) == [
        (date(2024, 1, 1), 80.0),
        (date(2024, 1, 3), 81.0),
        (date(2024, 1, 10), 79.0),
    ]


def test_trend_window_includes_d_minus_6() -> None:
    points = [(date(2024, 1, 2), 80.0), (date(2024, 1, 8), 70.0)]
    assert trend_7d(points)[1] == (date(2024, 1, 8), 75.0)


def test_trend_window_excludes_d_minus_7() -> None:
    points = [(date(2024, 1, 1), 80.0), (date(2024, 1, 8), 70.0)]
    assert trend_7d(points)[1] == (date(2024, 1, 8), 70.0)


def test_trend_sorts_input() -> None:
    points = [(date(2024, 1, 3), 82.0), (date(2024, 1, 1), 80.0)]
    assert trend_7d(points) == [(date(2024, 1, 1), 80.0), (date(2024, 1, 3), 81.0)]


def test_trend_empty() -> None:
    assert trend_7d([]) == []
