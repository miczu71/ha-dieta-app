"""Słownik składników: normalizacja nazw, reguły słów kluczowych, seed z E0 i mapowanie przez LLM.

Nic tu nie zapisuje do bazy. Klucze API nie trafiają do logów ani do komunikatów wyjątków.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from dieta.http_limit import read_limited

if TYPE_CHECKING:
    from dieta.settings import Settings

Durability = Literal["fresh", "chilled", "dry", "frozen"]
DURABILITIES: tuple[Durability, ...] = ("fresh", "chilled", "dry", "frozen")

SEED_PATH = Path(__file__).resolve().parent / "seed" / "seed_slownik.json"
BATCH_SIZE = 25
LLM_MODEL = "auto:text"
LLM_TIMEOUT_S = 180
LLM_MAX_BYTES = 1_000_000
CLASSES = ("skladnik", "gotowe", "alkohol", "slodycze_przekaski", "napoj", "non-food")

_log = logging.getLogger(__name__)

# Kolejność ma znaczenie: pierwsze trafienie wygrywa. Dopasowanie po podciągu znormalizowanej nazwy.
_RULES: tuple[tuple[str, str | None, Durability | None], ...] = (
    ("pierś z indyka", "pierś z indyka", "fresh"),
    ("indyk", "pierś z indyka", "fresh"),
    ("kurczak", "filet z kurczaka", "fresh"),
    ("łosoś", "łosoś", "fresh"),
    ("mięso mielone", "mięso mielone", "fresh"),
    ("pomidor", "pomidory", "fresh"),
    ("banan", "banany", "fresh"),
    ("jabłk", "jabłka", "fresh"),
    ("cytryn", "cytryny", "fresh"),
    ("ogórek", "ogórki", "fresh"),
    ("mleko", "mleko", "chilled"),
    ("jogurt", "jogurt naturalny", "chilled"),
    ("twaróg", "twaróg", "chilled"),
    ("jajk", "jajka", "chilled"),
    ("masło", "masło", "chilled"),
    ("ziemniak", "ziemniaki", "dry"),
    ("cebul", "cebula", "dry"),
    ("makaron", "makaron", "dry"),
    ("ryż", "ryż", "dry"),
    ("kasza", "kasza", "dry"),
    ("mąka", "mąka pszenna", "dry"),
    ("olej", "olej", "dry"),
    ("mrożon", None, "frozen"),
)


def normalize_name(name: str) -> str:
    return " ".join(name.lower().split())


def classify_rules(name: str) -> tuple[str | None, Durability | None]:
    """(składnik ogólny, trwałość) z reguł słów kluczowych; (None, None) gdy brak dopasowania."""
    norm = normalize_name(name)
    for keyword, ingredient, durability in _RULES:
        if keyword in norm:
            return ingredient, durability
    return None, None


@dataclass(frozen=True)
class SeedEntry:
    name: str
    klasa: str
    skladnik: str
    n: int


def load_seed(path: Path) -> dict[str, SeedEntry]:
    """Seed z E0 po znormalizowanej nazwie. Przy duplikacie nazwy wygrywa wpis z większą liczbą zakupów."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    seed: dict[str, SeedEntry] = {}
    for row in raw:
        entry = SeedEntry(
            name=str(row["name"]),
            klasa=str(row.get("klasa") or ""),
            skladnik=normalize_name(str(row.get("skladnik") or "")),
            n=int(row.get("n") or 0),
        )
        key = normalize_name(entry.name)
        current = seed.get(key)
        if current is None or entry.n > current.n:
            seed[key] = entry
    return seed


@dataclass(frozen=True)
class LlmMapping:
    name: str
    klasa: str | None
    ingredient: str | None
    durability: Durability | None
    answered: bool = True  # False = awaria LLM dla paczki (nie to samo co odpowiedź bez mapowania)


AskFn = Callable[[list[str]], list[dict[str, object]] | None]

PROMPT = (
    "Dostajesz listę nazw produktów z paragonów polskich sklepów (skróty!). Dla każdej zwróć obiekt JSON: "
    '{"i": numer, "klasa": jedna z '
    + str(list(CLASSES))
    + ', "skladnik": ogólna polska nazwa składnika kulinarnego '
    '(np. "pierś z indyka", "pomidory krojone", "mleko", "makaron penne"), '
    "pusty string gdy klasa != skladnik, "
    '"trwalosc": jedna z "fresh" (świeże, ok. 4 dni: mięso, ryby, owoce, warzywa), '
    '"chilled" (chłodnia, ok. 10 dni: '
    'nabiał, wędliny, jaja), "dry" (suche, ok. 30 dni: kasze, makarony, mąka, przyprawy, konserwy, oleje), '
    '"frozen" (mrożonki), pusty string gdy klasa != skladnik}. '
    "klasa=skladnik to produkt używany do gotowania (mięso, ryby, warzywa, owoce, nabiał, jaja, mąka, kasze, "
    "makarony, przyprawy, oleje, puszki, mrożonki); gotowe = dania/wyroby gotowe do jedzenia; "
    "non-food = nie-jedzenie. Odpowiedz WYŁĄCZNIE tablicą JSON, bez komentarza."
)


def _parse_durability(value: object) -> Durability | None:
    if isinstance(value, str) and value in DURABILITIES:
        return value
    return None


def map_with_llm(names: list[str], *, ask: AskFn) -> list[LlmMapping]:
    """Mapuje nazwy w paczkach po 25.

    Przy braku odpowiedzi jedna ponowna próba, potem pozycje bez mapowania i z ``answered=False``.
    """
    results: list[LlmMapping] = []
    for start in range(0, len(names), BATCH_SIZE):
        batch = names[start : start + BATCH_SIZE]
        safe = [prompt_safe(n) for n in batch]
        reply = ask(safe)
        if reply is None:
            reply = ask(safe)
        by_index: dict[int, dict[str, object]] = {}
        for item in reply or []:
            if isinstance(item, dict) and isinstance(item.get("i"), int):
                by_index[cast(int, item["i"])] = item
        for offset, name in enumerate(batch):
            row = by_index.get(offset)
            klasa: str | None = None
            ingredient: str | None = None
            durability: Durability | None = None
            if row is not None:
                raw_klasa = row.get("klasa")
                klasa = raw_klasa if isinstance(raw_klasa, str) and raw_klasa in CLASSES else None
                raw_ingredient = row.get("skladnik")
                if klasa == "skladnik" and isinstance(raw_ingredient, str) and raw_ingredient.strip():
                    ingredient = normalize_name(raw_ingredient)
                    durability = _parse_durability(row.get("trwalosc"))
            results.append(
                LlmMapping(
                    name=name,
                    klasa=klasa,
                    ingredient=ingredient,
                    durability=durability,
                    answered=reply is not None,
                )
            )
    return results


def prompt_safe(name: str) -> str:
    """Nazwa z cudzego API do jednej linii promptu: bez znaków sterujących i ``||``, ≤120 znaków."""
    cleaned = "".join(ch for ch in name if ch.isprintable() or ch.isspace())
    return " ".join(cleaned.replace("||", " ").split())[:120].rstrip()


def make_llm_ask(
    url: str, key: str, prompt: str = PROMPT
) -> Callable[[list[str]], list[dict[str, object]] | None]:
    """Zwraca `ask(names)` wołające {url}/v1/chat/completions.

    Błąd → None; treść i nagłówki nie trafiają do logu.
    """
    endpoint = f"{url.rstrip('/')}/v1/chat/completions"

    def ask(names: list[str]) -> list[dict[str, object]] | None:
        body = {
            "model": LLM_MODEL,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "\n".join(f"{i}. {n}" for i, n in enumerate(names))},
            ],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_S) as resp:
                payload = json.loads(read_limited(resp, LLM_MAX_BYTES))
            text = str(payload["choices"][0]["message"]["content"]).strip()
            data = json.loads(text[text.index("[") : text.rindex("]") + 1])
        except urllib.error.HTTPError as exc:
            _log.warning("LLM: błąd HTTP %s (%s)", exc.code, str(exc.reason)[:80])
            return None
        except Exception as exc:
            _log.warning("LLM: błąd paczki (%s)", type(exc).__name__)
            return None
        if not isinstance(data, list):
            return None
        return [row for row in data if isinstance(row, dict)]

    return ask


def llm_ask_from(settings: Settings, prompt: str = PROMPT) -> AskFn | None:
    """`ask` z opcji add-onu albo None, gdy LLM nie jest skonfigurowany (brak url/klucza)."""
    if not (settings.llm_url and settings.llm_key):
        return None
    return make_llm_ask(settings.llm_url, settings.llm_key, prompt)
