"""Koszt planu i lista zakupów z zatwierdzonego planu tygodnia (bez HTTP i bez zapisu do bazy).

Masa linii przepisu = ``estimate.to_grams`` (albo ``grams_assumed``) × skala sesji, gdzie
skala = suma porcji sesji (osoba 1 + osoba 2) / porcje przepisu. Cena składnika z ``pricing``.
Linie bez ``ingredient_id`` pomijamy (brak klucza dla ceny i stanu listy).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Literal

from dieta.estimate import (
    Line,
    Measures,
    NutritionSeed,
    Per100,
    estimate_recipe,
    is_free,
    is_noise,
    load_measures,
    load_nutrition_seed,
    to_grams,
)
from dieta.match import DEFAULT_BASICS
from dieta.pricing import IngredientPrice, ingredient_prices
from dieta.settings import Settings
from dieta.store import PlanMeal, PlanSession, Store
from dieta.sync import stats

Notice = Literal["ok", "no_plan", "not_approved", "past_week", "no_prices"]
WEEK_DAYS = 7
PRICED_MAPPING_STATUSES = ("rule", "seed", "ai", "approved")


@dataclass(frozen=True)
class ShoppingLine:
    ingredient_id: int
    name: str
    grams: float | None  # None = ilość nieznana
    pieces: float | None  # ≈ sztuk, gdy znana masa sztuki
    pack_g: float | None  # typowe opakowanie z paragonów
    packs: int | None  # ≈ liczba opakowań (w górę)
    cost: float | None
    priced: bool
    at_home: bool


@dataclass(frozen=True)
class ShoppingList:
    week_start: date
    notice: Notice
    to_buy: tuple[ShoppingLine, ...]
    at_home: tuple[ShoppingLine, ...]
    unpriced: tuple[ShoppingLine, ...]
    cost_total: float
    coverage: float


@dataclass(frozen=True)
class SessionCost:
    session_id: int
    recipe_id: int
    title: str
    cost: float
    per_portion: float
    coverage: float


@dataclass(frozen=True)
class PlanCost:
    total: float
    coverage: float
    sessions: tuple[SessionCost, ...]


@dataclass(frozen=True)
class _Mass:
    ingredient_id: int
    name: str
    grams: float | None


@dataclass(frozen=True)
class _SessionMasses:
    session: PlanSession
    title: str
    portions: float
    masses: tuple[_Mass, ...]


@dataclass(frozen=True)
class _Context:
    measures: Measures
    per100: dict[int, Per100]
    prices: dict[str, IngredientPrice]


@lru_cache(maxsize=1)
def _measures() -> Measures:
    return load_measures()


@lru_cache(maxsize=1)
def _seed() -> NutritionSeed:
    return load_nutrition_seed()


def _context(store: Store) -> _Context:
    seed = _seed()
    per100: dict[int, Per100] = {}
    piece_by_name: dict[str, float] = {}
    for ingredient_id, row in store.ingredient_nutrition().items():
        found = seed.find(row.name)
        extra = found[1] if found is not None else None
        piece_g = row.piece_g if row.piece_g is not None else (extra.piece_g if extra else None)
        per100[ingredient_id] = Per100(
            kcal=row.kcal or 0.0,
            protein=row.protein or 0.0,
            carbs=row.carbs or 0.0,
            fat=row.fat or 0.0,
            piece_g=piece_g,
            density_g_ml=extra.density_g_ml if extra else None,
            unit_g=extra.unit_g if extra else {},
        )
        if piece_g:
            piece_by_name[row.name] = piece_g
    mapping = {
        m.code: m.ingredient
        for m in store.mappings()
        if m.status in PRICED_MAPPING_STATUSES and m.ingredient is not None
    }
    return _Context(_measures(), per100, ingredient_prices(store.purchases(), mapping, piece_by_name))


def _session_masses(
    store: Store, ctx: _Context, session: PlanSession, meals: list[PlanMeal]
) -> _SessionMasses | None:
    recipe = store.recipe(session.recipe_id, {}, DEFAULT_BASICS)
    if recipe is None:
        return None
    portions = sum(
        (m.p1_portions or 0.0) + (m.p2_portions or 0.0) for m in meals if m.session_id == session.id
    )
    stored = store.recipe_lines(session.recipe_id)
    servings = recipe.servings
    if servings is None or servings <= 0:
        est_lines = [
            Line(
                name=ln.ingredient_name or ln.name,
                amount=ln.amount,
                unit=ln.unit,
                ingredient_id=ln.ingredient_id,
                grams_assumed=ln.grams_assumed,
                text=ln.text,
            )
            for ln in stored
        ]
        servings = estimate_recipe(est_lines, ctx.per100, None, measures=ctx.measures).servings
    scale = portions / servings
    masses: list[_Mass] = []
    for ln in stored:
        name = ln.ingredient_name or ln.name
        if ln.ingredient_id is None or is_free(name, ctx.measures):
            continue
        if ln.amount is None and is_noise(name, ln.text, ctx.measures):
            continue
        per = ctx.per100.get(ln.ingredient_id)
        grams, _ = to_grams(
            ln.amount,
            ln.unit,
            measures=ctx.measures,
            piece_g=per.piece_g if per else None,
            density_g_ml=per.density_g_ml if per else None,
            unit_g=per.unit_g if per else None,
        )
        if grams is None:
            grams = ln.grams_assumed
        masses.append(_Mass(ln.ingredient_id, name, None if grams is None else grams * scale))
    return _SessionMasses(session, recipe.title, portions, tuple(masses))


def _all_masses(
    store: Store, ctx: _Context, plan_id: int, min_start_day: int | None = None
) -> list[_SessionMasses]:
    meals = store.plan_meals(plan_id)
    result: list[_SessionMasses] = []
    for session in store.plan_sessions(plan_id):
        if min_start_day is not None and session.start_day < min_start_day:
            continue
        item = _session_masses(store, ctx, session, meals)
        if item is not None:
            result.append(item)
    return result


def _priced_cost(mass: _Mass, prices: dict[str, IngredientPrice]) -> float | None:
    price = prices.get(mass.name)
    if price is None or mass.grams is None:
        return None
    return mass.grams * price.price100 / 100


def _coverage(masses: list[_Mass], prices: dict[str, IngredientPrice]) -> float:
    """Masa linii z ceną / masa wszystkich linii z masą; 0 przy braku masy."""
    known = [m for m in masses if m.grams is not None]
    total = sum(m.grams or 0.0 for m in known)
    if total <= 0:
        return 0.0
    return sum(m.grams or 0.0 for m in known if m.name in prices) / total


def plan_cost(store: Store, settings: Settings, week_start: date) -> PlanCost:
    """Koszt gotowania dla domu (wszystkie sesje tygodnia, także szkicu), bez odejmowania „w domu"."""
    plan = store.plan(week_start)
    if plan is None:
        return PlanCost(0.0, 0.0, ())
    ctx = _context(store)
    sessions: list[SessionCost] = []
    every: list[_Mass] = []
    for item in _all_masses(store, ctx, plan.id):
        cost = sum(c for c in (_priced_cost(m, ctx.prices) for m in item.masses) if c is not None)
        every.extend(item.masses)
        sessions.append(
            SessionCost(
                session_id=item.session.id,
                recipe_id=item.session.recipe_id,
                title=item.title,
                cost=round(cost, 2),
                per_portion=round(cost / item.portions, 2) if item.portions > 0 else 0.0,
                coverage=_coverage(list(item.masses), ctx.prices),
            )
        )
    return PlanCost(
        total=round(sum(s.cost for s in sessions), 2),
        coverage=_coverage(every, ctx.prices),
        sessions=tuple(sessions),
    )


def _empty(week_start: date, notice: Notice) -> ShoppingList:
    return ShoppingList(week_start, notice, (), (), (), 0.0, 0.0)


def shopping_list(store: Store, settings: Settings, week_start: date, today: date) -> ShoppingList:
    """Lista z zatwierdzonego planu: bieżący tydzień tylko od sesji o ``start_day`` ≥ dziś."""
    plan = store.plan(week_start)
    if plan is None:
        return _empty(week_start, "no_plan")
    if plan.status != "approved":
        return _empty(week_start, "not_approved")
    if today >= week_start + timedelta(days=WEEK_DAYS):
        return _empty(week_start, "past_week")

    ctx = _context(store)
    min_start_day = max(0, (today - week_start).days)
    merged: dict[int, tuple[str, float | None]] = {}
    for item in _all_masses(store, ctx, plan.id, min_start_day):
        for mass in item.masses:
            name, grams = merged.get(mass.ingredient_id, (mass.name, None))
            if mass.grams is not None:
                grams = (grams or 0.0) + mass.grams
            merged[mass.ingredient_id] = (name, grams)

    states = store.shopping_states(week_start.isoformat())
    home_names = {s.ingredient for s in stats(store, settings, today) if s.at_home}
    force = states.get("force", set())

    to_buy: list[ShoppingLine] = []
    at_home: list[ShoppingLine] = []
    unpriced: list[ShoppingLine] = []
    priced_g = unpriced_g = cost_total = 0.0
    for ingredient_id, (name, grams) in sorted(merged.items(), key=lambda kv: kv[1][0]):
        price = ctx.prices.get(name)
        per = ctx.per100.get(ingredient_id)
        cost = None if price is None or grams is None else round(grams * price.price100 / 100, 2)
        pack_g = price.pack_g if price else None
        home = name in home_names and ingredient_id not in force
        line = ShoppingLine(
            ingredient_id=ingredient_id,
            name=name,
            grams=None if grams is None else round(grams, 1),
            pieces=round(grams / per.piece_g, 1) if grams and per and per.piece_g else None,
            pack_g=pack_g,
            packs=max(1, math.ceil(grams / pack_g)) if grams and pack_g else None,
            cost=cost,
            priced=cost is not None,
            at_home=home,
        )
        if home:
            at_home.append(line)
            continue
        if cost is None:
            unpriced.append(line)
            unpriced_g += grams or 0.0
        else:
            to_buy.append(line)
            priced_g += grams or 0.0
            cost_total += cost
    known_g = priced_g + unpriced_g
    return ShoppingList(
        week_start=week_start,
        notice="ok" if ctx.prices else "no_prices",
        to_buy=tuple(to_buy),
        at_home=tuple(at_home),
        unpriced=tuple(unpriced),
        cost_total=round(cost_total, 2),
        coverage=priced_g / known_g if known_g > 0 else 0.0,
    )
