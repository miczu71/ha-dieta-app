import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_plan_service import WEEK, _recipe
from test_web import PROFILE

from dieta import nutrition
from dieta import plan_service as svc
from dieta.settings import Settings
from dieta.store import _MIGRATIONS, Profile, RecipeMeta, Store
from dieta.targets import person_targets
from dieta.web.app import create_app


def _july_year(age: int) -> int:
    """Rok daty „1 lipca” z migracji 11 dla profilu w wieku ``age`` — dziś."""
    t = date.today()
    return t.year - age - (0 if (t.month, t.day) >= (7, 1) else 1)


def _p(born: date, approx: bool = False) -> Profile:
    return Profile(1, "f", born, 165.0, 2, "maintain", 0.0, None, None, 0.3, approx)


def test_age_on_birthday_boundaries() -> None:
    p = _p(date(1990, 6, 15))
    assert p.age_on(date(2026, 6, 15)) == 36
    assert p.age_on(date(2026, 6, 14)) == 35
    assert p.age_on(date(2026, 6, 16)) == 36


def test_age_on_leap_day() -> None:
    p = _p(date(2000, 2, 29))
    assert p.age_on(date(2025, 2, 28)) == 24
    assert p.age_on(date(2025, 3, 1)) == 25
    assert p.age_on(date(2024, 2, 28)) == 23
    assert p.age_on(date(2024, 2, 29)) == 24


def test_age_on_limits() -> None:
    p = _p(date(2016, 11, 24))
    assert p.age_on(date(2026, 11, 24)) == 10
    assert p.age_on(date(2026, 11, 23)) == 9
    q = _p(date(1906, 11, 24))
    assert q.age_on(date(2026, 11, 24)) == 120


def test_migration_v11_converts_age_to_approx_birth_date(tmp_path: Path) -> None:
    path = tmp_path / "dieta.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:10], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute("INSERT INTO profile VALUES (1,'m',30,180,2,'lose',0.5,NULL,NULL,0.3)")
    conn.commit()
    conn.close()
    s = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(path)
    store.add_weight(1, date(2026, 11, 1), 80.0)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 11
        row = check.execute("SELECT birth_date, birth_date_approx, age FROM profile").fetchone()
    assert row == (f"{_july_year(30)}-07-01", 1, 30)
    assert (tmp_path / "dieta.db.pre-v11").exists()
    t = person_targets(store, s, 1, today=date.today())
    assert t is not None
    assert t.bmr == round(nutrition.bmr_mifflin("m", 30, 180.0, 80.0))


def test_save_profile_roundtrip_and_age_column(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.save_profile(_p(date(1990, 1, 1), approx=True))
    got = store.profile(1)
    assert got == _p(date(1990, 1, 1), approx=True)
    with sqlite3.connect(tmp_path / "d.db") as c:
        assert c.execute("SELECT age FROM profile").fetchone()[0] == got.age_on(date.today())


def test_person_targets_bmr_changes_exactly_on_birthday(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(tmp_path / "d.db")
    store.save_profile(_p(date(1990, 6, 15)))
    store.add_weight(1, date(2026, 6, 1), 70.0)
    before = person_targets(store, s, 1, today=date(2026, 6, 14))
    on = person_targets(store, s, 1, today=date(2026, 6, 15))
    assert before is not None and on is not None
    assert before.bmr - on.bmr == 5  # 5 kcal na rok wieku (Mifflin)


def test_plan_keeps_params_json_after_birthday(tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, log_level="info", dev=True)
    store = Store(s.db_path)
    store.save_profile(_p(date(1990, 11, 24)))  # urodziny dziś (2026-11-24), po tygodniu planu
    store.add_weight(1, date(2026, 11, 1), 70.0)
    for cat, kcal in (("lunch", 380.0), ("dinner", 420.0), ("breakfast", 300.0)):
        for i in range(4):
            store.upsert_recipe(
                _recipe(f"{cat}{i}", f"{cat} {i}", kcal + 10 * i), RecipeMeta(cat, 3, True, "rule")
            )
    svc.generate(store, s, WEEK, seed=1)
    plan = store.plan(WEEK)
    assert plan is not None
    before = plan.params_json
    store.save_profile(_p(date(1980, 11, 24)))  # inny wiek niczego nie przelicza wstecz
    svc.view(store, s, WEEK)
    after = store.plan(WEEK)
    assert after is not None and after.params_json == before


def _client(tmp_path: Path) -> TestClient:
    s = Settings(data_dir=tmp_path, log_level="info", dev=True, person_1_name="Ala", person_2_name="Ola")
    return TestClient(create_app(s), follow_redirects=False)


@pytest.mark.parametrize(
    "born",
    ["2999-01-01", "9999-12-31", "abc", "2024-13-40"],
)
def test_route_rejects_bad_dates(tmp_path: Path, born: str) -> None:
    c = _client(tmp_path)
    assert c.post("/profile/1", data={**PROFILE, "birth_date": born}).headers["location"] == "./?error=profil"


def test_route_rejects_age_outside_range(tmp_path: Path) -> None:
    c = _client(tmp_path)
    t = date.today()
    ok = c.post("/profile/1", data={**PROFILE, "birth_date": date(t.year - 9, 1, 1).isoformat()})
    assert ok.headers["location"] == "./?error=profil"
    old = c.post("/profile/1", data={**PROFILE, "birth_date": date(t.year - 121, 1, 1).isoformat()})
    assert old.headers["location"] == "./?error=profil"
    assert (
        c.post("/profile/1", data={**PROFILE, "birth_date": date(t.year - 10, 1, 1).isoformat()}).headers[
            "location"
        ]
        == "./"
    )


def test_save_clears_approx_flag_and_banner(tmp_path: Path) -> None:
    c = _client(tmp_path)
    store = Store(Settings(data_dir=tmp_path, log_level="info", dev=True).db_path)
    assert "przybliżona" not in c.get("/").text
    store.save_profile(_p(date(1990, 7, 1), approx=True))
    assert "Data przybliżona — wpisz prawdziwą" in c.get("/").text
    c.post("/profile/1", data={**PROFILE, "birth_date": "1990-03-02"})
    html = c.get("/").text
    assert "Data przybliżona" not in html
    assert 'name="birth_date"' in html and "Wiek: " in html
    got = store.profile(1)
    assert got is not None and not got.birth_date_approx and got.birth_date == date(1990, 3, 2)


def test_save_keeps_approx_flag_when_date_unchanged(tmp_path: Path) -> None:
    c = _client(tmp_path)
    store = Store(Settings(data_dir=tmp_path, log_level="info", dev=True).db_path)
    store.save_profile(_p(date(1990, 7, 1), approx=True))
    c.post("/profile/1", data={**PROFILE, "birth_date": "1990-07-01"})
    got = store.profile(1)
    assert got is not None and got.birth_date_approx
