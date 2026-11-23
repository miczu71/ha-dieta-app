"""Serwis planu tygodnia: pula przepisów, historia, generacja → zapis → odczyt, undo, zatwierdzenie.

Klej między ``planner`` (czysty algorytm) a ``store``; bez HTTP. Wszystkie zmiany planu zapisują
migawkę przed zmianą (``save_undo``), żeby „Cofnij” cofało jeden krok.
"""

from __future__ import annotations

import json
import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from . import planner
from .match import DEFAULT_BASICS
from .profile import IngredientStat
from .settings import WEEKDAYS, Settings
from .store import PERSON_IDS, NewPlanMeal, NewPlanSession, PlanRow, RecipeRow, Store
from .sync import stats
from .targets import person_targets

DAY_NAMES = ("Pn", "Wt", "Śr", "Cz", "Pt", "Sb", "Nd")

# Kategoria katalogu → sloty (PLAN_E4 decyzja 4). Wartość = premia; obecność klucza = slot dozwolony.
# Premia 0,5 dla „cudzej” kategorii (obiad z dinner, kolacja z lunch): decyzja interpretacyjna W3.
CATEGORY_SLOTS: Mapping[str, Mapping[str, float]] = {
    "breakfast": {"breakfast": 1.0},
    "lunch": {"lunch": 1.0, "dinner": 0.5},
    "dinner": {"dinner": 1.0, "lunch": 0.5},
    "snack": {"snack1": 1.0, "snack2": 1.0},
    "dessert": {"snack1": 1.0, "snack2": 1.0},
}

# Outcome.notice — kod dla routera (komunikat wybiera szablon).
NOTICE_GENERATED = "generated"
NOTICE_NEEDS_CONFIRM = "needs_confirm"
NOTICE_NO_PROFILES = "no_profiles"
NOTICE_NO_PLAN = "no_plan"
NOTICE_APPROVED = "approved"
NOTICE_UNDONE = "undone"
NOTICE_NOTHING_TO_UNDO = "nothing_to_undo"
NOTICE_SWAPPED = "swapped"
NOTICE_UNCHANGED = "unchanged"
NOTICE_NOT_FOUND = "not_found"


@dataclass(frozen=True)
class Outcome:
    notice: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pool:
    items: list[planner.Candidate]
    estimated: frozenset[int]  # przepisy, których kcal to szacunek


@dataclass(frozen=True)
class PersonBalance:
    label: str
    kcal: int
    target: int
    ok: bool


@dataclass(frozen=True)
class MealView:
    day: int
    slot: str
    slot_label: str
    session_id: int
    session_first: bool  # pierwszy dzień sesji — tu stoją przyciski wymiany sesji
    session_start: int
    session_days: int
    recipe_id: int
    title: str
    p1: float | None
    p2: float | None
    kcal1: int | None
    kcal2: int | None
    estimated: bool
    minutes: int | None
    storage_days: int | None
    liked: bool


@dataclass(frozen=True)
class DayView:
    index: int  # 0 = poniedziałek
    name: str
    date: date
    people: tuple[PersonBalance | None, PersonBalance | None]
    meals: tuple[MealView, ...]


@dataclass(frozen=True)
class PlanView:
    week_start: date
    prev_week: date
    next_week: date
    plan: PlanRow | None
    person_labels: tuple[str, str]
    has_target: tuple[bool, bool]
    days: tuple[DayView, ...]


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def monday_of(raw: str | None, today: date) -> date:
    """Poniedziałek tygodnia zawierającego ``raw`` (ISO); brak albo zły format → tydzień ``today``."""
    try:
        day = date.fromisoformat(raw) if raw else today
    except ValueError:
        day = today
    return monday(day)


def _kcal_ok(r: RecipeRow) -> bool:
    if r.kcal_source == "source":
        source_ok = True
    else:
        source_ok = r.kcal_source == "estimate" and r.est_status in ("auto", "approved")
    return source_ok and None not in (r.kcal, r.protein, r.carbs, r.fat)


def _main_ingredient(store: Store, recipe_id: int) -> str | None:
    for _text, name in store.recipe_ingredient_names(recipe_id):
        if name is not None and name not in DEFAULT_BASICS:
            return name
    return None


def _candidate(store: Store, r: RecipeRow) -> planner.Candidate:
    return planner.Candidate(
        id=r.id,
        title=r.title,
        slot_affinity=CATEGORY_SLOTS.get(r.meal_category or "", {}),
        kcal=float(r.kcal or 0.0),
        protein=float(r.protein or 0.0),
        carbs=float(r.carbs or 0.0),
        fat=float(r.fat or 0.0),
        minutes=r.minutes,
        storage_days=r.storage_days,
        familiarity=r.familiarity.ratio,
        liked=r.liked,
        main_ingredient=_main_ingredient(store, r.id),
        dismissed=r.dismissed,
    )


def _stat_map(store: Store, settings: Settings) -> dict[str, IngredientStat]:
    return {s.ingredient: s for s in stats(store, settings, date.today())}


def candidates_for(
    store: Store, settings: Settings, recipe_ids: Iterable[int]
) -> dict[int, planner.Candidate]:
    """Kandydaci tylko dla podanych przepisów (bilans/odhaczanie), bez ładowania całego katalogu."""
    stat_map = _stat_map(store, settings)
    rows = {rid: store.recipe(rid, stat_map, DEFAULT_BASICS) for rid in set(recipe_ids)}
    return {rid: _candidate(store, row) for rid, row in rows.items() if row is not None}


def build_pool(store: Store, settings: Settings, extra_ids: Iterable[int] = ()) -> Pool:
    """Pula do planera. ``extra_ids`` (przepisy już w planie) dołączane zawsze, nawet gdy odrzucone,
    żeby wymiana i bilans nie padały na przepisie, który później odrzucono w katalogu."""
    stat_map = _stat_map(store, settings)
    items: list[planner.Candidate] = []
    estimated: set[int] = set()
    for r in store.recipes(stat_map, DEFAULT_BASICS):
        if r.meal_category not in CATEGORY_SLOTS or not _kcal_ok(r):
            continue
        items.append(_candidate(store, r))
        if r.kcal_source == "estimate":
            estimated.add(r.id)
    known = {c.id for c in items}
    for recipe_id in sorted(set(extra_ids) - known):
        row = store.recipe(recipe_id, stat_map, DEFAULT_BASICS)
        if row is None:
            continue
        items.append(_candidate(store, row))
        if row.kcal_source == "estimate":
            estimated.add(row.id)
    return Pool(items, frozenset(estimated))


def history(store: Store, week_start: date) -> dict[int, date]:
    """Przepisy z zatwierdzonych planów z ostatnich 14 dni. Wpisy z tego tygodnia pomijamy:
    przy regeneracji zatwierdzonego tygodnia nie mogą blokować własnych dań."""
    since = week_start - timedelta(days=planner.REPEAT_DAYS)
    week_end = week_start + timedelta(days=planner.WEEK_DAYS)
    return {
        recipe_id: eaten
        for recipe_id, eaten in store.approved_recipe_history(since).items()
        if not week_start <= eaten < week_end
    }


def _person_target(store: Store, settings: Settings, person_id: int) -> planner.PersonTarget | None:
    t = person_targets(store, settings, person_id)
    if t is None:
        return None
    return planner.PersonTarget(float(t.kcal), float(t.protein_g), float(t.fat_g), float(t.carb_g))


def build_targets(store: Store, settings: Settings) -> planner.Targets:
    return planner.Targets(
        _person_target(store, settings, PERSON_IDS[0]),
        _person_target(store, settings, PERSON_IDS[1]),
    )


def plan_config(settings: Settings) -> planner.PlanConfig:
    return planner.PlanConfig(
        slot_pct={
            "breakfast": settings.slot_breakfast_pct,
            "lunch": settings.slot_lunch_pct,
            "dinner": settings.slot_dinner_pct,
            "snack1": settings.slot_snack1_pct,
            "snack2": settings.slot_snack2_pct,
        },
        cook_days=tuple(sorted(WEEKDAYS.index(d) for d in settings.cook_days)),
        max_minutes_weekday=settings.max_minutes_weekday,
        session_days_max=settings.session_days_max,
        discovery=settings.discovery,
        familiar_threshold=settings.familiar_threshold / 100,
    )


def _params_json(config: planner.PlanConfig, targets: planner.Targets) -> str:
    return json.dumps({"config": asdict(config), "targets": asdict(targets)}, sort_keys=True)


def _to_result(store: Store, plan_id: int) -> planner.PlanResult:
    sessions = store.plan_sessions(plan_id)
    by_id = {s.id: s for s in sessions}
    return planner.PlanResult(
        tuple(planner.Session(s.slot, s.recipe_id, s.start_day, s.days) for s in sessions),
        tuple(
            planner.Meal(m.day, m.slot, by_id[m.session_id].recipe_id, m.p1_portions, m.p2_portions)
            for m in store.plan_meals(plan_id)
        ),
    )


def _contents(result: planner.PlanResult) -> tuple[list[NewPlanSession], list[NewPlanMeal]]:
    """Sesje i posiłki w formie zapisu. Posiłek łączy się z sesją po (slot, recipe_id); przepis
    występuje w planie najwyżej raz, więc para jest jednoznaczna."""
    sessions = [NewPlanSession(s.slot, s.recipe_id, s.start_day, s.days) for s in result.sessions]
    index = {(s.slot, s.recipe_id): i for i, s in enumerate(result.sessions)}
    meals = [
        NewPlanMeal(m.day, m.slot, index[(m.slot, m.recipe_id)], m.p1_portions, m.p2_portions)
        for m in result.meals
    ]
    return sessions, meals


def _snapshot(store: Store, plan: PlanRow) -> str:
    sessions = store.plan_sessions(plan.id)
    index = {s.id: i for i, s in enumerate(sessions)}
    data = {
        "status": plan.status,
        "sessions": [
            {"slot": s.slot, "recipe_id": s.recipe_id, "start_day": s.start_day, "days": s.days}
            for s in sessions
        ],
        "meals": [
            {
                "day": m.day,
                "slot": m.slot,
                "session_index": index[m.session_id],
                "p1_portions": m.p1_portions,
                "p2_portions": m.p2_portions,
            }
            for m in store.plan_meals(plan.id)
        ],
    }
    return json.dumps(data, sort_keys=True)


def _random_seed() -> int:
    return random.randrange(1, 2**31)


def generate(
    store: Store,
    settings: Settings,
    week_start: date,
    *,
    confirm: bool = False,
    seed: int | None = None,
) -> Outcome:
    """Generuje plan tygodnia. Zatwierdzony tydzień zmienia się tylko z ``confirm=True``."""
    targets = build_targets(store, settings)
    if targets.p1 is None and targets.p2 is None:
        return Outcome(NOTICE_NO_PROFILES)
    plan = store.plan(week_start)
    if plan is not None and plan.status == "approved" and not confirm:
        return Outcome(NOTICE_NEEDS_CONFIRM)
    config = plan_config(settings)
    seed = _random_seed() if seed is None else seed
    pool = build_pool(store, settings)
    result = planner.generate(pool.items, targets, config, history(store, week_start), seed, week_start)
    sessions, meals = _contents(result)
    if plan is None:
        store.save_plan(week_start, "draft", seed, _params_json(config, targets), sessions, meals)
    else:
        store.save_undo(plan.id, _snapshot(store, plan))
        store.replace_plan_contents(plan.id, sessions, meals)
        store.update_plan_params(plan.id, seed, _params_json(config, targets))
        if plan.status == "approved":
            store.set_plan_status(plan.id, "draft")
    return Outcome(NOTICE_GENERATED, result.warnings)


def approve(store: Store, week_start: date) -> Outcome:
    plan = store.plan(week_start)
    if plan is None:
        return Outcome(NOTICE_NO_PLAN)
    if plan.status != "approved":
        store.set_plan_status(plan.id, "approved")
    return Outcome(NOTICE_APPROVED)


SwapOp = Callable[
    [
        planner.PlanResult,
        list[planner.Candidate],
        planner.Targets,
        planner.PlanConfig,
        Mapping[int, date],
        int,
    ],
    planner.PlanResult,
]


def _apply(store: Store, settings: Settings, plan: PlanRow, op: SwapOp) -> Outcome:
    targets = build_targets(store, settings)
    if targets.p1 is None and targets.p2 is None:
        return Outcome(NOTICE_NO_PROFILES)
    current = _to_result(store, plan.id)
    pool = build_pool(store, settings, extra_ids={s.recipe_id for s in current.sessions})
    config = plan_config(settings)
    new = op(current, pool.items, targets, config, history(store, plan.week_start), _random_seed())
    if new == current:
        return Outcome(NOTICE_UNCHANGED, new.warnings)
    store.save_undo(plan.id, _snapshot(store, plan))
    sessions, meals = _contents(new)
    store.replace_plan_contents(plan.id, sessions, meals)
    return Outcome(NOTICE_SWAPPED, new.warnings)


def swap_session(store: Store, settings: Settings, week_start: date, session_id: int, mode: str) -> Outcome:
    plan = store.plan(week_start)
    if plan is None:
        return Outcome(NOTICE_NO_PLAN)
    sess = next((s for s in store.plan_sessions(plan.id) if s.id == session_id), None)
    if sess is None:
        return Outcome(NOTICE_NOT_FOUND)

    def op(
        cur: planner.PlanResult,
        pool: list[planner.Candidate],
        targets: planner.Targets,
        config: planner.PlanConfig,
        hist: Mapping[int, date],
        seed: int,
    ) -> planner.PlanResult:
        return planner.swap_session(
            cur, pool, targets, config, hist, seed, plan.week_start, sess.slot, sess.start_day, mode
        )

    return _apply(store, settings, plan, op)


def swap_day(store: Store, settings: Settings, week_start: date, day: int, slot: str, mode: str) -> Outcome:
    plan = store.plan(week_start)
    if plan is None:
        return Outcome(NOTICE_NO_PLAN)

    def op(
        cur: planner.PlanResult,
        pool: list[planner.Candidate],
        targets: planner.Targets,
        config: planner.PlanConfig,
        hist: Mapping[int, date],
        seed: int,
    ) -> planner.PlanResult:
        return planner.swap_day(cur, pool, targets, config, hist, seed, plan.week_start, slot, day, mode)

    return _apply(store, settings, plan, op)


def undo(store: Store, week_start: date) -> Outcome:
    plan = store.plan(week_start)
    if plan is None:
        return Outcome(NOTICE_NO_PLAN)
    snapshot = store.pop_undo(plan.id)
    if snapshot is None:
        return Outcome(NOTICE_NOTHING_TO_UNDO)
    data = json.loads(snapshot)
    store.replace_plan_contents(
        plan.id,
        [NewPlanSession(**s) for s in data["sessions"]],
        [NewPlanMeal(**m) for m in data["meals"]],
    )
    if data["status"] != plan.status:
        store.set_plan_status(plan.id, data["status"])
    return Outcome(NOTICE_UNDONE)


def view(store: Store, settings: Settings, week_start: date) -> PlanView:
    plan = store.plan(week_start)
    targets = build_targets(store, settings)
    labels = (settings.person_name(PERSON_IDS[0]), settings.person_name(PERSON_IDS[1]))
    has_target = (targets.p1 is not None, targets.p2 is not None)
    prev_week = week_start - timedelta(days=planner.WEEK_DAYS)
    next_week = week_start + timedelta(days=planner.WEEK_DAYS)
    if plan is None:
        days = tuple(_empty_day(week_start, i) for i in range(planner.WEEK_DAYS))
        return PlanView(week_start, prev_week, next_week, None, labels, has_target, days)

    current = _to_result(store, plan.id)
    pool = build_pool(store, settings, extra_ids={s.recipe_id for s in current.sessions})
    by_id = {c.id: c for c in pool.items}
    config = plan_config(settings)
    balances = planner.evaluate(current, pool.items, targets, config)
    sessions = {s.id: s for s in store.plan_sessions(plan.id)}
    meals_by_day: dict[int, list[MealView]] = {i: [] for i in range(planner.WEEK_DAYS)}
    for m in store.plan_meals(plan.id):
        sess = sessions[m.session_id]
        cand = by_id[sess.recipe_id]
        meals_by_day[m.day].append(
            MealView(
                day=m.day,
                slot=m.slot,
                slot_label=planner.SLOT_NAMES_PL[m.slot],
                session_id=sess.id,
                session_first=sess.start_day == m.day,
                session_start=sess.start_day,
                session_days=sess.days,
                recipe_id=cand.id,
                title=cand.title,
                p1=m.p1_portions,
                p2=m.p2_portions,
                kcal1=_kcal(m.p1_portions, cand.kcal),
                kcal2=_kcal(m.p2_portions, cand.kcal),
                estimated=cand.id in pool.estimated,
                minutes=cand.minutes,
                storage_days=cand.storage_days,
                liked=cand.liked,
            )
        )
    days = tuple(
        DayView(
            index=bal.day,
            name=DAY_NAMES[bal.day],
            date=week_start + timedelta(days=bal.day),
            people=(_balance(bal.p1, labels[0]), _balance(bal.p2, labels[1])),
            meals=tuple(sorted(meals_by_day[bal.day], key=lambda x: planner.SLOTS.index(x.slot))),
        )
        for bal in balances
    )
    return PlanView(week_start, prev_week, next_week, plan, labels, has_target, days)


def _kcal(portions: float | None, kcal_per_portion: float) -> int | None:
    return None if portions is None else round(portions * kcal_per_portion)


def _balance(person: planner.PersonDay | None, label: str) -> PersonBalance | None:
    if person is None:
        return None
    return PersonBalance(label, round(person.kcal), round(person.target_kcal), person.within_tolerance)


def _empty_day(week_start: date, index: int) -> DayView:
    return DayView(index, DAY_NAMES[index], week_start + timedelta(days=index), (None, None), ())
