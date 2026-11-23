"""Realizacja planu: odhaczanie posiłków (osoba × dzień × slot) i bilans dnia/tygodnia.

Serwis bez HTTP. Dziennik (``meal_log``) jest niezależny od ``plan_meal``: przy odhaczeniu zapisujemy
migawkę przepisu i wartości, więc późniejsza regeneracja planu nie zmienia bilansu. Brak wiersza =
„bez odpowiedzi” (nie liczy się do bilansu).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from . import planner
from .plan_service import build_pool
from .settings import Settings
from .store import PERSON_IDS, MealLogEntry, PlanMeal, PlanRow, PlanSession, Store
from .targets import person_targets

NOTICE_OK = "ok"
NOTICE_NO_PLAN = "no_plan"
NOTICE_NOT_APPROVED = "not_approved"
NOTICE_OUT_OF_WINDOW = "out_of_window"
NOTICE_NO_MEAL = "no_meal"
NOTICE_BAD = "bad"

WINDOW_DAYS = 7
STATUSES = ("eaten", "skipped", "substitute")
MAX_NAME_CHARS = 80
MAX_KCAL = 5000.0
MAX_MACRO = 1000.0
DAY_NAMES = ("Pn", "Wt", "Śr", "Cz", "Pt", "Sb", "Nd")


@dataclass(frozen=True)
class Outcome:
    notice: str


@dataclass(frozen=True)
class MealPerson:
    portions: float
    planned_kcal: float
    status: str | None  # None = bez odpowiedzi
    logged_kcal: float | None
    sub_name: str | None


@dataclass(frozen=True)
class MealLog:
    slot: str
    slot_label: str
    title: str
    recipe_id: int
    session_days: int
    per_person: tuple[MealPerson | None, MealPerson | None]


@dataclass(frozen=True)
class PersonDayLog:
    label: str
    target_kcal: float
    eaten_kcal: float
    eaten_protein: float
    eaten_fat: float
    eaten_carbs: float
    unanswered: int


@dataclass(frozen=True)
class DayLog:
    day: date
    editable: bool
    plan_status: str | None
    people: tuple[PersonDayLog | None, PersonDayLog | None]
    meals: tuple[MealLog, ...]


@dataclass(frozen=True)
class PersonDayTotals:
    day: int  # 0 = poniedziałek
    name: str
    date: date
    eaten_kcal: float
    target_kcal: float
    unanswered: int
    future: bool


@dataclass(frozen=True)
class PersonWeek:
    label: str
    days: tuple[PersonDayTotals, ...]
    eaten_kcal: float
    target_kcal: float  # suma celów dni <= dziś
    unanswered: int
    leftover_portions: float


@dataclass(frozen=True)
class WeekBalance:
    week_start: date
    prev_week: date
    next_week: date
    plan_status: str | None
    people: tuple[PersonWeek | None, PersonWeek | None]


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _in_window(day: date, today: date) -> bool:
    return today - timedelta(days=WINDOW_DAYS) <= day <= today


def _portions(meal: PlanMeal, person_id: int) -> float | None:
    return meal.p1_portions if person_id == PERSON_IDS[0] else meal.p2_portions


def _valid_number(value: float | None, high: float) -> bool:
    return value is not None and 0 <= value <= high


def _valid_input(
    person_id: int,
    slot: str,
    status: str,
    name: str,
    kcal: float | None,
    macros: tuple[float | None, float | None, float | None],
) -> bool:
    if status not in STATUSES or person_id not in PERSON_IDS or slot not in planner.SLOTS:
        return False
    if status != "substitute":
        return True
    clean = name.strip()
    if not clean or len(clean) > MAX_NAME_CHARS or not _valid_number(kcal, MAX_KCAL):
        return False
    return all(m is None or _valid_number(m, MAX_MACRO) for m in macros)


def mark(
    store: Store,
    settings: Settings,
    day: date,
    person_id: int,
    slot: str,
    status: str,
    today: date,
    *,
    name: str = "",
    kcal: float | None = None,
    protein: float | None = None,
    fat: float | None = None,
    carbs: float | None = None,
) -> Outcome:
    """Zapisuje stan posiłku osoby; ten sam (dzień, osoba, slot) nadpisuje poprzedni wpis."""
    if not _valid_input(person_id, slot, status, name, kcal, (protein, fat, carbs)):
        return Outcome(NOTICE_BAD)
    if not _in_window(day, today):
        return Outcome(NOTICE_OUT_OF_WINDOW)
    plan = store.plan(_monday(day))
    if plan is None:
        return Outcome(NOTICE_NO_PLAN)
    if plan.status != "approved":
        return Outcome(NOTICE_NOT_APPROVED)
    meal = next((m for m in store.plan_meals(plan.id) if m.day == day.weekday() and m.slot == slot), None)
    portions = None if meal is None else _portions(meal, person_id)
    if meal is None or portions is None:
        return Outcome(NOTICE_NO_MEAL)
    session = next((s for s in store.plan_sessions(plan.id) if s.id == meal.session_id), None)
    if session is None:
        return Outcome(NOTICE_NO_MEAL)
    cand = next(
        (
            c
            for c in build_pool(store, settings, extra_ids={session.recipe_id}).items
            if c.id == session.recipe_id
        ),
        None,
    )
    if cand is None:
        return Outcome(NOTICE_NO_MEAL)

    if status == "eaten":
        entry = MealLogEntry(
            day, person_id, slot, status, cand.id, cand.title, portions,
            portions * cand.kcal, portions * cand.protein, portions * cand.fat, portions * cand.carbs,
        )  # fmt: skip
    elif status == "skipped":
        entry = MealLogEntry(
            day, person_id, slot, status, cand.id, cand.title, portions, 0.0, None, None, None
        )
    else:
        assert kcal is not None
        entry = MealLogEntry(
            day, person_id, slot, status, cand.id, name.strip(), portions, kcal, protein, fat, carbs
        )
    store.set_meal_log(entry)
    return Outcome(NOTICE_OK)


def clear(store: Store, settings: Settings, day: date, person_id: int, slot: str, today: date) -> Outcome:
    """Cofnięcie odhaczenia = usunięcie wiersza (brak wiersza też jest ``ok``)."""
    del settings
    if person_id not in PERSON_IDS or slot not in planner.SLOTS:
        return Outcome(NOTICE_BAD)
    if not _in_window(day, today):
        return Outcome(NOTICE_OUT_OF_WINDOW)
    store.clear_meal_log(day, person_id, slot)
    return Outcome(NOTICE_OK)


def _approved_plan(store: Store, day: date) -> PlanRow | None:
    plan = store.plan(_monday(day))
    return plan if plan is not None and plan.status == "approved" else None


def _unanswered(
    store: Store, plan: PlanRow | None, day: date, logged: set[tuple[int, str]]
) -> dict[int, int]:
    counts = {pid: 0 for pid in PERSON_IDS}
    if plan is None:
        return counts
    for meal in store.plan_meals(plan.id):
        if meal.day != day.weekday():
            continue
        for pid in PERSON_IDS:
            if _portions(meal, pid) is not None and (pid, meal.slot) not in logged:
                counts[pid] += 1
    return counts


def unanswered_for(store: Store, settings: Settings, day: date, today: date) -> dict[int, int]:
    """Liczba zaplanowanych posiłków osoby bez wiersza w dzienniku (tylko plan zatwierdzony, dni <= dziś)."""
    del settings
    if day > today:
        return {pid: 0 for pid in PERSON_IDS}
    logged = {(e.person_id, e.slot) for e in store.meal_log(day, day)}
    return _unanswered(store, _approved_plan(store, day), day, logged)


def _label(settings: Settings, person_id: int) -> str:
    return settings.person_name(person_id)


def day_log(store: Store, settings: Settings, day: date, today: date) -> DayLog:
    plan = store.plan(_monday(day))
    entries = {(e.person_id, e.slot): e for e in store.meal_log(day, day)}
    meals: list[MealLog] = []
    if plan is not None:
        sessions: dict[int, PlanSession] = {s.id: s for s in store.plan_sessions(plan.id)}
        pool = build_pool(store, settings, extra_ids={s.recipe_id for s in sessions.values()})
        by_id = {c.id: c for c in pool.items}
        plan_meals = [m for m in store.plan_meals(plan.id) if m.day == day.weekday()]
        for m in sorted(plan_meals, key=lambda x: planner.SLOTS.index(x.slot)):
            sess = sessions[m.session_id]
            cand = by_id[sess.recipe_id]
            per: list[MealPerson | None] = []
            for pid in PERSON_IDS:
                portions = _portions(m, pid)
                if portions is None:
                    per.append(None)
                    continue
                e = entries.get((pid, m.slot))
                per.append(
                    MealPerson(
                        portions,
                        portions * cand.kcal,
                        None if e is None else e.status,
                        None if e is None else e.kcal,
                        e.title if e is not None and e.status == "substitute" else None,
                    )
                )
            meals.append(
                MealLog(
                    m.slot, planner.SLOT_NAMES_PL[m.slot], cand.title, cand.id, sess.days,
                    (per[0], per[1]),
                )
            )  # fmt: skip
    approved = plan is not None and plan.status == "approved"
    unanswered = unanswered_for(store, settings, day, today)
    people: list[PersonDayLog | None] = []
    for pid in PERSON_IDS:
        target = person_targets(store, settings, pid)
        if target is None:
            people.append(None)
            continue
        rows = [e for (p, _), e in entries.items() if p == pid]
        people.append(
            PersonDayLog(
                _label(settings, pid),
                float(target.kcal),
                sum(e.kcal for e in rows),
                sum(e.protein or 0.0 for e in rows),
                sum(e.fat or 0.0 for e in rows),
                sum(e.carbs or 0.0 for e in rows),
                unanswered[pid],
            )
        )
    return DayLog(
        day,
        approved and _in_window(day, today),
        None if plan is None else plan.status,
        (people[0], people[1]),
        tuple(meals),
    )


def _leftover(store: Store, plan: PlanRow | None, rows: list[MealLogEntry]) -> dict[int, float]:
    """Porcje z posiłków skipped/substitute należących do sesji wielodniowych bieżącego planu."""
    totals = {pid: 0.0 for pid in PERSON_IDS}
    if plan is None:
        return totals
    multi = {s.recipe_id for s in store.plan_sessions(plan.id) if s.days > 1}
    for e in rows:
        if e.status in ("skipped", "substitute") and e.recipe_id in multi:
            totals[e.person_id] += e.portions or 0.0
    return totals


def week_balance(store: Store, settings: Settings, week_start: date, today: date) -> WeekBalance:
    plan = store.plan(week_start)
    rows = store.meal_log(week_start, week_start + timedelta(days=planner.WEEK_DAYS - 1))
    dates = [week_start + timedelta(days=i) for i in range(planner.WEEK_DAYS)]
    approved = plan if plan is not None and plan.status == "approved" else None
    unanswered = {
        d: _unanswered(
            store, approved if d <= today else None, d, {(e.person_id, e.slot) for e in rows if e.day == d}
        )
        for d in dates
    }
    leftover = _leftover(store, plan, rows)
    people: list[PersonWeek | None] = []
    for pid in PERSON_IDS:
        target = person_targets(store, settings, pid)
        if target is None:
            people.append(None)
            continue
        days = tuple(
            PersonDayTotals(
                i,
                DAY_NAMES[i],
                d,
                sum(e.kcal for e in rows if e.person_id == pid and e.day == d),
                float(target.kcal),
                unanswered[d][pid],
                d > today,
            )
            for i, d in enumerate(dates)
        )
        people.append(
            PersonWeek(
                _label(settings, pid),
                days,
                sum(t.eaten_kcal for t in days),
                sum(t.target_kcal for t in days if not t.future),
                sum(t.unanswered for t in days),
                leftover[pid],
            )
        )
    return WeekBalance(
        week_start,
        week_start - timedelta(days=planner.WEEK_DAYS),
        week_start + timedelta(days=planner.WEEK_DAYS),
        None if plan is None else plan.status,
        (people[0], people[1]),
    )
