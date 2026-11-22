"""Ekran „Co kupujemy”: top składniki z paragonów i kolejka zatwierdzania mapowań."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..dictionary import Durability, make_llm_ask
from ..settings import Settings
from ..store import MappingRow, Store
from ..sync import stats, sync
from ..zakupy_client import fetch_purchases

log = logging.getLogger(__name__)

DURABILITY_LABELS: dict[str, str] = {
    "fresh": "świeże (kilka dni)",
    "chilled": "chłodnia (ok. tygodnia)",
    "dry": "suche i puszki (miesiąc)",
    "frozen": "mrożonki (kilka miesięcy)",
}
TOP_LIMIT = 40
SyncRunner = Callable[[], int]


def seconds_until(now: datetime, clock: str) -> float:
    """Sekundy do najbliższego HH:MM (jutro, gdy dziś już minęło)."""
    hour, minute = (int(part) for part in clock.split(":"))
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def default_runner(settings: Settings, store: Store) -> SyncRunner:
    """Prawdziwy sync: Zakupy przez HTTP, mapowanie nieznanych przez LLM (jeśli skonfigurowany)."""

    lock = threading.Lock()  # ręczny refresh i nocna pętla nie mogą pisać do Store równolegle

    def run() -> int:
        with lock:
            return _run()

    def _run() -> int:
        ask = (
            make_llm_ask(settings.llm_url, settings.llm_key)
            if settings.llm_url and settings.llm_key
            else None
        )
        return sync(store, settings, date.today(), fetch=fetch_purchases, ask=ask)

    return run


def review_queue(store: Store) -> list[MappingRow]:
    """Do decyzji: mapowania `pending` oraz zmapowane składniki bez trwałości."""
    return [
        row
        for row in store.mappings()
        if row.name is not None
        and (row.status == "pending" or (row.status in ("rule", "seed", "ai") and row.durability is None))
    ]


def make_router(
    settings: Settings, store: Store, templates: Jinja2Templates, runner: SyncRunner
) -> APIRouter:
    router = APIRouter()

    def back(path: str, error: str = "", notice: str = "") -> RedirectResponse:
        # Adresy względne: działają pod prefiksem Ingress.
        query = f"?error={error}" if error else (f"?notice={notice}" if notice else "")
        return RedirectResponse(f"{path}{query}", status_code=303)

    def page(request: Request, name: str, **ctx: object) -> Response:
        base = {
            "version": request.app.state.version,
            "durability_labels": DURABILITY_LABELS,
            "configured": bool(settings.zakupy_url and settings.zakupy_token),
            "queue_size": len(review_queue(store)),
            "last_sync_at": store.get_meta("last_sync_at"),
            "last_sync_error": store.get_meta("last_sync_error") or "",
        }
        return templates.TemplateResponse(request, name, {**base, **ctx})

    @router.get("/kupujemy")
    async def kupujemy(request: Request, error: str = "", notice: str = "") -> Response:
        rows = stats(store, settings, date.today())
        return page(
            request,
            "kupujemy.html",
            page_title="Co kupujemy",
            section="kupujemy",
            root="",
            rows=rows[:TOP_LIMIT],
            total=len(rows),
            error=error,
            notice=notice,
        )

    @router.post("/kupujemy/odswiez")
    async def refresh() -> Response:
        try:
            count = await run_in_threadpool(runner)
        except Exception as exc:  # noqa: BLE001 — logujemy tylko typ (komunikat może zawierać nagłówki z tokenem)
            log.warning("ręczny sync zakończony błędem: %s", type(exc).__name__)
            store.set_meta("last_sync_error", "Nieoczekiwany błąd synchronizacji (szczegóły w logu add-onu).")
            return back("../kupujemy", error="sync")
        if count == 0 and (store.get_meta("last_sync_error") or ""):
            return back("../kupujemy", error="sync")
        return back("../kupujemy", notice="ok")

    @router.get("/kupujemy/zatwierdz")
    async def review(request: Request, error: str = "") -> Response:
        return page(
            request,
            "zatwierdz.html",
            page_title="Do zatwierdzenia",
            section="kupujemy",
            root="../",
            queue=review_queue(store),
            error=error,
        )

    @router.post("/kupujemy/zatwierdz")
    async def decide(
        code: str = Form(...),
        action: str = Form(...),
        ingredient: str = Form(""),
        durability: str = Form(""),
    ) -> Response:
        known = {row.code: row for row in review_queue(store)}
        if code not in known:
            return back("zatwierdz", error="nieznany")
        if action == "exclude":
            store.exclude(code)
        elif action == "approve":
            name = " ".join(ingredient.split()).lower()
            if not name or len(name) > 80 or durability not in DURABILITY_LABELS:
                return back("zatwierdz", error="dane")
            store.approve(code, name, cast(Durability, durability))
        else:
            return back("zatwierdz", error="dane")
        return back("zatwierdz")

    return router
