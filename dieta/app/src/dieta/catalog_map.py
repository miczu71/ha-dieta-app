"""Mapowanie produktów katalogu Frisco na składniki Diety (czyste funkcje + wstrzykiwany ``ask``).

Ścisłe dopasowanie nazwy = ``rule``; luźne (produkt „zawiera” składnik) idzie do LLM albo nie dostaje wpisu.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Literal

from dieta.dictionary import BATCH_SIZE, AskFn, normalize_name
from dieta.zakupy_client import CatalogItem

STEM_LEN = 5
_TOKEN_RE = re.compile(r"[a-ząćęłńóśźż]+")
_NON_FOOD_WORDS = frozenset({"wino", "piwo", "wódka", "whisky", "szampon", "żel", "płyn", "karma"})
_NON_FOOD_PREFIXES = ("pieluch", "szampon", "wódk", "whisk")
_SKIP_INGREDIENT_PREFIXES = ("sól", "woda", "pieprz")

CatalogMatchStatus = Literal["rule", "ai", "pending", "excluded"]

ASK_PROMPT = (
    "Dostajesz linie w formacie „numer. nazwa produktu || składnik”. Dla każdej oceń, czy produkt jest "
    "zwykłym (podstawowym) składnikiem kulinarnym o podanej nazwie, a nie daniem gotowym, przetworem, "
    "przekąską ani czymś tylko zawierającym ten składnik. Zwróć obiekt JSON "
    '{"i": numer, "ok": "tak" | "nie" | "watpliwe"}. Odpowiedz WYŁĄCZNIE tablicą JSON, bez komentarza.'
)


@dataclass(frozen=True)
class CatalogDecision:
    ean: str
    status: CatalogMatchStatus
    ingredient: str | None
    source_name: str


def _tokens(name: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_name(name))


def _stems(name: str) -> frozenset[str]:
    return frozenset(t[:STEM_LEN] for t in _tokens(name) if len(t) >= 3)


def is_candidate_product(item: CatalogItem) -> bool:
    """Filtr wstępny: waga/objętość znana, cena dodatnia, nie non-food/alkohol."""
    if item.size_unit is None or item.size_unit == "Piece":
        return False
    if item.size is None or item.size <= 0 or item.price <= 0:
        return False
    tokens = _tokens(item.name)
    return not any(t in _NON_FOOD_WORDS or t.startswith(_NON_FOOD_PREFIXES) for t in tokens)


@dataclass(frozen=True)
class _Match:
    ingredient: str
    strict: bool
    stems: int


def _best_match(name: str, ingredient_stems: dict[str, frozenset[str]]) -> _Match | None:
    tokens = _tokens(name)
    product_stems = {t[:STEM_LEN] for t in tokens}
    best: _Match | None = None
    for ingredient, stems in ingredient_stems.items():
        if not stems <= product_stems:
            continue
        cand = _Match(ingredient, len(tokens) <= len(stems) + 1, len(stems))
        if best is None or (cand.strict, cand.stems) > (best.strict, best.stems):
            best = cand
    return best


def _parse_verdicts(reply: list[dict[str, object]] | None, count: int) -> dict[int, str]:
    verdicts: dict[int, str] = {}
    for row in reply or []:
        index, ok = row.get("i"), row.get("ok")
        if isinstance(index, int) and 0 <= index < count and ok in ("tak", "nie", "watpliwe"):
            verdicts[index] = ok
    return verdicts


def _prompt_safe(name: str) -> str:
    """Nazwa z cudzego API do jednej linii promptu: bez znaków sterujących i ``||``, ≤120 znaków."""
    cleaned = "".join(" " if ch.isspace() else ch for ch in name if ch.isprintable() or ch.isspace())
    return " ".join(cleaned.replace("||", " ").split())[:120].rstrip()


def map_catalog(
    items: Sequence[CatalogItem],
    ingredients: Collection[str],
    ask: AskFn | None,
) -> list[CatalogDecision]:
    """Decyzje dla podanych produktów (wołający pomija EAN, które mają już wpis).

    Bez ``ask`` luźni kandydaci nie dostają wpisu; odpowiedź ``None`` albo brak oceny → brak wpisu.
    """
    ingredient_stems = {
        name: _stems(name)
        for name in sorted({normalize_name(n) for n in ingredients})
        if name and not any(t.startswith(_SKIP_INGREDIENT_PREFIXES) for t in _tokens(name))
    }
    ingredient_stems = {n: s for n, s in ingredient_stems.items() if s}
    decisions: list[CatalogDecision] = []
    loose: list[tuple[CatalogItem, str]] = []
    for item in items:
        if not is_candidate_product(item):
            continue
        match = _best_match(item.name, ingredient_stems)
        if match is None:
            continue
        if match.strict:
            decisions.append(CatalogDecision(item.ean, "rule", match.ingredient, normalize_name(item.name)))
        else:
            loose.append((item, match.ingredient))

    if ask is None:
        return decisions
    status_of: dict[str, CatalogMatchStatus] = {"tak": "ai", "nie": "excluded", "watpliwe": "pending"}
    for start in range(0, len(loose), BATCH_SIZE):
        batch = loose[start : start + BATCH_SIZE]
        reply = ask([f"{_prompt_safe(item.name)} || {ingredient}" for item, ingredient in batch])
        verdicts = _parse_verdicts(reply, len(batch))
        for offset, (item, ingredient) in enumerate(batch):
            verdict = verdicts.get(offset)
            if verdict is None:
                continue
            status = status_of[verdict]
            decisions.append(
                CatalogDecision(
                    item.ean,
                    status,
                    None if status == "excluded" else ingredient,
                    normalize_name(item.name),
                )
            )
    return decisions
