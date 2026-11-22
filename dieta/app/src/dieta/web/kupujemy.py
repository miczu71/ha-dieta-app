"""Ekran „Co kupujemy”: top składniki z paragonów i kolejka zatwierdzania mapowań."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..dictionary import Durability, llm_ask_from
from ..settings import Settings
from ..store import MEAL_CATEGORIES, MappingRow, PendingRecipeMeta, Store
from ..sync import map_recipe_ingredients, stats, sync
from ..zakupy_client import fetch_purchases

log = logging.getLogger(__name__)

DURABILITY_LABELS: dict[str, str] = {
    "fresh": "świeże (kilka dni)",
    "chilled": "chłodnia (ok. tygodnia)",
    "dry": "suche i puszki (miesiąc)",
    "frozen": "mrożonki (kilka miesięcy)",
}
MEAL_LABELS: dict[str, str] = {
    "breakfast": "śniadanie",
    "lunch": "obiad",
    "dinner": "kolacja",
    "snack": "przekąska",
    "dessert": "deser",
}
TOP_LIMIT = 40
ING_PREFIX = "ing:"
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
        return sync(store, settings, date.today(), fetch=fetch_purchases, ask=llm_ask_from(settings))

    return run


def review_queue(store: Store) -> list[MappingRow]:
    """Do decyzji: mapowania `pending` oraz zmapowane składniki bez trwałości.

    Składniki przepisów (kod ``ing:<nazwa>``) nie mają zakupu, więc nazwę biorą z ``source_name``
    i trafiają tu tylko jako ``pending``.
    """
    queue: list[MappingRow] = []
    for row in store.mappings():
        recipe_ing = row.code.startswith(ING_PREFIX)
        if row.name is None and recipe_ing and row.source_name:
            row = replace(row, name=row.source_name)
        # brak trwałości składnika przepisu nie blokuje znajomości — do kolejki tylko `pending`
        no_durability = not recipe_ing and row.status in ("rule", "seed", "ai") and row.durability is None
        if row.name is not None and (row.status == "pending" or no_durability):
            queue.append(row)
    return queue


def recipe_queue(store: Store) -> list[PendingRecipeMeta]:
    return store.pending_recipe_meta()


def queue_size(store: Store) -> int:
    return len(review_queue(store)) + len(recipe_queue(store))


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
            "queue_size": queue_size(store),
            "last_sync_at": store.get_meta("last_sync_at"),
            "last_sync_error": store.get_meta("last_sync_error") or "",
        }
        return templates.TemplateResponse(request, name, {**base, **ctx})

    @router.get("/kupujemy")
    def kupujemy(request: Request, error: str = "", notice: str = "") -> Response:
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
    def review(request: Request, error: str = "") -> Response:
        return page(
            request,
            "zatwierdz.html",
            page_title="Do zatwierdzenia",
            section="kupujemy",
            root="../",
            queue=review_queue(store),
            recipe_queue=recipe_queue(store),
            meal_labels=MEAL_LABELS,
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
        if code.startswith(ING_PREFIX):
            await run_in_threadpool(map_recipe_ingredients, store, None)  # powiąż z przepisami
        return back("zatwierdz")

    @router.post("/kupujemy/zatwierdz-przepis")
    async def decide_recipe(
        recipe_id: int = Form(...),
        category: str = Form(...),
        storage_days: int = Form(...),
        freezable: str = Form(""),
    ) -> Response:
        if recipe_id not in {p.id for p in recipe_queue(store)}:
            return back("zatwierdz", error="nieznany")
        if category not in MEAL_CATEGORIES or not 1 <= storage_days <= 14:
            return back("zatwierdz", error="dane")
        store.approve_recipe_meta(recipe_id, category, storage_days, freezable == "1")
        return back("zatwierdz")

    return router
