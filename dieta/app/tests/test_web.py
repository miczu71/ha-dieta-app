from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from dieta.settings import Settings
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
