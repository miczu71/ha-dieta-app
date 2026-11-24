from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from dieta.settings import Settings
from dieta.store import BodyRow, Store
from dieta.web.app import create_app


def _client(tmp_path: Path) -> TestClient:
    s = Settings(data_dir=tmp_path, log_level="info", dev=True, person_1_name="Ala", person_2_name="Ola")
    return TestClient(create_app(s), follow_redirects=False)


PROFILE = {
    "sex": "m",
    "birth_date": f"{date.today().year - 30}-01-01", "height_cm": "180", "pal_level": "3", "goal": "lose",
    "pace_kg_week": "0.5", "kcal_override": "", "protein_g_kg": "", "fat_pct": "30",
}  # fmt: skip


def test_empty_state_prompts_for_data(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/")
    assert r.status_code == 200
    assert "Ala" in r.text and "Ola" in r.text
    assert "Uzupełnij profil" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert "nie stanowią porady medycznej" in r.text


def test_profile_and_weight_give_targets(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert c.post("/profile/1", data=PROFILE).headers["location"] == "../"
    c.post("/weight/1", data={"day": date.today().isoformat(), "kg": "80"})
    html = c.get("/").text
    # BMR 1780, TDEE 2759, redukcja 0,5 kg/tydz. → 2209 kcal
    assert ">2209<" in html and ">1780<" in html and ">2759<" in html
    assert "<svg" in html


def test_manual_override_wins(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data={**PROFILE, "kcal_override": "2000"})
    c.post("/weight/1", data={"day": date.today().isoformat(), "kg": "80,0"})
    html = c.get("/").text
    assert ">2000<" in html and "Ręczny cel" in html


def test_invalid_input_rejected(tmp_path: Path) -> None:
    c = _client(tmp_path)
    assert (
        c.post("/profile/1", data={**PROFILE, "birth_date": f"{date.today().year - 5}-01-01"}).headers[
            "location"
        ]
        == "../?error=profil"
    )
    assert c.post("/weight/1", data={"day": "2999-01-01", "kg": "80"}).headers["location"] == "../?error=waga"
    assert c.post("/weight/1", data={"day": date.today().isoformat(), "kg": "abc"}).status_code == 303
    assert c.post("/weight/3", data={"day": date.today().isoformat(), "kg": "80"}).status_code == 404
    assert "niepoprawna" in c.get("/?error=waga").text


def test_save_redirects_land_on_root(tmp_path: Path) -> None:
    """Adres z ``Location`` rozwiązany względem URL-a POST-a (jak robi przeglądarka) to ``/``."""
    from urllib.parse import urljoin

    c = _client(tmp_path)
    today = date.today().isoformat()
    posts = [
        ("/profile/1", PROFILE),
        ("/profile/1", {**PROFILE, "sex": "x"}),
        ("/weight/1", {"day": today, "kg": "80"}),
        ("/weight/1", {"day": today, "kg": "abc"}),
        ("/person/1/name", {"name": "Ala"}),
        ("/person/1/name", {"name": ""}),
    ]
    for url, data in posts:
        target = urljoin("http://x" + url, c.post(url, data=data).headers["location"])
        assert target.split("?")[0] == "http://x/", (url, target)


def _store(tmp_path: Path) -> Store:
    return Store(Settings(data_dir=tmp_path, log_level="info", dev=True).db_path)


def test_card_shows_formula_and_body_age(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data=PROFILE)
    c.post("/weight/1", data={"day": date.today().isoformat(), "kg": "80"})
    two_days_ago = int((datetime.now() - timedelta(days=2)).timestamp())
    _store(tmp_path).add_body_measurements(
        1, [BodyRow(ts=two_days_ago, source="zepp", weight_kg=80.0, fat_pct=20.0)]
    )
    html = c.get("/").text
    # lbm 80 * 0,8 = 64 kg → 370 + 21,6 * 64 = 1752,4
    assert "BMR: Katch-McArdle" in html and "pomiar składu sprzed 2 dni" in html and ">1752<" in html


def test_card_without_body_shows_mifflin(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data=PROFILE)
    c.post("/weight/1", data={"day": date.today().isoformat(), "kg": "80"})
    html = c.get("/").text
    assert "BMR: Mifflin" in html and "Katch" not in html


def test_suggestion_applies_via_kcal_endpoint(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data=PROFILE)
    today = date.today()
    for back in range(20, -1, -1):
        c.post("/weight/1", data={"day": (today - timedelta(days=back)).isoformat(), "kg": "80"})
    html = c.get("/").text
    # Mifflin 2209 kcal, trend 0 przy tempie 0,5 → 2209 - 550 = 1659 → 1660
    assert 'action="profile/1/kcal"' in html and 'name="kcal" value="1660"' in html


def test_kcal_endpoint_sets_override(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data=PROFILE)
    r = c.post("/profile/1/kcal", data={"kcal": "2100"})
    assert r.headers["location"] == "../../"
    profile = _store(tmp_path).profile(1)
    assert profile is not None and profile.kcal_override == 2100


def test_kcal_endpoint_rejects_out_of_range(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data=PROFILE)
    c.post("/profile/1", data={**PROFILE, "kcal_override": "2000"})
    for bad in ("799", "6001", "abc"):
        r = c.post("/profile/1/kcal", data={"kcal": bad})
        assert r.headers["location"] == "../../?error=profil", bad
    profile = _store(tmp_path).profile(1)
    assert profile is not None and profile.kcal_override == 2000


def test_kcal_endpoint_unknown_person_404(tmp_path: Path) -> None:
    assert _client(tmp_path).post("/profile/3/kcal", data={"kcal": "2100"}).status_code == 404


def test_history_section_only_with_measurements(tmp_path: Path) -> None:
    c = _client(tmp_path)
    c.post("/profile/1", data=PROFILE)
    assert "Historia pomiarów" not in c.get("/").text
    row = BodyRow(
        ts=int(datetime.now().timestamp()), source="zepp", weight_kg=80.0, fat_pct=20.0, muscle_pct=40.0
    )
    _store(tmp_path).add_body_measurements(1, [row])
    html = c.get("/").text
    assert "Historia pomiarów" in html and "<svg" in html
