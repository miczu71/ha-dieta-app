from pathlib import Path

from fastapi.testclient import TestClient

from dieta.settings import Settings
from dieta.web.app import create_app


def test_healthz(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path, log_level="info", dev=True)))
    assert client.get("/healthz").json() == {"status": "ok"}
