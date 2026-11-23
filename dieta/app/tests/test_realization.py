from datetime import date, timedelta
from pathlib import Path

import pytest
from test_web_plan import _demo

from dieta import plan_service as svc
from dieta import realization as rz
from dieta.settings import Settings
from dieta.store import NewPlanMeal, NewPlanSession, Store

WEEK = date(2026, 11, 23)  # poniedziałek
TODAY = date(2026, 11, 25)  # środa


def _setup(
    tmp_path: Path, *, approve: bool = True, people: tuple[int, ...] = (1, 2)
) -> tuple[Store, Settings]:
    settings = Settings(
        data_dir=tmp_path, log_level="info", dev=True, person_1_name="Ala", person_2_name="Olek"
    )
    store = Store(settings.db_path)
    _demo(store, people)
    svc.generate(store, settings, WEEK, seed=1)
    if approve:
        svc.approve(store, WEEK)
    return store, settings


def _meal(store: Store, day: date, slot: str):  # type: ignore[no-untyped-def]
    plan = store.plan(WEEK)
    assert plan is not None
    return next(m for m in store.plan_meals(plan.id) if m.day == day.weekday() and m.slot == slot)


def test_eaten_stores_snapshot_from_recipe(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    out = rz.mark(store, settings, WEEK, 1, "lunch", "eaten", TODAY)
    assert out.notice == rz.NOTICE_OK
    (row,) = store.meal_log(WEEK, WEEK)
    portions = _meal(store, WEEK, "lunch").p1_portions
    assert row.status == "eaten"
    assert row.portions == portions
    assert row.title.startswith("Danie lunch")
    assert portions is not None
    # przepisy demo: kcal 380..410 na porcję, białko 25, tłuszcz 12, węgle 40
    assert row.kcal == pytest.approx(portions * 380, abs=portions * 40)
    assert row.protein == pytest.approx(portions * 25)
    assert row.fat == pytest.approx(portions * 12)
    assert row.carbs == pytest.approx(portions * 40)


def test_overwrite_and_clear(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    rz.mark(store, settings, WEEK, 1, "lunch", "eaten", TODAY)
    rz.mark(store, settings, WEEK, 1, "lunch", "skipped", TODAY)
    (row,) = store.meal_log(WEEK, WEEK)
    assert (row.status, row.kcal, row.protein) == ("skipped", 0.0, None)
    assert rz.clear(store, settings, WEEK, 1, "lunch", TODAY).notice == rz.NOTICE_OK
    assert store.meal_log(WEEK, WEEK) == []
    assert rz.clear(store, settings, WEEK, 1, "lunch", TODAY).notice == rz.NOTICE_OK


def test_snapshot_survives_plan_change(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    rz.mark(store, settings, WEEK, 1, "lunch", "eaten", TODAY)
    before = rz.day_log(store, settings, WEEK, TODAY).people[0]
    plan = store.plan(WEEK)
    assert plan is not None
    # wymiana sesji: ten sam slot dostaje inny przepis, stary plan_meal znika
    store.replace_plan_contents(
        plan.id,
        [NewPlanSession("lunch", 1, 0, 1)],
        [NewPlanMeal(0, "lunch", 0, 1.0, 1.0)],
    )
    after = rz.day_log(store, settings, WEEK, TODAY).people[0]
    assert before is not None and after is not None
    assert after.eaten_kcal == before.eaten_kcal > 0
    assert rz.week_balance(store, settings, WEEK, TODAY).people[0].eaten_kcal == before.eaten_kcal  # type: ignore[union-attr]


def test_window(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    assert rz.mark(store, settings, TODAY - timedelta(days=1), 1, "lunch", "skipped", TODAY).notice == "ok"
    assert (
        rz.mark(store, settings, TODAY - timedelta(days=8), 1, "lunch", "skipped", TODAY).notice
        == "out_of_window"
    )
    assert (
        rz.mark(store, settings, TODAY + timedelta(days=1), 1, "lunch", "skipped", TODAY).notice
        == "out_of_window"
    )
    assert rz.clear(store, settings, TODAY + timedelta(days=1), 1, "lunch", TODAY).notice == "out_of_window"


def test_draft_and_no_plan(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path, approve=False)
    assert rz.mark(store, settings, WEEK, 1, "lunch", "eaten", TODAY).notice == rz.NOTICE_NOT_APPROVED
    assert store.meal_log(WEEK, WEEK) == []
    prev = WEEK - timedelta(days=3)  # niedziela poprzedniego tygodnia, bez planu
    assert rz.mark(store, settings, prev, 1, "lunch", "eaten", TODAY).notice == rz.NOTICE_NO_PLAN
    assert rz.day_log(store, settings, WEEK, TODAY).editable is False


def test_person_without_portions_is_no_meal(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path, people=(1,))
    assert rz.mark(store, settings, WEEK, 1, "lunch", "eaten", TODAY).notice == "ok"
    assert rz.mark(store, settings, WEEK, 2, "lunch", "eaten", TODAY).notice == rz.NOTICE_NO_MEAL
    assert rz.mark(store, settings, WEEK, 1, "snack1", "eaten", TODAY).notice == rz.NOTICE_NO_MEAL


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "bogus"},
        {"person_id": 3},
        {"slot": "brunch"},
        {"status": "substitute"},
        {"status": "substitute", "name": "  ", "kcal": 100.0},
        {"status": "substitute", "name": "x" * 81, "kcal": 100.0},
        {"status": "substitute", "name": "Kanapka"},
        {"status": "substitute", "name": "Kanapka", "kcal": -1.0},
        {"status": "substitute", "name": "Kanapka", "kcal": 5001.0},
        {"status": "substitute", "name": "Kanapka", "kcal": float("nan")},
        {"status": "substitute", "name": "Kanapka", "kcal": 100.0, "protein": 1001.0},
        {"status": "substitute", "name": "Kanapka", "kcal": 100.0, "fat": -2.0},
    ],
)
def test_bad_input_writes_nothing(tmp_path: Path, kwargs: dict[str, object]) -> None:
    store, settings = _setup(tmp_path)
    args: dict[str, object] = {"person_id": 1, "slot": "lunch", "status": "eaten"}
    args.update(kwargs)
    out = rz.mark(store, settings, WEEK, today=TODAY, **args)  # type: ignore[arg-type]
    assert out.notice == rz.NOTICE_BAD
    assert store.meal_log(WEEK, WEEK) == []


def test_substitute_keeps_planned_recipe_and_values(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    out = rz.mark(
        store, settings, WEEK, 2, "dinner", "substitute", TODAY, name=" Kanapka ", kcal=450.0, protein=20.0
    )
    assert out.notice == "ok"
    (row,) = store.meal_log(WEEK, WEEK)
    assert (row.title, row.kcal, row.protein, row.fat) == ("Kanapka", 450.0, 20.0, None)
    assert row.recipe_id is not None
    assert row.portions == _meal(store, WEEK, "dinner").p2_portions


def test_day_balance_and_unanswered(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    day = WEEK
    log0 = rz.day_log(store, settings, day, TODAY)
    assert len(log0.meals) == 3
    assert log0.people[0] is not None and log0.people[0].unanswered == 3
    assert log0.people[0].eaten_kcal == 0
    rz.mark(store, settings, day, 1, "breakfast", "eaten", TODAY)
    rz.mark(store, settings, day, 1, "lunch", "skipped", TODAY)
    rz.mark(store, settings, day, 1, "dinner", "substitute", TODAY, name="Pizza", kcal=700.0)
    log = rz.day_log(store, settings, day, TODAY)
    p1, p2 = log.people
    assert p1 is not None and p2 is not None
    portions = _meal(store, day, "breakfast").p1_portions
    assert portions is not None
    assert p1.unanswered == 0
    assert (
        p1.eaten_kcal == pytest.approx(portions * 300, abs=portions * 40 + 700 + 1) and p1.eaten_kcal >= 700
    )
    assert p1.eaten_protein == pytest.approx(portions * 25)  # tylko eaten; zamiennik bez makro
    assert p2.unanswered == 3 and p2.eaten_kcal == 0
    assert log.editable is True
    by_slot = {m.slot: m for m in log.meals}
    assert by_slot["lunch"].per_person[0].status == "skipped"  # type: ignore[union-attr]
    assert by_slot["dinner"].per_person[0].sub_name == "Pizza"  # type: ignore[union-attr]
    assert by_slot["lunch"].per_person[1].status is None  # type: ignore[union-attr]
    assert rz.unanswered_for(store, settings, day, TODAY) == {1: 0, 2: 3}
    assert rz.unanswered_for(store, settings, TODAY + timedelta(days=1), TODAY) == {1: 0, 2: 0}


def test_day_log_without_profile_has_no_person(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path, people=(1,))
    assert rz.day_log(store, settings, WEEK, TODAY).people[1] is None


def test_week_balance_targets_only_up_to_today_and_leftovers(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    plan = store.plan(WEEK)
    assert plan is not None
    multi = {s.recipe_id for s in store.plan_sessions(plan.id) if s.days > 1}
    assert multi, "demo plan powinien mieć sesje wielodniowe"
    lunch_portions = _meal(store, WEEK, "lunch").p1_portions
    rz.mark(store, settings, WEEK, 1, "lunch", "skipped", TODAY)
    rz.mark(store, settings, WEEK, 1, "dinner", "substitute", TODAY, name="Pizza", kcal=600.0)
    bal = rz.week_balance(store, settings, WEEK, TODAY)
    p1 = bal.people[0]
    assert p1 is not None and bal.plan_status == "approved"
    assert len(p1.days) == 7
    assert [d.future for d in p1.days] == [False, False, False, True, True, True, True]
    target = p1.days[0].target_kcal
    assert p1.target_kcal == pytest.approx(3 * target)
    assert p1.eaten_kcal == 600.0
    dinner_portions = _meal(store, WEEK, "dinner").p1_portions
    assert lunch_portions is not None and dinner_portions is not None
    assert p1.leftover_portions == pytest.approx(lunch_portions + dinner_portions)
    # bez odpowiedzi: dni <= dziś (3 dni x 3 posiłki) minus 2 odhaczone
    assert p1.unanswered == 9 - 2
    assert bal.prev_week == WEEK - timedelta(days=7) and bal.next_week == WEEK + timedelta(days=7)
    assert p1.days[0].name == "Pn"


def test_week_balance_without_plan(tmp_path: Path) -> None:
    store, settings = _setup(tmp_path)
    bal = rz.week_balance(store, settings, WEEK + timedelta(days=7), WEEK + timedelta(days=7))
    assert bal.plan_status is None
    assert bal.people[0] is not None and bal.people[0].leftover_portions == 0
    assert bal.people[0].unanswered == 0
