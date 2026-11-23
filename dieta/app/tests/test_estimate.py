from __future__ import annotations

import json
from pathlib import Path

import pytest

from dieta.estimate import (
    Line,
    Measures,
    Per100,
    estimate_recipe,
    is_free,
    load_measures,
    load_nutrition_seed,
    to_grams,
)


@pytest.fixture(scope="module")
def measures() -> Measures:
    return load_measures()


def _grams(
    measures: Measures,
    amount: float | None,
    unit: str | None,
    *,
    piece_g: float | None = None,
    density_g_ml: float | None = None,
    unit_g: dict[str, float] | None = None,
) -> tuple[float | None, str]:
    grams, method = to_grams(
        amount,
        unit,
        measures=measures,
        piece_g=piece_g,
        density_g_ml=density_g_ml,
        unit_g=unit_g,
    )
    return grams, method


# --- przeliczenia ---------------------------------------------------------


def test_grams_exact_units(measures: Measures) -> None:
    assert _grams(measures, 250, "g") == (250, "exact")
    grams, method = _grams(measures, 1.5, "kg")
    assert grams == pytest.approx(1500)
    assert method == "exact"


def test_tablespoon_of_oil_uses_density(measures: Measures) -> None:
    # 2 łyżki = 30 ml * 0.91 g/ml = 27.3 g
    grams, method = _grams(measures, 2, "łyżki", density_g_ml=0.91)
    assert grams == pytest.approx(27.3)
    assert method == "measure"


def test_cup_of_flour_uses_density(measures: Measures) -> None:
    # 1 szklanka = 250 ml * 0.55 g/ml = 137.5 g
    grams, method = _grams(measures, 1, "szklanka", density_g_ml=0.55)
    assert grams == pytest.approx(137.5)
    assert method == "measure"


def test_volume_without_density_assumes_water(measures: Measures) -> None:
    grams, method = _grams(measures, 1, "dl")
    assert grams == pytest.approx(100)
    assert method == "measure"


def test_pinch_is_small_measure(measures: Measures) -> None:
    grams, method = _grams(measures, 2, "szczypty")
    assert grams == pytest.approx(1.0)
    assert method == "measure"


def test_clove_of_garlic_from_unit_g(measures: Measures) -> None:
    grams, method = _grams(measures, 2, "ząbki", unit_g={"ząbek": 4})
    assert grams == pytest.approx(8)
    assert method == "piece"


def test_piece_with_szt_uses_piece_g(measures: Measures) -> None:
    grams, method = _grams(measures, 2, "szt", piece_g=55)
    assert grams == pytest.approx(110)
    assert method == "piece"


def test_piece_without_unit_uses_piece_g(measures: Measures) -> None:
    grams, method = _grams(measures, 2, None, piece_g=55)
    assert grams == pytest.approx(110)
    assert method == "piece"


def test_can_from_unit_g(measures: Measures) -> None:
    grams, method = _grams(measures, 1, "puszka", unit_g={"puszka": 400})
    assert grams == pytest.approx(400)
    assert method == "piece"


def test_no_amount_gives_none(measures: Measures) -> None:
    assert _grams(measures, None, "g") == (None, "none")
    assert _grams(measures, None, None, piece_g=55) == (None, "none")


def test_negative_amount_gives_none(measures: Measures) -> None:
    assert _grams(measures, -5, "g") == (None, "none")
    assert _grams(measures, -1, None, piece_g=55) == (None, "none")


def test_zero_amount_gives_none(measures: Measures) -> None:
    assert _grams(measures, 0, "g") == (None, "none")
    assert _grams(measures, 0, "szt", piece_g=55) == (None, "none")


def test_unknown_unit_gives_none(measures: Measures) -> None:
    # "pęczek" nie ma wagi w unit_g ani default_unit_g i nie jest szt
    assert _grams(measures, 1, "pęczek") == (None, "none")


def test_piece_unit_without_weight_gives_none(measures: Measures) -> None:
    assert _grams(measures, 2, "szt", piece_g=None) == (None, "none")


# --- darmowe składniki ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["sól, pieprz", "pieprz czarny", "do smaku", "woda", "Sól"],
)
def test_free_ingredients(name: str, measures: Measures) -> None:
    assert is_free(name, measures) is True


@pytest.mark.parametrize("name", ["mleko", "solanka", "wodorosty"])
def test_paid_ingredients(name: str, measures: Measures) -> None:
    assert is_free(name, measures) is False


# --- słownik (seed) -------------------------------------------------------


def test_find_exact_and_alias_on_real_seed() -> None:
    seed = load_nutrition_seed()
    chicken = seed.find("pierś z kurczaka")
    assert chicken is not None
    assert chicken[0] == "filet z kurczaka"
    assert chicken[1].kcal == 110

    egg = seed.find("jajko")
    assert egg is not None
    assert egg[0] == "jajka"
    assert egg[1].piece_g == 55


def test_find_no_match_on_real_seed() -> None:
    assert load_nutrition_seed().find("zzzqqq") is None


def test_find_prefers_longest_phrase(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.json"
    base = {"kcal": 350, "protein": 7, "carbs": 78, "fat": 1}
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"name": "ryż biały", "aliases": ["ryż"], **base},
                    {
                        "name": "ryż basmati",
                        "aliases": ["ryż basmati długoziarnisty"],
                        **base,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    seed = load_nutrition_seed(path)
    found = seed.find("ryż basmati długoziarnisty gotowany")
    assert found is not None
    assert found[0] == "ryż basmati"
    found = seed.find("ryż na obiad")
    assert found is not None
    assert found[0] == "ryż biały"


def test_find_requires_whole_word(tmp_path: Path) -> None:
    path = tmp_path / "nutrition.json"
    base = {"kcal": 350, "protein": 7, "carbs": 78, "fat": 1}
    path.write_text(
        json.dumps({"items": [{"name": "ryż biały", "aliases": ["ryż"], **base}]}),
        encoding="utf-8",
    )
    assert load_nutrition_seed(path).find("ryżowy makaron") is None


# --- estymacja przepisu ---------------------------------------------------

CHICKEN = Per100(kcal=110, protein=23, carbs=0, fat=1.5, piece_g=200)
OIL = Per100(kcal=884, protein=0, carbs=0, fat=100, density_g_ml=0.91)
SOY = Per100(kcal=53, protein=8, carbs=4.9, fat=0.6)
PER100 = {1: CHICKEN, 2: OIL, 4: SOY}


def test_simple_recipe_hand_computed(measures: Measures) -> None:
    # kurczak 200 g: 220 kcal, 46 g białka, 3 g tłuszczu
    # olej 1 łyżka = 15 ml * 0.91 = 13.65 g: 13.65 * 8.84 = 120.666 kcal, 13.65 g tłuszczu
    # sól pominięta (darmowa); 340.666 kcal / 2 porcje = 170.33 -> 170
    lines = [
        Line("pierś z kurczaka", 200, "g", 1),
        Line("oliwa z oliwek", 1, "łyżka", 2),
        Line("sól", 1, "szczypta", None),
    ]
    est = estimate_recipe(lines, PER100, 2, measures=measures)
    assert est.kcal == 170
    assert est.protein == pytest.approx(23.0)
    assert est.carbs == pytest.approx(0.0)
    assert est.fat == pytest.approx(8.3, abs=0.05)
    assert est.total == 2
    assert est.known == 2
    assert est.coverage == pytest.approx(1.0)
    assert est.unknown_names == ()
    assert est.servings == 2
    assert est.servings_estimated is False
    assert est.total_g == pytest.approx(213.65)
    assert est.assumed_lines == 0


def test_free_lines_are_skipped(measures: Measures) -> None:
    est = estimate_recipe(
        [Line("sól", 1, "szczypta", None), Line("woda", 100, "ml", None)],
        {},
        1,
        measures=measures,
    )
    assert est.total == 0
    assert est.coverage == 0.0
    assert est.kcal == 0


def test_line_without_grams_lowers_coverage(measures: Measures) -> None:
    lines = [
        Line("pierś z kurczaka", 200, "g", 1),
        Line("oliwa z oliwek", 1, "łyżka", 2),
        Line("pomidory suszone", None, None, 3),
    ]
    est = estimate_recipe(lines, PER100, 2, measures=measures)
    # nominalna masa = mediana (200, 13.65) = 106.825; 213.65 / (213.65 + 106.825) = 2/3
    assert est.coverage == pytest.approx(2 / 3)
    assert est.total == 3
    assert est.known == 2
    assert est.unknown_names == ("pomidory suszone",)
    assert est.kcal == 170


def test_unknown_names_unique_in_order(measures: Measures) -> None:
    lines = [
        Line("pomidory suszone", None, None, None),
        Line("pomidory suszone", 1, "szt", None),
        Line("majonez", 10, "g", 99),
    ]
    est = estimate_recipe(lines, {}, 1, measures=measures)
    assert est.unknown_names == ("pomidory suszone", "majonez")
    assert est.known == 0


def test_assumed_line_counts_half(measures: Measures) -> None:
    lines = [
        Line("pierś z kurczaka", 200, "g", 1),
        Line("oliwa z oliwek", 1, "łyżka", 2),
        Line("sos sojowy", None, None, 4, grams_assumed=20),
    ]
    est = estimate_recipe(lines, PER100, 2, measures=measures)
    # waga: 213.65 pełne + 0.5 * 20 = 223.65 z 233.65 masy
    assert est.coverage == pytest.approx(223.65 / 233.65)
    assert est.known == 3
    assert est.assumed_lines == 1
    # 340.666 + 20 g * 53 kcal/100 g = 351.266 / 2 = 175.63 -> 176
    assert est.kcal == 176


def test_missing_servings_uses_total_mass(measures: Measures) -> None:
    lines = [Line("pierś z kurczaka", 1000, "g", 1)]
    est = estimate_recipe(lines, PER100, None, measures=measures)
    assert est.servings_estimated is True
    assert est.total_g == pytest.approx(1000)
    assert est.servings == pytest.approx(1000 / 400)
    # 1000 g * 110 kcal/100 g = 1100 kcal / 2.5 porcji = 440
    assert est.kcal == 440


def test_missing_servings_minimum_one(measures: Measures) -> None:
    est = estimate_recipe([Line("oliwa z oliwek", 1, "łyżka", 2)], PER100, 0, measures=measures)
    assert est.servings == 1.0
    assert est.servings_estimated is True


def test_empty_recipe(measures: Measures) -> None:
    est = estimate_recipe([], {}, 2, measures=measures)
    assert est.coverage == 0.0
    assert est.kcal == 0
    assert est.total == 0
    assert est.known == 0
    assert est.unknown_names == ()
    assert est.servings == 2
    assert est.servings_estimated is False


def test_piece_recipe_on_real_seed(measures: Measures) -> None:
    seed = load_nutrition_seed()
    found = seed.find("jajko")
    assert found is not None
    _, values = found
    est = estimate_recipe([Line("jajko", 2, "szt", 10)], {10: values}, 1, measures=measures)
    # 2 szt * 55 g = 110 g; 110 * 143 kcal/100 g = 157.3 -> 157
    assert est.kcal == 157
    assert est.coverage == pytest.approx(1.0)
