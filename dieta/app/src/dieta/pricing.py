"""Ceny składników z paragonów (zł za 100 g). Czyste funkcje, bez bazy i HTTP.

Cena kodu zakupu: ``unit_price`` z Zakupów to zł/kg (``is_weight``) albo zł/szt. (pakowany).
Pakowany przeliczamy po gramaturze z nazwy, a gdy jej brak, po masie sztuki składnika.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from dieta.zakupy_client import Purchase

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
