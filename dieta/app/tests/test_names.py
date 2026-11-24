import sqlite3
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dieta import plan_service as svc
from dieta import push
from dieta.names import person_name, validate_name
from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import _MIGRATIONS, Profile, RecipeMeta, Store
from dieta.web.app import create_app


def _recipe(sid: str, title: str, kcal: float) -> Recipe:
    return Recipe("respo", sid, title, f"https://example.org/{sid}", None, 2, kcal, 25, 40, 12, 30, (), None,
                  (RawIngredient("1 ryż", 1, None, "ryż"),))  # fmt: skip


def _demo(store: Store) -> None:
    for pid in (1, 2):
        store.save_profile(
            Profile(pid, "f", date(date.today().year - 30, 1, 1), 165.0, 2, "maintain", 0.0, None, None, 0.3)
        )
        store.add_weight(pid, date(2026, 11, 1), 70.0)
    for category, base in (("lunch", 380.0), ("dinner", 420.0), ("breakfast", 300.0)):
        for i in range(4):
            store.upsert_recipe(
                _recipe(f"{category}{i}", f"Danie {category} {i}", base + 10 * i),
                RecipeMeta(category, 3, True, "rule"),
            )


def _settings(tmp_path: Path, **kw: str) -> Settings:
    return Settings(data_dir=tmp_path, log_level="info", dev=True, **kw)


def test_migration_v9_to_v10_creates_person_and_backup(tmp_path: Path) -> None:
    path = tmp_path / "dieta.db"
    conn = sqlite3.connect(path)
    for version, script in enumerate(_MIGRATIONS[:9], start=1):
        conn.executescript(f"BEGIN;{script}PRAGMA user_version = {version};COMMIT;")
    conn.execute("INSERT INTO profile VALUES (1,'m',30,180,2,'lose',0.5,NULL,NULL,0.3)")
    conn.execute("INSERT INTO meta VALUES ('k','v')")
    conn.commit()
    conn.close()
    store = Store(path)
    with sqlite3.connect(path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 11
        tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "person" in tables
    assert store.get_meta("k") == "v"
    assert store.profile(1) is not None
    backup = tmp_path / "dieta.db.pre-v10"
    assert backup.exists()
    with sqlite3.connect(backup) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 9


def test_set_and_clear_person_name(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    assert store.person_names() == {}
    store.set_person_name(1, "Ala")
    store.set_person_name(2, "Ola")
    store.set_person_name(1, "Ania")
    assert store.person_names() == {1: "Ania", 2: "Ola"}
    store.set_person_name(1, "")
    assert store.person_names() == {2: "Ola"}
    with pytest.raises(ValueError):
        store.set_person_name(3, "X")


def test_validate_name() -> None:
    assert validate_name("  Ala  ", "Ola") == "Ala"
    assert validate_name("a" * 30, "Ola") == "a" * 30
    assert validate_name("a" * 31, "Ola") is None
    assert validate_name("   ", "Ola") == ""
    assert validate_name("", "Ola") == ""
    assert validate_name("ola", "Ola") is None
    assert validate_name("Ola", "ola") is None


def test_name_chain_db_over_option_over_default(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    plain = _settings(tmp_path)
    opt = _settings(tmp_path, person_1_name="Opcja")
    assert person_name(store, plain, 1) == "Osoba 1"
    assert person_name(store, opt, 1) == "Opcja"
    store.set_person_name(1, "Baza")
    assert person_name(store, opt, 1) == "Baza"
    assert person_name(store, opt, 2) == "Osoba 2"


def _client(tmp_path: Path) -> tuple[TestClient, Store, Settings]:
    s = _settings(tmp_path)
    store = Store(s.db_path)
    app = create_app(s, sync_runner=lambda: 0, crawl_runner=lambda src: {})
    return TestClient(app, follow_redirects=False), store, s


def test_post_name_ok_and_duplicate(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    r = client.post("/person/1/name", data={"name": " Ala "})
    assert r.status_code == 303 and r.headers["location"] == "../../"
    assert store.person_names() == {1: "Ala"}
    assert "Ala" in client.get("/").text
    client.post("/person/2/name", data={"name": "Ola"})
    r = client.post("/person/2/name", data={"name": "ala"})
    assert r.headers["location"] == "../../?error=nazwa"
    assert store.person_names() == {1: "Ala", 2: "Ola"}
    assert "nazwa" in client.get("/?error=nazwa").text.lower()
    assert client.post("/person/3/name", data={"name": "X"}).status_code == 404


def test_name_visible_everywhere_and_after_start(tmp_path: Path) -> None:
    client, store, settings = _client(tmp_path)
    _demo(store)
    svc.generate(store, settings, svc.monday(date.today()), seed=1)
    svc.approve(store, svc.monday(date.today()))
    assert "Osoba 1" in client.get("/dzis").text
    store.set_person_name(1, "Zofia")  # po utworzeniu aplikacji
    for path in ("/dzis", "/bilans", "/plan", "/"):
        assert "Zofia" in client.get(path).text, path


def test_name_in_push_text(tmp_path: Path) -> None:
    s = _settings(tmp_path, push_notify="family", push_time="07:00")
    store = Store(s.db_path)
    _demo(store)
    today = date.today()
    svc.generate(store, s, svc.monday(today), seed=1)
    svc.approve(store, svc.monday(today))
    store.set_person_name(1, "Zofia")
    sent: list[tuple[str, str]] = []

    def sender(service: str, title: str, message: str, path: str) -> bool:
        sent.append((title, message))
        return True

    assert push.run_once(s, store, today, sender) == "sent"
    assert "Zofia" in sent[0][1]


def test_name_input_prefill_and_empty_post(tmp_path: Path) -> None:
    client, store, _ = _client(tmp_path)
    html = client.get("/").text
    assert 'name="name" maxlength="30" value="" placeholder="Osoba 1"' in html
    client.post("/person/1/name", data={"name": ""})
    assert store.person_names() == {}
    client.post("/person/1/name", data={"name": "Ola"})
    assert 'name="name" maxlength="30" value="Ola"' in client.get("/").text
