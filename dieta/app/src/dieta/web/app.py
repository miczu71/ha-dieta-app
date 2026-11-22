"""Aplikacja FastAPI (szkielet E1; ekrany dodaje paczka W3)."""

from __future__ import annotations

from fastapi import FastAPI

from ..settings import Settings


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="Dieta", docs_url=None, redoc_url=None)
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
