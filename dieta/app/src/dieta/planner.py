"""Planer tygodnia: czysty algorytm (bez store, sieci i zegara; losowość tylko z ``random.Random(seed)``).

Decyzje interpretacyjne (tam, gdzie PLAN_E4 jest niejednoznaczny, wybrane prostsze rozwiązanie):

* Cel dnia = cel kcal osoby x suma udziałów włączonych slotów (reszta to rezerwa poza planem).
  Warunek ±10% jest egzekwowany na poziomie slotu (błąd kcal slotu <= 10% => dzień też <= 10%).
* Pokrycie od poniedziałku: dzień 0 jest zawsze dozwolonym startem sesji (inaczej dni przed pierwszym
  dniem gotowania zostałyby puste); pozostałe starty tylko w ``cook_days``. Sesja kończy się w dniu,
  po którym zaczyna się kolejna możliwa sesja (albo w niedzielę); gdy kandydat tak się nie układa,
  wybierany jest tylko wtedy, gdy nie ma lepszego, a ewentualna luka dostaje ostrzeżenie.
* Porcje są zaokrąglane do 0,05 i obcinane do 0,6–1,8; kandydat z błędem slotu > 10% jest ostatnią
  deską ratunku (z ostrzeżeniem). Lokalnej poprawy wielokrotnej nie robimy: twardy filtr na slot
  daje te same gwarancje prościej.
* Znajomość: faza A wybiera sesje ze znajomych (próg obniżany o 10 pp z ostrzeżeniem), faza B zamienia
  ``discovery`` losowych sesji na przepisy niekupowane (znajomość < ``familiar_threshold``).
* Przepis występuje w planie najwyżej raz (dlatego sesję identyfikuje ``recipe_id``); historia blokuje
  przepis, jeśli któryś dzień sesji leży mniej niż 14 dni od daty z historii (w obie strony).
* Wymiana dnia: nowy przepis dostaje własną sesję 1-dniową zaczynającą się tego dnia (także poza dniem
  gotowania - to świadoma decyzja użytkownika). ``Session.days`` = rozpiętość od ``start_day`` do
  ostatniego posiłku sesji (może zawierać dziury po wymienionym dniu).
* Wymiana (losowa i ♥): kcal na porcję w ±15% starego przepisu; losowa tylko ze znajomych, ♥ tylko
  z polubionych (bez wymogu znajomości). Brak kandydata => plan bez zmian + komunikat w ``warnings``.
  ``warnings`` z ``swap_*`` zawierają tylko nowe komunikaty tej operacji.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

SLOTS: tuple[str, ...] = ("breakfast", "lunch", "dinner", "snack1", "snack2")
SLOT_NAMES_PL = {
    "breakfast": "śniadanie",
    "lunch": "obiad",
    "dinner": "kolacja",
    "snack1": "II śniadanie",
    "snack2": "podwieczorek",
}
DEFAULT_SLOT_PCT: Mapping[str, int] = {"breakfast": 25, "lunch": 35, "dinner": 30, "snack1": 0, "snack2": 0}

# --- wagi i kary (jedno miejsce) ---
W_FAMILIARITY = 3.0
W_LIKED = 1.5
W_PORTION_FIT = 2.0
W_AFFINITY = 1.0
W_HOME = 1.5  # tryb „taniej”: premia za udział składników już w domu
P_REPEAT = math.inf  # twarde
P_MAIN_DUP = 1.0
P_MACRO = 1.0
P_TIME = 1.0
JITTER = 0.4

# --- stałe domenowe ---
MIN_PORTION = 0.6
MAX_PORTION = 1.8
PORTION_STEP = 0.05
SLOT_TOLERANCE = 0.10
DAY_TOLERANCE = 0.10
SWAP_KCAL_TOL = 0.15
REPEAT_DAYS = 14
MACRO_BAND = 0.40
MAIN_DUP_MAX = 2
WEEK_DAYS = 7


@dataclass(frozen=True)
class Candidate:
    id: int
    title: str
    slot_affinity: Mapping[str, float]  # slot -> premia (obecność = dozwolony)
    kcal: float  # na porcję
    protein: float
    carbs: float
    fat: float
    minutes: int | None
    storage_days: int | None
    familiarity: float  # 0..1
    liked: bool = False
    main_ingredient: str | None = None
    dismissed: bool = False
    home_ratio: float = 0.0  # 0..1, udział masy składników „w domu”


@dataclass(frozen=True)
class PersonTarget:
    kcal: float
    protein: float
    fat: float
    carbs: float


@dataclass(frozen=True)
class Targets:
    p1: PersonTarget | None
    p2: PersonTarget | None


@dataclass(frozen=True)
class PlanConfig:
    slot_pct: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_SLOT_PCT))
    cook_days: tuple[int, ...] = (5, 6, 2)  # 0 = poniedziałek
    max_minutes_weekday: int = 45
    session_days_max: int = 4
    discovery: int = 1
    familiar_threshold: float = 0.5
    cheaper: bool = False


@dataclass(frozen=True)
class Session:
    slot: str
    recipe_id: int
    start_day: int
    days: int


@dataclass(frozen=True)
class Meal:
    day: int
    slot: str
    recipe_id: int
    p1_portions: float | None
    p2_portions: float | None


@dataclass(frozen=True)
class PlanResult:
    sessions: tuple[Session, ...]
    meals: tuple[Meal, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersonDay:
    kcal: float
    protein: float
    carbs: float
    fat: float
    target_kcal: float
    within_tolerance: bool


@dataclass(frozen=True)
class DayBalance:
    day: int
    p1: PersonDay | None
    p2: PersonDay | None


@dataclass(frozen=True)
class _Fit:
    p1: float | None
    p2: float | None
    err: float  # największy błąd kcal slotu wśród osób
    raw: float  # średnia nieobcięta porcja (miara „naturalności”)


@dataclass(frozen=True)
class _Block:
    slot: str
    start: int
    length: int
    cand: Candidate


@dataclass(frozen=True)
class _Ctx:
    pool: tuple[Candidate, ...]
    by_id: Mapping[int, Candidate]
    targets: Targets
    config: PlanConfig
    history: Mapping[int, date]
    week_start: date


def _ctx(
    pool: Sequence[Candidate],
    targets: Targets,
    config: PlanConfig,
    history: Mapping[int, date],
    week_start: date,
) -> _Ctx:
    return _Ctx(tuple(pool), {c.id: c for c in pool}, targets, config, history, week_start)


def _enabled_slots(config: PlanConfig) -> list[str]:
    return [s for s in SLOTS if config.slot_pct.get(s, 0) > 0]


def _storage(c: Candidate) -> int:
    return c.storage_days if c.storage_days and c.storage_days > 0 else 1


def _round_portion(raw: float) -> float:
    p = round(round(raw / PORTION_STEP) * PORTION_STEP, 2)
    return min(MAX_PORTION, max(MIN_PORTION, p))


def _fit(ctx: _Ctx, cand: Candidate, slot: str) -> _Fit | None:
    if cand.kcal <= 0:
        return None
    pct = ctx.config.slot_pct.get(slot, 0)
    ports: list[float | None] = []
    errs: list[float] = []
    raws: list[float] = []
    for t in (ctx.targets.p1, ctx.targets.p2):
        if t is None or t.kcal <= 0:
            ports.append(None)
            continue
        want = t.kcal * pct / 100
        raw = want / cand.kcal
        p = _round_portion(raw)
        ports.append(p)
        errs.append(abs(p * cand.kcal - want) / want)
        raws.append(raw)
    if not raws:
        return None
    return _Fit(ports[0], ports[1], max(errs), sum(raws) / len(raws))


def _history_ok(ctx: _Ctx, cand: Candidate, days: Sequence[int]) -> bool:
    last = ctx.history.get(cand.id)
    if last is None:
        return True
    return all(abs((ctx.week_start + timedelta(days=d) - last).days) >= REPEAT_DAYS for d in days)


def _history_prefix(ctx: _Ctx, cand: Candidate, start: int, limit: int) -> int:
    """Ile kolejnych dni od ``start`` (max ``limit``) przepis może zajmować wg historii."""
    n = 0
    while n < limit and _history_ok(ctx, cand, [start + n]):
        n += 1
    return n


def _macro_penalty(ctx: _Ctx, cand: Candidate, slot: str, fit: _Fit) -> float:
    pct = ctx.config.slot_pct.get(slot, 0) / 100
    total = 0.0
    n = 0
    for t, port in ((ctx.targets.p1, fit.p1), (ctx.targets.p2, fit.p2)):
        if t is None or port is None:
            continue
        n += 1
        pen = 0.0
        want_p = t.protein * pct
        if want_p > 0:
            pen += max(0.0, 1 - port * cand.protein / want_p)
        for got, tgt in ((port * cand.fat, t.fat * pct), (port * cand.carbs, t.carbs * pct)):
            if tgt > 0:
                ratio = got / tgt
                pen += max(0.0, ratio - (1 + MACRO_BAND), (1 - MACRO_BAND) - ratio)
        total += pen
    return P_MACRO * total / n if n else 0.0


def _score(
    ctx: _Ctx,
    cand: Candidate,
    slot: str,
    fit: _Fit,
    start: int,
    main_counts: Mapping[str, int],
    rng: random.Random,
) -> float:
    s = W_FAMILIARITY * cand.familiarity
    s += W_LIKED if cand.liked else 0.0
    s += W_AFFINITY * cand.slot_affinity.get(slot, 0.0)
    if ctx.config.cheaper:
        s += W_HOME * cand.home_ratio
    s += W_PORTION_FIT * (1 - min(1.0, abs(math.log(fit.raw)) / math.log(MAX_PORTION)))
    s -= W_PORTION_FIT * fit.err
    main = cand.main_ingredient
    if main and main_counts.get(main, 0) >= MAIN_DUP_MAX:
        s -= P_MAIN_DUP * (main_counts[main] - MAIN_DUP_MAX + 1)
    if start < 5 and cand.minutes is not None and cand.minutes > ctx.config.max_minutes_weekday:
        s -= P_TIME
    s -= _macro_penalty(ctx, cand, slot, fit)
    s += rng.random() * JITTER
    return s


def _thresholds(t0: float) -> list[float]:
    out = []
    i = 0
    while t0 - 0.1 * i > 1e-9:
        out.append(round(t0 - 0.1 * i, 10))
        i += 1
    out.append(0.0)
    return out


def _starts(config: PlanConfig) -> set[int]:
    return {d for d in config.cook_days if 0 <= d < WEEK_DAYS} | {0}


def _main_counts(blocks: Sequence[_Block]) -> Counter[str]:
    return Counter(b.cand.main_ingredient for b in blocks if b.cand.main_ingredient)


def _plan_slot(
    ctx: _Ctx, slot: str, used: set[int], main_counts: Counter[str], rng: random.Random, warnings: list[str]
) -> list[_Block]:
    starts = _starts(ctx.config)
    name = SLOT_NAMES_PL[slot]
    blocks: list[_Block] = []
    d = 0
    lowered = False
    cap_max = ctx.config.session_days_max
    while d < WEEK_DAYS:
        if d not in starts:
            nxt = min((s for s in starts if s > d), default=WEEK_DAYS)
            warnings.append(f"Brak posiłku w slocie {name}: dni {d + 1}–{nxt} bez sesji gotowania.")
            d = nxt
            continue
        best_any: tuple[tuple[bool, bool], float, Candidate, int] | None = None
        chosen: tuple[tuple[bool, bool], float, Candidate, int] | None = None
        chosen_thr = ctx.config.familiar_threshold
        best_thr = chosen_thr
        any_candidate = False
        for thr in _thresholds(ctx.config.familiar_threshold):
            level_best: tuple[tuple[bool, bool], float, Candidate, int] | None = None
            for c in ctx.pool:
                if c.dismissed or slot not in c.slot_affinity or c.id in used or c.familiarity < thr:
                    continue
                fit = _fit(ctx, c, slot)
                if fit is None:
                    continue
                cap = min(_storage(c), cap_max, WEEK_DAYS - d)
                cap = _history_prefix(ctx, c, d, cap)
                if cap < 1:
                    continue
                valid = [L for L in range(cap, 0, -1) if d + L == WEEK_DAYS or d + L in starts]
                length = valid[0] if valid else cap
                tier = (fit.err <= SLOT_TOLERANCE, bool(valid))
                score = _score(ctx, c, slot, fit, d, main_counts, rng)
                key = (tier, score)
                if level_best is None or key > (level_best[0], level_best[1]):
                    level_best = (tier, score, c, length)
            if level_best is not None:
                any_candidate = True
                if best_any is None:
                    best_any = level_best
                    best_thr = thr
                if level_best[0] == (True, True):
                    chosen, chosen_thr = level_best, thr
                    break
        if chosen is None and best_any is not None:
            chosen, chosen_thr = best_any, best_thr
        if not any_candidate or chosen is None:
            nxt = min((s for s in starts if s > d), default=WEEK_DAYS)
            warnings.append(f"Pusta pula dla slotu {name} (dzień {d + 1}): brak kandydatów.")
            d = nxt
            continue
        if chosen_thr < ctx.config.familiar_threshold and not lowered:
            lowered = True
            warnings.append(
                f"Mała pula znajomych dla slotu {name}: próg znajomości obniżony do {chosen_thr:.0%}."
            )
        tier, _, cand, length = chosen
        if not tier[0]:
            warnings.append(f"Porcje poza zakresem ±10% kcal: {name}, {cand.title}.")
        blocks.append(_Block(slot, d, length, cand))
        used.add(cand.id)
        if cand.main_ingredient:
            main_counts[cand.main_ingredient] += 1
        d += length
    return blocks


def _apply_discovery(
    ctx: _Ctx, blocks: list[_Block], used: set[int], rng: random.Random, warnings: list[str]
) -> list[_Block]:
    want = ctx.config.discovery
    if want <= 0 or not blocks:
        return blocks
    thr = ctx.config.familiar_threshold
    done = 0
    order = list(range(len(blocks)))
    rng.shuffle(order)
    for idx in order:
        if done >= want:
            break
        b = blocks[idx]
        if b.cand.familiarity < thr:
            done += 1
            continue
        days = range(b.start, b.start + b.length)
        counts = _main_counts([x for i, x in enumerate(blocks) if i != idx])
        best: tuple[float, Candidate] | None = None
        for c in ctx.pool:
            if c.dismissed or b.slot not in c.slot_affinity or c.id in used or c.familiarity >= thr:
                continue
            if _storage(c) < b.length or not _history_ok(ctx, c, list(days)):
                continue
            fit = _fit(ctx, c, b.slot)
            if fit is None or fit.err > SLOT_TOLERANCE:
                continue
            s = _score(ctx, c, b.slot, fit, b.start, counts, rng)
            if best is None or s > best[0]:
                best = (s, c)
        if best is None:
            continue
        used.discard(b.cand.id)
        used.add(best[1].id)
        blocks[idx] = _Block(b.slot, b.start, b.length, best[1])
        done += 1
    if done < want:
        warnings.append(f"Odkrywanie: znaleziono tylko {done} z {want} sesji niekupowanych.")
    return blocks


def _assemble(ctx: _Ctx, blocks: Sequence[_Block], warnings: Sequence[str]) -> PlanResult:
    sessions: list[Session] = []
    meals: list[Meal] = []
    for b in sorted(blocks, key=lambda x: (SLOTS.index(x.slot), x.start)):
        sessions.append(Session(b.slot, b.cand.id, b.start, b.length))
        fit = _fit(ctx, b.cand, b.slot)
        p1 = fit.p1 if fit else None
        p2 = fit.p2 if fit else None
        for day in range(b.start, b.start + b.length):
            meals.append(Meal(day, b.slot, b.cand.id, p1, p2))
    meals.sort(key=lambda m: (m.day, SLOTS.index(m.slot)))
    return PlanResult(tuple(sessions), tuple(meals), tuple(warnings))


def generate(
    pool: Sequence[Candidate],
    targets: Targets,
    config: PlanConfig,
    history: Mapping[int, date],
    seed: int,
    week_start: date,
) -> PlanResult:
    ctx = _ctx(pool, targets, config, history, week_start)
    if targets.p1 is None and targets.p2 is None:
        return PlanResult((), (), ("Brak celów kcal: uzupełnij profile.",))
    rng = random.Random(seed)
    warnings: list[str] = []
    used: set[int] = set()
    main_counts: Counter[str] = Counter()
    blocks: list[_Block] = []
    for slot in _enabled_slots(config):
        blocks.extend(_plan_slot(ctx, slot, used, main_counts, rng, warnings))
    blocks = _apply_discovery(ctx, blocks, used, rng, warnings)
    return _assemble(ctx, blocks, warnings)


def evaluate(
    plan: PlanResult,
    pool: Sequence[Candidate],
    targets: Targets,
    config: PlanConfig,
) -> tuple[DayBalance, ...]:
    by_id = {c.id: c for c in pool}
    covered = sum(config.slot_pct.get(s, 0) for s in _enabled_slots(config)) / 100
    out: list[DayBalance] = []
    for day in range(WEEK_DAYS):
        sums = {1: [0.0] * 4, 2: [0.0] * 4}
        for m in plan.meals:
            if m.day != day:
                continue
            c = by_id[m.recipe_id]
            for i, port in ((1, m.p1_portions), (2, m.p2_portions)):
                if port is None:
                    continue
                sums[i][0] += port * c.kcal
                sums[i][1] += port * c.protein
                sums[i][2] += port * c.carbs
                sums[i][3] += port * c.fat
        people: list[PersonDay | None] = []
        for i, t in ((1, targets.p1), (2, targets.p2)):
            if t is None:
                people.append(None)
                continue
            tk = t.kcal * covered
            kcal = sums[i][0]
            ok = tk <= 0 or abs(kcal - tk) / tk <= DAY_TOLERANCE
            people.append(PersonDay(kcal, sums[i][1], sums[i][2], sums[i][3], tk, ok))
        out.append(DayBalance(day, people[0], people[1]))
    return tuple(out)


def _swap_candidates(
    ctx: _Ctx,
    old: Candidate,
    slot: str,
    days: Sequence[int],
    min_storage: int,
    used: set[int],
    mode: str,
) -> list[Candidate]:
    thr = ctx.config.familiar_threshold
    out = []
    for c in ctx.pool:
        if c.dismissed or slot not in c.slot_affinity or c.id in used or c.id == old.id:
            continue
        if mode == "liked":
            if not c.liked:
                continue
        elif c.familiarity < thr:
            continue
        if _storage(c) < min_storage or not _history_ok(ctx, c, days):
            continue
        if old.kcal <= 0 or abs(c.kcal - old.kcal) / old.kcal > SWAP_KCAL_TOL:
            continue
        fit = _fit(ctx, c, slot)
        if fit is None or fit.err > SLOT_TOLERANCE:
            continue
        out.append(c)
    return out


def _pick(
    ctx: _Ctx, cands: Sequence[Candidate], slot: str, start: int, plan: PlanResult, rng: random.Random
) -> Candidate:
    counts: Counter[str] = Counter()
    for sess in plan.sessions:
        main = ctx.by_id[sess.recipe_id].main_ingredient
        if main:
            counts[main] += 1
    best: tuple[float, Candidate] | None = None
    for c in cands:
        fit = _fit(ctx, c, slot)
        assert fit is not None
        s = _score(ctx, c, slot, fit, start, counts, rng)
        if best is None or s > best[0]:
            best = (s, c)
    assert best is not None
    return best[1]


def _no_candidate(mode: str) -> str:
    if mode == "liked":
        return "Brak polubionych przepisów pasujących do wymiany."
    return "Brak zamiennika spełniającego warunki."


def swap_session(
    plan: PlanResult,
    pool: Sequence[Candidate],
    targets: Targets,
    config: PlanConfig,
    history: Mapping[int, date],
    seed: int,
    week_start: date,
    slot: str,
    start_day: int,
    mode: str = "random",
) -> PlanResult:
    ctx = _ctx(pool, targets, config, history, week_start)
    session = next((s for s in plan.sessions if s.slot == slot and s.start_day == start_day), None)
    if session is None:
        return PlanResult(plan.sessions, plan.meals, ("Nie znaleziono sesji do wymiany.",))
    old = ctx.by_id[session.recipe_id]
    days = [m.day for m in plan.meals if m.recipe_id == old.id and m.slot == slot]
    span = max(days) - session.start_day + 1 if days else session.days
    used = {s.recipe_id for s in plan.sessions}
    cands = _swap_candidates(ctx, old, slot, days, span, used, mode)
    if not cands:
        return PlanResult(plan.sessions, plan.meals, (_no_candidate(mode),))
    new = _pick(ctx, cands, slot, session.start_day, plan, random.Random(seed))
    fit = _fit(ctx, new, slot)
    assert fit is not None
    sessions = tuple(Session(slot, new.id, s.start_day, s.days) if s is session else s for s in plan.sessions)
    meals = tuple(
        Meal(m.day, slot, new.id, fit.p1, fit.p2) if m.recipe_id == old.id and m.slot == slot else m
        for m in plan.meals
    )
    return PlanResult(sessions, meals, ())


def swap_day(
    plan: PlanResult,
    pool: Sequence[Candidate],
    targets: Targets,
    config: PlanConfig,
    history: Mapping[int, date],
    seed: int,
    week_start: date,
    slot: str,
    day: int,
    mode: str = "random",
) -> PlanResult:
    ctx = _ctx(pool, targets, config, history, week_start)
    meal = next((m for m in plan.meals if m.day == day and m.slot == slot), None)
    if meal is None:
        return PlanResult(plan.sessions, plan.meals, ("Brak posiłku do wymiany w tym dniu.",))
    old = ctx.by_id[meal.recipe_id]
    used = {s.recipe_id for s in plan.sessions}
    cands = _swap_candidates(ctx, old, slot, [day], 1, used, mode)
    if not cands:
        return PlanResult(plan.sessions, plan.meals, (_no_candidate(mode),))
    new = _pick(ctx, cands, slot, day, plan, random.Random(seed))
    fit = _fit(ctx, new, slot)
    assert fit is not None
    old_session = next(s for s in plan.sessions if s.slot == slot and s.recipe_id == old.id)
    old_days = [m.day for m in plan.meals if m.slot == slot and m.recipe_id == old.id]
    meals = [m for m in plan.meals if m is not meal]
    meals.append(Meal(day, slot, new.id, fit.p1, fit.p2))
    warnings: list[str] = []
    target_day = max(old_days) + 1
    free = target_day < WEEK_DAYS and not any(m.day == target_day and m.slot == slot for m in meals)
    fits_storage = target_day - old_session.start_day + 1 <= _storage(old)
    moved = free and fits_storage
    if moved:
        meals.append(Meal(target_day, slot, old.id, meal.p1_portions, meal.p2_portions))
    else:
        warnings.append(
            f"Porcja „{old.title}” z dnia {day + 1} została porzucona: brak wolnego dnia po sesji "
            "albo przekroczony czas przechowywania."
        )
    meals.sort(key=lambda m: (m.day, SLOTS.index(m.slot)))
    sessions: list[Session] = []
    for s in plan.sessions:
        if s.slot == slot and s.recipe_id == old.id:
            remaining = [m.day for m in meals if m.slot == slot and m.recipe_id == old.id]
            if not remaining:
                continue
            s = Session(slot, old.id, s.start_day, max(remaining) - s.start_day + 1)
        sessions.append(s)
    sessions.append(Session(slot, new.id, day, 1))
    sessions.sort(key=lambda s: (SLOTS.index(s.slot), s.start_day))
    return PlanResult(tuple(sessions), tuple(meals), tuple(warnings))
