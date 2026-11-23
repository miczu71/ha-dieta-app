"""Ceny składników z paragonów (zł za 100 g). Czyste funkcje, bez bazy i HTTP.

Cena kodu zakupu: ``unit_price`` z Zakupów to zł/kg (``is_weight``) albo zł/szt. (pakowany).
Pakowany przeliczamy po gramaturze z nazwy, a gdy jej brak, po masie sztuki składnika.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from statistics import median

from dieta.zakupy_client import CatalogItem, Purchase, ShelfItem

# opcjonalny mnożnik „6x", liczba, jednostka; lookbehind odcina liczby sklejone z poprzednim tokenem
_PACK_RE = re.compile(
    r"(?<![\w.,])(?:(\d+)\s*[x×]\s*)?(\d+(?:[.,]\d+)?)\s*(kg|g|ml|l)\b",
    re.IGNORECASE,
)
# „10 szt.", „6 sztuk": opakowanie wielosztukowe — masa jednej sztuki nie jest masą opakowania
_COUNT_RE = re.compile(r"(?<![\w.,])(\d+)\s*(?:szt|sztuk)\b", re.IGNORECASE)
_UNIT_G = {"kg": 1000.0, "g": 1.0, "l": 1000.0, "ml": 1.0}  # litr/mililitr → gramy przy gęstości 1


@dataclass(frozen=True)
class IngredientPrice:
    price100: float  # zł za 100 g
    pack_g: float | None  # typowe opakowanie (mediana gramatur z nazw), gdy znane
    codes: tuple[str, ...]  # kody zakupu, z których policzono cenę
    source: str = "receipt"  # receipt | receipt_old | shelf | promo | frisco
    as_of: date | None = None  # data ceny (zakupu, pobrania cache albo katalogu)
    promo_to: str | None = None  # koniec promocji (source == "promo")
    estimate: bool = False  # cena przybliżona (Frisco × współczynnik)


def _pack_from_name(name: str) -> float | None:
    match = _PACK_RE.search(name)
    if match is None:
        return None
    count = float(match.group(1)) if match.group(1) else 1.0
    grams = count * float(match.group(2).replace(",", ".")) * _UNIT_G[match.group(3).lower()]
    return grams if grams > 0 else None


def parse_pack_g(names: Sequence[str]) -> float | None:
    """Gramatura opakowania z nazw produktu (mediana z nazw, w których ją znaleziono)."""
    found = [g for g in (_pack_from_name(n) for n in names) if g is not None]
    return float(median(found)) if found else None


def price_per_100g(purchase: Purchase, piece_g: float | None) -> float | None:
    """Cena za 100 g dla kodu zakupu albo ``None``, gdy nie da się jej uczciwie policzyć."""
    price = purchase.unit_price
    if price is None or price <= 0:
        return None
    if purchase.is_weight:
        return price / 10
    pack = parse_pack_g(purchase.names)
    if pack is not None:
        return price / pack * 100
    multi_piece = any(int(m.group(1)) > 1 for n in purchase.names if (m := _COUNT_RE.search(n)))
    if piece_g is not None and piece_g > 0 and not multi_piece:
        return price / piece_g * 100
    return None


def ingredient_prices(
    purchases: Sequence[Purchase],
    mapping: Mapping[str, str],
    piece_g_by_ingredient: Mapping[str, float],
) -> dict[str, IngredientPrice]:
    """Cena składnika = mediana cen kodów ważona liczbą zakupów.

    ``mapping``: code → nazwa składnika (tylko zmapowane rule/seed/ai/approved); kody spoza mapowania
    i kody bez ceny są pomijane, składnik bez żadnej ceny nie trafia do wyniku.
    """
    prices: dict[str, list[float]] = {}
    packs: dict[str, list[float]] = {}
    codes: dict[str, list[str]] = {}
    for purchase in sorted(purchases, key=lambda p: p.code):
        ingredient = mapping.get(purchase.code)
        if ingredient is None:
            continue
        price100 = price_per_100g(purchase, piece_g_by_ingredient.get(ingredient))
        if price100 is None:
            continue
        prices.setdefault(ingredient, []).extend([price100] * max(1, purchase.purchases))
        codes.setdefault(ingredient, []).append(purchase.code)
        if not purchase.is_weight:
            pack = parse_pack_g(purchase.names)
            if pack is not None:
                packs.setdefault(ingredient, []).append(pack)
    return {
        ingredient: IngredientPrice(
            price100=float(median(values)),
            pack_g=float(median(packs[ingredient])) if ingredient in packs else None,
            codes=tuple(codes[ingredient]),
        )
        for ingredient, values in prices.items()
    }


MIN_GROUP_PAIRS = 30  # współczynnik per trwałość tylko przy tylu parach w grupie


@dataclass(frozen=True)
class Factor:
    """Współczynnik cena z paragonu / cena Frisco: ``value`` globalny, ``by_durability`` dla grup n>=30."""

    value: float
    n: int
    by_durability: dict[str, float]


@dataclass(frozen=True)
class FriscoPrice:
    price100: float  # zł za 100 g, po współczynniku (przybliżona cena „z paragonu")
    n_products: int


def frisco_price100(item: CatalogItem) -> float | None:
    """zł/100 g produktu Frisco (rozmiar w kg/l); ``None`` dla sztuk i braków."""
    if item.size_unit not in ("Kilogram", "Litre") or item.size is None or item.size <= 0 or item.price <= 0:
        return None
    return item.price / (item.size * 1000) * 100


def receipt_to_frisco_factor(
    purchases: Sequence[Purchase],
    frisco: Sequence[CatalogItem],
    piece_g_by_ingredient: Mapping[str, float] | None = None,
    *,
    durability_by_code: Mapping[str, str] | None = None,
    ingredient_by_code: Mapping[str, str] | None = None,
) -> Factor:
    """Mediana ilorazu (cena z paragonu / cena Frisco) po parach kod zakupu ↔ EAN (``code.split(":",1)[-1]``).

    ``piece_g_by_ingredient`` + ``ingredient_by_code`` pozwalają policzyć cenę pakowanych bez gramatury.
    """
    by_ean = {i.ean: p for i in frisco if (p := frisco_price100(i)) is not None}
    piece_g = piece_g_by_ingredient or {}
    ratios: list[float] = []
    groups: dict[str, list[float]] = {}
    for purchase in purchases:
        frisco100 = by_ean.get(purchase.code.split(":", 1)[-1])
        if frisco100 is None:
            continue
        ingredient = (ingredient_by_code or {}).get(purchase.code)
        receipt100 = price_per_100g(purchase, piece_g.get(ingredient) if ingredient else None)
        if receipt100 is None:
            continue
        ratio = receipt100 / frisco100
        ratios.append(ratio)
        durability = (durability_by_code or {}).get(purchase.code)
        if durability:
            groups.setdefault(durability, []).append(ratio)
    if not ratios:
        return Factor(1.0, 0, {})
    return Factor(
        float(median(ratios)),
        len(ratios),
        {d: float(median(v)) for d, v in groups.items() if len(v) >= MIN_GROUP_PAIRS},
    )


def frisco_prices(
    items: Sequence[CatalogItem],
    mapped: Mapping[str, str],
    factor: Factor,
    durability_by_ingredient: Mapping[str, str] | None = None,
) -> dict[str, FriscoPrice]:
    """Cena składnika = mediana zł/100 g zmapowanych produktów × współczynnik (trwałości albo globalny).

    ``mapped``: EAN → nazwa składnika (tylko rule/ai/approved); składnik bez produktów nie trafia do wyniku.
    """
    per_ingredient: dict[str, list[float]] = {}
    for item in items:
        ingredient = mapped.get(item.ean)
        price100 = frisco_price100(item)
        if ingredient is None or price100 is None:
            continue
        per_ingredient.setdefault(ingredient, []).append(price100)
    result: dict[str, FriscoPrice] = {}
    for ingredient, values in per_ingredient.items():
        durability = (durability_by_ingredient or {}).get(ingredient)
        multiplier = factor.by_durability.get(durability, factor.value) if durability else factor.value
        result[ingredient] = FriscoPrice(float(median(values)) * multiplier, len(values))
    return result


RECEIPT_FRESH_DAYS = 30
RECEIPT_MAX_DAYS = 180
SHELF_FRESH_DAYS = 7
PROMO_RATIO = 0.8  # ostatnia cena < 0,8 × mediana = promocja/kupon, nie cena typowa
_SHELF_KG_UNITS = ("Kilogram", "Litre")


def _fresh_receipt100(purchase: Purchase, piece_g: float | None) -> float | None:
    """Świeża cena kodu: ostatnia płacona, chyba że to promocja (poniżej 0,8 × mediany)."""
    last, typical = purchase.last_unit_price, purchase.unit_price
    if last is not None and last > 0 and (typical is None or last >= PROMO_RATIO * typical):
        return price_per_100g(replace(purchase, unit_price=last), piece_g)
    return price_per_100g(purchase, piece_g)


def _shelf_price100(item: ShelfItem, price: float) -> float | None:
    if item.size_unit in _SHELF_KG_UNITS and item.size is not None and item.size > 0:
        return price / (item.size * 1000) * 100
    pack = parse_pack_g([item.name]) if item.name else None
    return price / pack * 100 if pack else None


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value[:10]) if value else None
    except ValueError:
        return None


def _promo_applies(item: ShelfItem, week_start: date, week_end: date) -> bool:
    if not item.is_promotion or item.card_only:
        return False
    start, end = _day(item.promo_from), _day(item.promo_to)
    return (start is None or start <= week_end) and (end is None or end >= week_start)


def _shelf_prices(
    shelf: Sequence[ShelfItem],
    ean_to_ingredient: Mapping[str, str],
    today: date,
    week_start: date,
    week_end: date,
) -> dict[str, IngredientPrice]:
    best: dict[str, IngredientPrice] = {}
    for item in shelf:
        ingredient = ean_to_ingredient.get(item.ean)
        fetched = _day(item.fetched_at)
        if ingredient is None or fetched is None or fetched < today - timedelta(days=SHELF_FRESH_DAYS):
            continue
        promo = _promo_applies(item, week_start, week_end)
        price100 = _shelf_price100(item, item.price if promo else (item.regular or item.price))
        if price100 is None:
            continue
        current = best.get(ingredient)
        if current is None or price100 < current.price100:
            best[ingredient] = IngredientPrice(
                price100,
                None,
                (f"b:{item.ean}",),
                source="promo" if promo else "shelf",
                as_of=fetched,
                promo_to=item.promo_to if promo else None,
            )
    return best


def cascade_prices(
    purchases: Sequence[Purchase],
    mapping: Mapping[str, str],
    piece_g_by_ingredient: Mapping[str, float],
    *,
    frisco: Mapping[str, FriscoPrice],
    shelf: Sequence[ShelfItem],
    today: date,
    week_start: date,
    week_end: date,
    shelf_map: Mapping[str, str] | None = None,
    frisco_as_of: date | None = None,
) -> dict[str, IngredientPrice]:
    """Cena składnika kaskadą: paragon ≤30 dni → Biedronka (cache ≤7 dni) → paragon ≤180 dni → Frisco ≈.

    ``mapping``: kod zakupu → składnik; ``shelf_map``: EAN → składnik (``catalog_map``); EAN Biedronki bez
    wpisu w ``shelf_map`` bierze składnik z kodu zakupu ``b:{ean}``. Gdy brak Frisco, a jest starszy paragon
    (>180 dni), zwraca go jako ``receipt_old``. ``pack_g`` zawsze z paragonów, gdy znane.
    """
    base = ingredient_prices(purchases, mapping, piece_g_by_ingredient)
    fresh: dict[str, list[tuple[float, date]]] = {}
    for purchase in sorted(purchases, key=lambda p: p.code):
        ingredient = mapping.get(purchase.code)
        if ingredient is None or purchase.last < today - timedelta(days=RECEIPT_FRESH_DAYS):
            continue
        price100 = _fresh_receipt100(purchase, piece_g_by_ingredient.get(ingredient))
        if price100 is not None:
            fresh.setdefault(ingredient, []).append((price100, purchase.last))

    def _old(low: date | None, high: date | None) -> dict[str, IngredientPrice]:
        subset = [p for p in purchases if (low is None or p.last >= low) and (high is None or p.last <= high)]
        found = ingredient_prices(subset, mapping, piece_g_by_ingredient)
        result: dict[str, IngredientPrice] = {}
        for ingredient, price in found.items():
            latest = max(p.last for p in subset if p.code in price.codes)
            result[ingredient] = replace(price, source="receipt_old", as_of=latest)
        return result

    ean_map = {code.split(":", 1)[1]: ing for code, ing in mapping.items() if code.startswith("b:")}
    ean_map.update(shelf_map or {})
    shelf_best = _shelf_prices(shelf, ean_map, today, week_start, week_end)
    mid = _old(today - timedelta(days=RECEIPT_MAX_DAYS), today - timedelta(days=RECEIPT_FRESH_DAYS + 1))
    ancient = _old(None, today - timedelta(days=RECEIPT_MAX_DAYS + 1))

    result: dict[str, IngredientPrice] = {}
    for ingredient in {*base, *fresh, *shelf_best, *mid, *frisco, *ancient}:
        chosen: IngredientPrice | None = None
        if ingredient in fresh:
            values = fresh[ingredient]
            chosen = IngredientPrice(
                float(median(v for v, _ in values)),
                None,
                tuple(base[ingredient].codes) if ingredient in base else (),
                source="receipt",
                as_of=max(d for _, d in values),
            )
        elif ingredient in shelf_best:
            chosen = shelf_best[ingredient]
        elif ingredient in mid:
            chosen = mid[ingredient]
        elif ingredient in frisco:
            chosen = IngredientPrice(
                frisco[ingredient].price100,
                None,
                (),
                source="frisco",
                as_of=frisco_as_of,
                estimate=True,
            )
        elif ingredient in ancient:
            chosen = ancient[ingredient]
        if chosen is None:
            continue
        pack_g = base[ingredient].pack_g if ingredient in base else None
        result[ingredient] = replace(chosen, pack_g=pack_g)
    return result
