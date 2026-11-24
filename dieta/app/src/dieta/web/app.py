"""Aplikacja FastAPI: ekran „Profile” (cele kcal, makro, waga)."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from importlib import metadata
from pathlib import Path
from typing import Literal, cast

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import nutrition, push, zepp
from ..names import person_name, validate_name
from ..pipeline import CrawlRunner, default_crawl_runner
from ..plan_service import monday
from ..settings import Settings
from ..store import PERSON_IDS, BodyRow, Profile, Store
from ..targets import KCAL_MAX, KCAL_MIN, kcal_suggestion, person_targets
from ..zepp_service import ZeppService
from ..zepp_sync import start as start_zepp_sync
from .catalog import make_catalog_router
from .chart import body_chart, weight_chart
from .dzis import make_dzis_router
from .kupujemy import SyncRunner, default_runner, make_router, seconds_until
from .lista import make_lista_router
from .plan import make_plan_router
from .zepp import banner as zepp_banner
from .zepp import make_zepp_router

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
    saved_name: str
    profile: Profile | None
    weight: float | None
    bmr: int | None = None
    tdee: int | None = None
    kcal: int | None = None
    auto_kcal: int | None = None
    macros: nutrition.Macros | None = None
    chart: str = ""
    recent: list[tuple[date, float]] | None = None
    trend: float | None = None
    suggestion: int | None = None
    bmr_label: str = "Mifflin"
    body_age_days: int | None = None
    body_chart: str = ""
    history_chart: str = ""
    body_rows: list[tuple[str, BodyRow]] = field(default_factory=list)


def _weekly(points: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Średnie tygodniowe; kluczem poniedziałek tygodnia."""
    buckets: dict[date, list[float]] = {}
    for d, kg in points:
        buckets.setdefault(monday(d), []).append(kg)
    return [(mon, sum(v) / len(v)) for mon, v in sorted(buckets.items())]


def build_card(store: Store, settings: Settings, person_id: int) -> Card:
    profile = store.profile(person_id)
    weight = store.latest_weight(person_id)
    saved_name = store.person_names().get(person_id, "")
    card = Card(person_id, saved_name or settings.person_name(person_id), saved_name, profile, weight)
    points = store.weights(person_id, 90)
    card.chart = weight_chart(points, nutrition.trend_7d(points))
    card.recent = list(reversed(points))[:5]
    card.trend = nutrition.trend_rate_kg_week(points)
    target = person_targets(store, settings, person_id)
    if target and profile:
        card.bmr, card.tdee, card.auto_kcal, card.kcal = (
            target.bmr,
            target.tdee,
            target.auto_kcal,
            target.kcal,
        )
        card.macros = target.macros
        card.bmr_label = "Katch-McArdle" if target.bmr_formula == "katch" else "Mifflin"
        card.body_age_days = target.body_age_days
        # Baza = kcal automatyczny: po „Zastosuj” (override) ta sama propozycja znika zamiast się kumulować.
        proposal = kcal_suggestion(profile.goal, profile.pace_kg_week, target.auto_kcal, card.trend)
        card.suggestion = None if proposal == target.kcal else proposal
    body_rows = store.body_measurements(person_id)
    if body_rows:
        card.body_chart = body_chart(body_rows)
        card.history_chart = weight_chart(_weekly(store.weights(person_id, 3650)), [])
        card.body_rows = [
            (datetime.fromtimestamp(r.ts).strftime("%d.%m.%Y"), r) for r in reversed(body_rows[-30:])
        ]
    return card


async def _nightly(settings: Settings, runner: SyncRunner, crawl_runner: CrawlRunner) -> None:
    """Codziennie o `refresh_time`: sync zakupów, potem crawl katalogu; błąd kroku nie zatrzymuje pętli."""
    while True:
        await asyncio.sleep(seconds_until(datetime.now(), settings.refresh_time))
        if settings.zakupy_url and settings.zakupy_token:
            try:
                await asyncio.to_thread(runner)
            except Exception as exc:  # noqa: BLE001 — pętla tła ma przeżyć; logujemy tylko typ (bez tokenów)
                log.warning("nocny sync zakończony błędem: %s", type(exc).__name__)
        try:
            await asyncio.to_thread(crawl_runner, None)
        except Exception as exc:  # noqa: BLE001
            log.warning("nocny crawl katalogu zakończony błędem: %s", type(exc).__name__)


def create_app(
    settings: Settings,
    *,
    sync_runner: SyncRunner | None = None,
    crawl_runner: CrawlRunner | None = None,
    zepp_http: zepp.Http | None = None,
    zepp_now: Callable[[], int] | None = None,
) -> FastAPI:
    store = Store(settings.db_path)
    zepp_service = ZeppService(store, http=zepp_http or zepp.default_http, now=zepp_now or time.time)
    runner = sync_runner or default_runner(settings, store)
    crawler = crawl_runner or default_crawl_runner(settings, store)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = []
        if sync_runner is None and crawl_runner is None:
            tasks.append(asyncio.create_task(_nightly(settings, runner, crawler)))
            push_task = push.start(settings, store)
            if push_task is not None:
                tasks.append(push_task)
            zepp_task = start_zepp_sync(zepp_service, store, settings)
            if zepp_task is not None:
                tasks.append(zepp_task)
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Dieta", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings
    version = app_version()
    app.state.version = version
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(make_router(settings, store, templates, runner))
    app.include_router(make_catalog_router(settings, store, templates, crawler))
    app.include_router(make_plan_router(settings, store, templates))
    app.include_router(make_dzis_router(settings, store, templates))
    app.include_router(make_lista_router(settings, store, templates))
    if settings.zepp_enabled:
        app.include_router(make_zepp_router(zepp_service))

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
    async def index(request: Request, error: str = "", zepp: str = "", n: str = "", p: str = "") -> Response:
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
                "today_date": date.today(),
                "page_title": "Profile",
                "section": "profile",
                "root": "",
                "zepp_views": {pid: zepp_service.view(pid) for pid in PERSON_IDS}
                if settings.zepp_enabled
                else None,
                "zepp_banner": zepp_banner(zepp, n, p) if settings.zepp_enabled else None,
                "zepp_time": lambda ts: datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
            },
        )

    def _person(person_id: int) -> int:
        if person_id not in PERSON_IDS:
            raise HTTPException(404)
        return person_id

    def _back(error: str = "", up: int = 1) -> RedirectResponse:
        # Adres względny liczony od URL-a POST-a (``up`` = liczba segmentów katalogów w ścieżce POST-a):
        # działa pod prefiksem Ingress (/api/hassio_ingress/<token>/) i prowadzi na stronę główną.
        return RedirectResponse("../" * up + (f"?error={error}" if error else ""), status_code=303)

    @app.post("/person/{person_id}/name")
    async def save_name(person_id: int, name: str = Form("")) -> Response:
        _person(person_id)
        other = person_name(store, settings, 2 if person_id == 1 else 1)
        valid = validate_name(name, other)
        if valid is None:
            return _back("nazwa", up=2)
        store.set_person_name(person_id, valid)
        return _back(up=2)

    @app.post("/profile/{person_id}")
    async def save_profile(
        person_id: int,
        sex: str = Form(...),
        birth_date: str = Form(...),
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
            or not 100 <= height_cm <= 250
            or not 0 <= pace_kg_week <= 1.5
            or not 15 <= fat_pct <= 50
        ):
            return _back("profil")
        try:
            born = date.fromisoformat(birth_date)
        except ValueError:
            return _back("profil")
        try:
            override = int(kcal_override) if kcal_override.strip() else None
            protein = float(protein_g_kg.replace(",", ".")) if protein_g_kg.strip() else None
        except ValueError:
            return _back("profil")
        if (override is not None and not KCAL_MIN <= override <= KCAL_MAX) or (
            protein is not None and not 0.5 <= protein <= 4
        ):
            return _back("profil")
        previous = store.profile(person_id)
        profile = Profile(
            person_id=person_id,
            sex=cast(Literal["m", "f"], sex),
            birth_date=born,
            height_cm=height_cm,
            pal_level=pal_level,
            goal=cast(Literal["lose", "maintain", "gain"], goal),
            pace_kg_week=pace_kg_week,
            kcal_override=override,
            protein_g_kg=protein,
            fat_pct=fat_pct / 100,
            birth_date_approx=bool(previous and previous.birth_date_approx and previous.birth_date == born),
        )
        today = date.today()
        if born > today or not 10 <= profile.age_on(today) <= 120:
            return _back("profil")
        store.save_profile(profile)
        return _back()

    @app.post("/profile/{person_id}/kcal")
    async def save_kcal(person_id: int, kcal: str = Form("")) -> Response:
        _person(person_id)
        profile = store.profile(person_id)
        try:
            value = int(kcal)
        except ValueError:
            return _back("profil", up=2)
        if profile is None or not KCAL_MIN <= value <= KCAL_MAX:
            return _back("profil", up=2)
        store.save_profile(replace(profile, kcal_override=value))
        return _back(up=2)

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
