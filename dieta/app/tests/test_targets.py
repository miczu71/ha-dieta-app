from datetime import date, datetime
from pathlib import Path

import pytest

from dieta import nutrition
from dieta.settings import Settings
from dieta.store import BodyRow, Profile, Store
from dieta.targets import kcal_suggestion, person_targets
from dieta.web.app import build_card


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path, log_level="info", dev=True)


def _profile(person_id: int, **kw: object) -> Profile:
    base: dict[str, object] = {
        "person_id": person_id, "sex": "f",
        "birth_date": date(date.today().year - 35, 1, 1), "height_cm": 168.0, "pal_level": 3,
        "goal": "lose", "pace_kg_week": 0.4, "kcal_override": None, "protein_g_kg": None, "fat_pct": 0.3,
    }  # fmt: skip
    base.update(kw)
    return Profile(**base)  # type: ignore[arg-type]


def test_none_without_profile_or_weight(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    s = _settings(tmp_path)
    assert person_targets(store, s, 1) is None
    store.save_profile(_profile(1))
    assert person_targets(store, s, 1) is None  # brak wagi
    store.add_weight(2, date(2026, 11, 1), 70.0)
    assert person_targets(store, s, 2) is None  # brak profilu


def test_targets_match_direct_nutrition_computation(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_profile(1))
    store.add_weight(1, date(2026, 11, 1), 70.0)
    t = person_targets(store, _settings(tmp_path), 1)
    assert t is not None
    bmr = nutrition.bmr_mifflin("f", 35, 168.0, 70.0)
    auto = nutrition.target_kcal(nutrition.tdee(bmr, 3), "lose", 0.4)
    m = nutrition.macros(round(auto), 70.0, "lose", None, 0.3)
    assert (t.kcal, t.protein_g, t.fat_g, t.carb_g) == (round(auto), m.protein_g, m.fat_g, m.carb_g)


def test_build_card_equivalent_with_and_without_override(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    s = _settings(tmp_path)
    store.save_profile(_profile(1))
    store.save_profile(_profile(2, sex="m", kcal_override=2400, protein_g_kg=1.8))
    for pid, kg in ((1, 70.0), (2, 90.0)):
        store.add_weight(pid, date(2026, 11, 1), kg)
    for pid in (1, 2):
        card = build_card(store, s, pid)
        t = person_targets(store, s, pid)
        assert t is not None
        profile = store.profile(pid)
        weight = store.latest_weight(pid)
        assert profile is not None and weight is not None
        bmr = nutrition.bmr_mifflin(profile.sex, profile.age_on(date.today()), profile.height_cm, weight)
        total = nutrition.tdee(bmr, profile.pal_level)
        auto = nutrition.target_kcal(total, profile.goal, profile.pace_kg_week)
        kcal = profile.kcal_override or round(auto)
        assert (card.bmr, card.tdee, card.auto_kcal, card.kcal) == (
            round(bmr),
            round(total),
            round(auto),
            kcal,
        )
        assert card.macros == nutrition.macros(
            kcal, weight, profile.goal, profile.protein_g_kg, profile.fat_pct
        )
    assert build_card(store, s, 2).kcal == 2400


def test_build_card_without_data_has_no_targets(tmp_path: Path) -> None:
    card = build_card(Store(tmp_path / "d.db"), _settings(tmp_path), 1)
    assert card.kcal is None and card.macros is None and card.bmr is None


def _body_ts(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, 12).timestamp())


def _body(day: date, fat_pct: float | None = 20.0) -> BodyRow:
    return BodyRow(ts=_body_ts(day), source="zepp", weight_kg=70.0, fat_pct=fat_pct)


def test_without_body_composition_uses_mifflin(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_profile(1))
    store.add_weight(1, date(2026, 11, 20), 70.0)
    t = person_targets(store, _settings(tmp_path), 1, today=date(2026, 11, 20))
    assert t is not None and t.bmr_formula == "mifflin" and t.body_age_days is None


def test_body_composition_14_days_old_uses_katch(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_profile(1))
    store.add_weight(1, date(2026, 11, 20), 70.0)
    store.add_body_measurements(1, [_body(date(2026, 11, 6))])
    t = person_targets(store, _settings(tmp_path), 1, today=date(2026, 11, 20))
    assert t is not None and t.bmr_formula == "katch" and t.body_age_days == 14
    # lbm = 70 * 0,8 = 56 kg; 370 + 21,6 * 56 = 1579,6
    assert t.bmr == 1580


def test_body_composition_15_days_old_uses_mifflin(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_profile(1))
    store.add_weight(1, date(2026, 11, 20), 70.0)
    store.add_body_measurements(1, [_body(date(2026, 11, 5))])
    t = person_targets(store, _settings(tmp_path), 1, today=date(2026, 11, 20))
    assert t is not None and t.bmr_formula == "mifflin" and t.body_age_days is None


def test_body_row_without_fat_is_ignored(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_profile(1))
    store.add_weight(1, date(2026, 11, 20), 70.0)
    store.add_body_measurements(1, [_body(date(2026, 11, 19), fat_pct=None)])
    t = person_targets(store, _settings(tmp_path), 1, today=date(2026, 11, 20))
    assert t is not None and t.bmr_formula == "mifflin"


def test_suggestion_lose_too_slow_reduces_kcal() -> None:
    # oczekiwane -0,5; trend -0,2 → delta 0,3 → 2200 - 330 = 1870
    assert kcal_suggestion("lose", 0.5, 2200, -0.2) == 1870


def test_suggestion_lose_on_track_is_none() -> None:
    assert kcal_suggestion("lose", 0.5, 2200, -0.5) is None


def test_suggestion_maintain_drifting_up_reduces_kcal() -> None:
    # delta 0,5 → 2000 - 550 = 1450 → zaokrąglenie do 10: 1450
    assert kcal_suggestion("maintain", 0.0, 2000, 0.5) == 1450


def test_suggestion_gain_too_slow_increases_kcal() -> None:
    # oczekiwane +0,25; trend 0 → delta -0,25 → granica, brak zmiany
    assert kcal_suggestion("gain", 0.25, 2500, 0.0) is None
    # oczekiwane +0,5; trend 0 → delta -0,5 → 2500 + 550 = 3050
    assert kcal_suggestion("gain", 0.5, 2500, 0.0) == 3050


def test_suggestion_none_without_trend() -> None:
    assert kcal_suggestion("lose", 0.5, 2200, None) is None


def test_suggestion_threshold_is_inclusive() -> None:
    # |delta| == 0,25 → None; 0,26 → zmiana
    assert kcal_suggestion("maintain", 0.0, 2000, 0.25) is None
    assert kcal_suggestion("maintain", 0.0, 2000, 0.26) == 1710


@pytest.mark.parametrize("fat", [0.0, 1.0, 61.0, 100.0, 120.0, -5.0])
def test_implausible_fat_pct_falls_back_to_mifflin(tmp_path: Path, fat: float) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_profile(1))
    store.add_weight(1, date(2026, 11, 20), 70.0)
    store.add_body_measurements(1, [_body(date(2026, 11, 19), fat_pct=fat)])
    t = person_targets(store, _settings(tmp_path), 1, today=date(2026, 11, 20))
    assert t is not None and t.bmr_formula == "mifflin"


def test_suggestion_is_clamped_to_kcal_range() -> None:
    low = kcal_suggestion("lose", 0.5, 1500, 1.5)
    assert low == 800
    high = kcal_suggestion("gain", 0.5, 5900, -2.0)
    assert high == 6000
    assert kcal_suggestion("lose", 0.5, 800, 1.5) is None  # już na dolnej granicy → brak propozycji
