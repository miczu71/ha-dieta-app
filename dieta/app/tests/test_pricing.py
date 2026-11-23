from datetime import date

import pytest

from dieta.pricing import IngredientPrice, ingredient_prices, parse_pack_g, price_per_100g
from dieta.zakupy_client import Purchase

D = date(2026, 11, 1)


def _p(
    code: str,
    names: tuple[str, ...] = ("Produkt",),
    unit_price: float | None = None,
    is_weight: bool | None = None,
    purchases: int = 1,
) -> Purchase:
    return Purchase(
        code=code,
        chain="lidl",
        name=names[0],
        names=names,
        purchases=purchases,
        spend=10.0,
        first=D,
        last=D,
        dates=(D,),
        category="food",
        unit_price=unit_price,
        is_weight=is_weight,
    )


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (("Twaróg 400g",), 400.0),
        (("Mąka 0,5 kg",), 500.0),
        (("Mleko 1 l",), 1000.0),
        (("Śmietana 500ml",), 500.0),
        (("Jogurt 6x100g",), 600.0),
        (("Chleb tostowy",), None),
        ((), None),
        (("Ser 200g", "Ser 400 g"), 300.0),
        (("Pieczywo 2 gł",), None),
    ],
)
def test_parse_pack_g(names: tuple[str, ...], expected: float | None) -> None:
    assert parse_pack_g(names) == expected


def test_price_per_100g_weight_uses_price_per_kg() -> None:
    assert price_per_100g(_p("a", unit_price=24.9, is_weight=True), None) == pytest.approx(2.49)


def test_price_per_100g_packed_with_grammage() -> None:
    assert price_per_100g(_p("a", ("Twaróg 400g",), 6.0, False), None) == pytest.approx(1.5)


def test_price_per_100g_packed_without_grammage_uses_piece_g() -> None:
    assert price_per_100g(_p("a", ("Cebula",), 1.0, False), 100.0) == pytest.approx(1.0)


def test_price_per_100g_none_cases() -> None:
    assert price_per_100g(_p("a", ("Cebula",), 1.0, False), None) is None
    assert price_per_100g(_p("a", ("Cebula 100g",), None, None), 100.0) is None
    assert price_per_100g(_p("a", ("Cebula 100g",), 0.0, False), 100.0) is None


def test_ingredient_prices_weighted_median_and_pack() -> None:
    purchases = [
        _p("a", ("Mleko 1l",), 4.0, False, purchases=3),  # 0,4/100 g
        _p("b", ("Mleko 2l",), 10.0, False, purchases=1),  # 0,5/100 g
        _p("c", ("Mleko x",), None, None),  # bez ceny
        _p("z", ("Inny 100g",), 9.0, False),  # niezmapowany
    ]
    mapping = {"a": "mleko", "b": "mleko", "c": "mleko"}
    prices = ingredient_prices(purchases, mapping, {})
    assert set(prices) == {"mleko"}
    price = prices["mleko"]
    assert price == IngredientPrice(price100=pytest.approx(0.4), pack_g=1500.0, codes=("a", "b"))  # type: ignore[arg-type]


def test_ingredient_prices_skips_ingredient_without_any_price() -> None:
    assert ingredient_prices([_p("a", ("Cebula",), 1.0, False)], {"a": "cebula"}, {}) == {}


def test_ingredient_prices_uses_piece_g_by_name() -> None:
    prices = ingredient_prices([_p("a", ("Cebula",), 1.0, False)], {"a": "cebula"}, {"cebula": 100.0})
    assert prices["cebula"].price100 == pytest.approx(1.0)
    assert prices["cebula"].pack_g is None
