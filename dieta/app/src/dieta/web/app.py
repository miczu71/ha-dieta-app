"""Aplikacja FastAPI: ekran „Profile” (cele kcal, makro, waga)."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import nutrition
from ..settings import Settings
from ..store import PERSON_IDS, Profile, Store
from .chart import weight_chart
from .kupujemy import SyncRunner, default_runner, make_router, seconds_until

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
GOAL_LABELS = {"lose": "Redukcja", "maintain": "Utrzymanie", "gain": "Masa"}
PAL_LABELS = {
    1: "siedzący (1,2)",
    2: "lekka aktywność (1,375)",
    3: "umiarkowana (1,55)",
    4: "wysoka (1,725)",
    5: "bardzo wysoka (1,9)",
}
_VERSION_RE = re.compile(r'^version:\s*"?([^"\s]+)"?', re.M)


def app_version() -> str:
    """Wersja z config.yaml (źródło prawdy dla Supervisora); awaryjnie z metadanych pakietu."""
    for parent in WEB_DIR.parents:
        cfg = parent / "config.yaml"
        if cfg.is_file():
            m = _VERSION_RE.search(cfg.read_text(encoding="utf-8"))
            if m:
                return m.group(1)
    try:
        return metadata.version("dieta")
    except metadata.PackageNotFoundError:
        return "dev"


@dataclass
class Card:
    person_id: int
    name: str
    profile: Profile | None
    weight: float | None
    bmr: int | None = None
    tdee: int | None = None
    kcal: int | None = None
    auto_kcal: int | None = None
    macros: nutrition.Macros | None = None
    chart: str = ""
    recent: list[tuple[date, float]] | None = None


def build_card(store: Store, settings: Settings, person_id: int) -> Card:
    profile = store.profile(person_id)
    weight = store.latest_weight(person_id)
    card = Card(person_id, settings.person_name(person_id), profile, weight)
    points = store.weights(person_id, 90)
    card.chart = weight_chart(points, nutrition.trend_7d(points))
    card.recent = list(reversed(points))[:5]
    if profile and weight:
        bmr = nutrition.bmr_mifflin(profile.sex, profile.age, profile.height_cm, weight)
        total = nutrition.tdee(bmr, profile.pal_level)
        auto = nutrition.target_kcal(total, profile.goal, profile.pace_kg_week)
        kcal = profile.kcal_override or round(auto)
        card.bmr, card.tdee, card.auto_kcal, card.kcal = round(bmr), round(total), round(auto), kcal
        card.macros = nutrition.macros(kcal, weight, profile.goal, profile.protein_g_kg, profile.fat_pct)
    return card


async def _nightly(settings: Settings, runner: SyncRunner) -> None:
    """Odświeża snapshot zakupów codziennie o `refresh_time`; błąd jednego przebiegu nie zatrzymuje pętli."""
    while True:
        await asyncio.sleep(seconds_until(datetime.now(), settings.refresh_time))
        try:
            await asyncio.to_thread(runner)
        except Exception as exc:  # noqa: BLE001 — pętla tła ma przeżyć; logujemy tylko typ (bez treści z tokenami)
            log.warning("nocny sync zakończony błędem: %s", type(exc).__name__)


def create_app(settings: Settings, *, sync_runner: SyncRunner | None = None) -> FastAPI:
    store = Store(settings.db_path)
    runner = sync_runner or default_runner(settings, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = None
        if sync_runner is None and settings.zakupy_url and settings.zakupy_token:
            task = asyncio.create_task(_nightly(settings, runner))
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="Dieta", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    version = app_version()
    app.state.version = version
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(make_router(settings, store, templates, runner))

    @app.middleware("http")
    async def no_store(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        if not request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index(request: Request, error: str = "") -> Response:
        cards = [build_card(store, settings, pid) for pid in PERSON_IDS]
        return templates.TemplateResponse(
            request,
            "profiles.html",
            {
                "cards": cards,
                "version": version,
                "error": error,
                "goal_labels": GOAL_LABELS,
                "pal_labels": PAL_LABELS,
                "today": date.today().isoformat(),
                "page_title": "Profile",
                "section": "profile",
                "root": "",
            },
        )

    def _person(person_id: int) -> int:
        if person_id not in PERSON_IDS:
            raise HTTPException(404)
        return person_id

    def _back(error: str = "") -> RedirectResponse:
        # Adres względny: działa pod prefiksem Ingress (/api/hassio_ingress/<token>/).
        return RedirectResponse("./" + (f"?error={error}" if error else ""), status_code=303)

    @app.post("/profile/{person_id}")
    async def save_profile(
        person_id: int,
        sex: str = Form(...),
        age: int = Form(...),
        height_cm: float = Form(...),
        pal_level: int = Form(...),
        goal: str = Form(...),
        pace_kg_week: float = Form(0.0),
        kcal_override: str = Form(""),
        protein_g_kg: str = Form(""),
        fat_pct: float = Form(30.0),
    ) -> Response:
        _person(person_id)
        if (
            sex not in ("m", "f")
            or goal not in GOAL_LABELS
            or pal_level not in nutrition.PAL
            or not 10 <= age <= 120
            or not 100 <= height_cm <= 250
            or not 0 <= pace_kg_week <= 1.5
            or not 15 <= fat_pct <= 50
        ):
            return _back("profil")
        try:
            override = int(kcal_override) if kcal_override.strip() else None
            protein = float(protein_g_kg.replace(",", ".")) if protein_g_kg.strip() else None
        except ValueError:
            return _back("profil")
        if (override is not None and not 800 <= override <= 6000) or (
            protein is not None and not 0.5 <= protein <= 4
        ):
            return _back("profil")
        store.save_profile(
            Profile(
                person_id=person_id,
                sex=cast(Literal["m", "f"], sex),
                age=age,
                height_cm=height_cm,
                pal_level=pal_level,
                goal=cast(Literal["lose", "maintain", "gain"], goal),
                pace_kg_week=pace_kg_week,
                kcal_override=override,
                protein_g_kg=protein,
                fat_pct=fat_pct / 100,
            )
        )
        return _back()

    @app.post("/weight/{person_id}")
    async def add_weight(person_id: int, day: str = Form(...), kg: str = Form(...)) -> Response:
        _person(person_id)
        try:
            when = date.fromisoformat(day)
            value = float(kg.replace(",", "."))
        except ValueError:
            return _back("waga")
        if not 25 <= value <= 400 or when > date.today():
            return _back("waga")
        store.add_weight(person_id, when, value)
        return _back()

    return app
