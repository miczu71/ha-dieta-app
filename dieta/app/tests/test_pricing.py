from datetime import date

import pytest

from dieta.pricing import (
    Factor,
    IngredientPrice,
    frisco_prices,
    ingredient_prices,
    parse_pack_g,
    price_per_100g,
    receipt_to_frisco_factor,
)
from dieta.zakupy_client import CatalogItem, Purchase

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


def test_price_per_100g_multi_piece_pack_is_not_priced_by_piece_mass() -> None:
    # „10 szt." za 12 zł: masa jednej sztuki (60 g) nie jest masą opakowania
    assert price_per_100g(_p("a", ("Jajka M 10 szt.",), 12.0, False), 60.0) is None
    assert price_per_100g(_p("a", ("Jajko 1 szt.",), 1.0, False), 60.0) == pytest.approx(1.0 / 60 * 100)


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


def _c(ean: str, price: float = 10.0, size: float | None = 1.0, unit: str | None = "Kilogram") -> CatalogItem:
    return CatalogItem(ean, f"p{ean}", None, size, unit, price, "2026-11-20T08:00:00")


def test_factor_is_median_ratio_with_pair_count() -> None:
    frisco = [_c("1", 10.0), _c("2", 10.0), _c("3", 10.0), _c("9", 10.0, unit="Piece")]
    purchases = [
        _p("b:1", unit_price=8.0, is_weight=True),  # 0,8 zł/100 g vs 1,0 -> 0,8
        _p("b:2", unit_price=10.0, is_weight=True),  # 1.0
        _p("2", unit_price=12.0, is_weight=True),  # goły EAN: 1,2
        _p("b:9", unit_price=10.0, is_weight=True),  # Piece pomijany
        _p("b:77", unit_price=10.0, is_weight=True),  # brak pary
    ]
    factor = receipt_to_frisco_factor(purchases, frisco)
    assert factor.n == 3 and factor.value == pytest.approx(1.0)
    assert factor.by_durability == {}


def test_factor_without_pairs_is_neutral() -> None:
    assert receipt_to_frisco_factor([_p("b:5", unit_price=3.0, is_weight=True)], [_c("1")]) == Factor(
        1.0, 0, {}
    )


def test_factor_by_durability_only_with_30_pairs() -> None:
    frisco = [_c(str(i), 10.0) for i in range(31)] + [_c("900", 10.0)]
    purchases = [_p(f"b:{i}", unit_price=20.0, is_weight=True) for i in range(31)]
    purchases.append(_p("b:900", unit_price=10.0, is_weight=True))
    durability = {p.code: "dry" for p in purchases[:31]} | {"b:900": "fresh"}
    factor = receipt_to_frisco_factor(purchases, frisco, durability_by_code=durability)
    assert factor.n == 32
    assert factor.by_durability == {"dry": pytest.approx(2.0)}


def test_frisco_prices_median_times_factor() -> None:
    items = [_c("1", 10.0), _c("2", 20.0), _c("3", 30.0), _c("4", 99.0), _c("5", 5.0, unit="Piece")]
    mapped = {"1": "pomidory", "2": "pomidory", "3": "pomidory", "5": "jajka"}
    result = frisco_prices(items, mapped, Factor(0.5, 10, {"dry": 2.0}))
    assert result["pomidory"].price100 == pytest.approx(2.0 * 0.5)  # mediana 2,0 zł/100 g
    assert result["pomidory"].n_products == 3
    assert "jajka" not in result  # tylko sztuki
    dry = frisco_prices(items, mapped, Factor(0.5, 10, {"dry": 2.0}), {"pomidory": "dry"})
    assert dry["pomidory"].price100 == pytest.approx(4.0)
