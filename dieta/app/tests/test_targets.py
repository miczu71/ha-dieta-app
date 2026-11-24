from datetime import date
from pathlib import Path

from dieta import nutrition
from dieta.settings import Settings
from dieta.store import Profile, Store
from dieta.targets import person_targets
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
