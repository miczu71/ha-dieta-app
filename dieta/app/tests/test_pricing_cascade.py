from datetime import date, timedelta

import pytest

from dieta.pricing import FriscoPrice, cascade_prices
from dieta.zakupy_client import Purchase, ShelfItem

TODAY = date(2026, 11, 23)
WEEK_START = TODAY
WEEK_END = TODAY + timedelta(days=6)


def _p(
    code: str = "b:111",
    days_ago: int = 5,
    unit: float | None = 20.0,
    last_unit: float | None = None,
    weight: bool = True,
    names: tuple[str, ...] = ("Masło",),
) -> Purchase:
    last = TODAY - timedelta(days=days_ago)
    return Purchase(
        code, "biedronka", names[0], names, 1, 10.0, last, last, (last,), "food",
        unit_price=unit, is_weight=weight, last_unit_price=last_unit,
    )  # fmt: skip


def _s(
    ean: str = "111",
    price: float = 6.0,
    regular: float | None = 7.0,
    promo: bool = False,
    card_only: bool = False,
    promo_from: str | None = "2026-11-20",
    promo_to: str | None = "2026-11-25",
    fetched: str = "2026-11-22T03:00:00",
    size: float | None = 0.2,
    unit: str | None = "Kilogram",
    name: str | None = "Masło",
) -> ShelfItem:
    return ShelfItem(ean, name, size, unit, price, regular, promo_from, promo_to, promo, card_only, fetched)


def _run(
    purchases: list[Purchase] | None = None,
    shelf: list[ShelfItem] | None = None,
    frisco: dict[str, FriscoPrice] | None = None,
    mapping: dict[str, str] | None = None,
    shelf_map: dict[str, str] | None = None,
    today: date = TODAY,
):  # type: ignore[no-untyped-def]
    return cascade_prices(
        purchases or [],
        mapping if mapping is not None else {p.code: "masło" for p in purchases or []},
        {},
        frisco=frisco or {},
        shelf=shelf or [],
        today=today,
        week_start=WEEK_START,
        week_end=WEEK_END,
        shelf_map=shelf_map,
        frisco_as_of=date(2026, 11, 16),
    )


def test_a_fresh_receipt_uses_last_price() -> None:
    price = _run([_p(unit=30.0, last_unit=28.0)])["masło"]
    assert price.price100 == pytest.approx(2.8) and price.source == "receipt"
    assert price.as_of == TODAY - timedelta(days=5) and price.estimate is False


def test_promo_threshold_falls_back_to_median() -> None:
    # ostatni 2,00 zł/100 g (<0,8 × 3,00) → mediana 3,00
    assert _run([_p(unit=30.0, last_unit=20.0)])["masło"].price100 == pytest.approx(3.0)
    # dokładnie 0,8 × mediany zostaje ostatnią ceną
    assert _run([_p(unit=30.0, last_unit=24.0)])["masło"].price100 == pytest.approx(2.4)


def test_a_multiple_codes_median() -> None:
    purchases = [_p("b:1", unit=10.0), _p("b:2", unit=20.0), _p("b:3", unit=60.0)]
    assert _run(purchases, mapping={"b:1": "masło", "b:2": "masło", "b:3": "masło"})["masło"].price100 == 2.0


def test_b_shelf_regular_price_when_no_fresh_receipt() -> None:
    price = _run(shelf=[_s()], shelf_map={"111": "masło"})["masło"]
    assert price.price100 == pytest.approx(3.5) and price.source == "shelf"  # regular 7,00 / 200 g
    assert price.as_of == date(2026, 11, 22)


def test_b_shelf_via_purchase_code_b_ean() -> None:
    price = _run([_p(days_ago=60)], shelf=[_s()])["masło"]
    assert price.source == "shelf"


def test_b_promo_only_when_window_intersects_week_and_not_card_only() -> None:
    m = {"111": "masło"}
    promo = _run(shelf=[_s(promo=True)], shelf_map=m)["masło"]
    assert promo.source == "promo" and promo.price100 == pytest.approx(3.0) and promo.promo_to == "2026-11-25"
    after = _s(promo=True, promo_from="2026-11-30", promo_to="2026-12-05")
    assert _run(shelf=[after], shelf_map=m)["masło"].source == "shelf"
    before = _s(promo=True, promo_from="2026-11-10", promo_to="2026-11-22")
    assert _run(shelf=[before], shelf_map=m)["masło"].price100 == pytest.approx(3.5)
    card = _s(promo=True, card_only=True)
    assert _run(shelf=[card], shelf_map=m)["masło"].source == "shelf"


def test_b_lowest_across_eans_and_size_from_name() -> None:
    items = [_s("111", regular=7.0), _s("222", regular=5.0, size=None, unit=None, name="Masło 250g")]
    price = _run(shelf=items, shelf_map={"111": "masło", "222": "masło"})["masło"]
    assert price.price100 == pytest.approx(2.0)  # 5,00 / 250 g


def test_b_missing_grammage_skipped() -> None:
    item = _s(size=None, unit=None, name="Masło")
    assert _run(shelf=[item], shelf_map={"111": "masło"}) == {}


def test_b_cache_older_than_7_days_ignored() -> None:
    old = _s(fetched="2026-11-15T03:00:00")  # 8 dni
    assert _run(shelf=[old], shelf_map={"111": "masło"}) == {}
    edge = _s(fetched="2026-11-16T03:00:00")  # 7 dni
    assert _run(shelf=[edge], shelf_map={"111": "masło"})["masło"].source == "shelf"


def test_c_older_receipt_uses_median_not_last() -> None:
    price = _run([_p(days_ago=90, unit=30.0, last_unit=10.0)])["masło"]
    assert price.source == "receipt_old" and price.price100 == pytest.approx(3.0)
    assert price.as_of == TODAY - timedelta(days=90)


def test_d_frisco_is_estimate() -> None:
    price = _run(frisco={"masło": FriscoPrice(4.2, 3)})["masło"]
    assert price.source == "frisco" and price.estimate is True and price.price100 == 4.2
    assert price.as_of == date(2026, 11, 16)


def test_d_ancient_receipt_only_without_frisco() -> None:
    old = _p(days_ago=300, unit=30.0)
    assert _run([old])["masło"].source == "receipt_old"
    assert _run([old], frisco={"masło": FriscoPrice(4.2, 3)})["masło"].source == "frisco"


def test_cascade_order_a_b_c_d_on_one_ingredient() -> None:
    fresh, old = _p("b:111", days_ago=3, unit=30.0), _p("b:222", days_ago=90, unit=50.0)
    mapping = {"b:111": "masło", "b:222": "masło"}
    shelf = [_s()]
    frisco = {"masło": FriscoPrice(9.0, 1)}
    assert _run([fresh, old], shelf, frisco, mapping)["masło"].source == "receipt"
    assert _run([old], shelf, frisco, {"b:222": "masło"}, {"111": "masło"})["masło"].source == "shelf"
    assert _run([old], [], frisco, {"b:222": "masło"})["masło"].source == "receipt_old"
    assert _run([], [], frisco)["masło"].source == "frisco"


def test_pack_g_kept_from_receipts() -> None:
    packed = _p(days_ago=60, unit=3.2, weight=False, names=("Ryż biały 400g",))
    price = _run([packed], frisco={"ryż": FriscoPrice(1.0, 1)}, mapping={"b:111": "ryż"})["ryż"]
    assert price.source == "receipt_old" and price.pack_g == 400.0
