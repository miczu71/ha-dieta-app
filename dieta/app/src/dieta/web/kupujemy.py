"""Ekran „Co kupujemy”: top składniki z paragonów i kolejka zatwierdzania mapowań."""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import partial
from typing import cast

from fastapi import APIRouter, Form, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..catalog_map import ASK_PROMPT as CATALOG_ASK_PROMPT
from ..dictionary import Durability, llm_ask_from
from ..nutrition_lookup import MAX_KCAL_100G
from ..pipeline import estimate_nutrition
from ..settings import Settings
from ..shelf_queue import sync_shelf_queue
from ..store import (
    MEAL_CATEGORIES,
    IngredientNutrition,
    MappingRow,
    PendingCatalog,
    PendingRecipeMeta,
    RecipeRow,
    Store,
)
from ..sync import map_recipe_ingredients, stats, sync, sync_catalog, sync_shelf
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
SyncRunner = Callable[..., int]  # runner(force=False); ręczne odświeżenie woła z force=True


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

    def run(force: bool = False) -> int:
        with lock:
            return _run(force)

    def _run(force: bool) -> int:
        count = sync(store, settings, date.today(), fetch=fetch_purchases, ask=llm_ask_from(settings))
        if settings.zakupy_url:
            sync_catalog(
                store,
                settings,
                date.today(),
                ask=llm_ask_from(settings, CATALOG_ASK_PROMPT),
                force=force,
            )
            if not force:
                sync_shelf_queue(store, settings, date.today())  # nocą zlecamy odczyt cen w Zakupach
            sync_shelf(store, settings, date.today())
        return count

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


def ingredient_queue(store: Store) -> list[IngredientNutrition]:
    return store.pending_ingredient_nutrition()


def estimate_queue(store: Store) -> list[RecipeRow]:
    """Szacunki kcal o statusie `pending` (bez odrzuconych)."""
    return store.recipes_by_est_status("pending", {}, frozenset())


def catalog_queue(store: Store) -> list[PendingCatalog]:
    """Mapowania EAN katalogu Frisco o statusie `pending` (kandydat składnika z automatu)."""
    return store.pending_catalog()


SYNC_ERROR_LABELS: tuple[tuple[str, str], ...] = (
    ("catalog_sync_error", "Katalog cen"),
    ("shelf_sync_error", "Ceny półkowe"),
    ("shelf_queue_error", "Kolejka cen"),
)


def sync_errors(store: Store) -> list[str]:
    """Komunikaty o niepustych błędach synchronizacji cen (katalog, półka, kolejka)."""
    return [f"{label}: {text}" for key, label in SYNC_ERROR_LABELS if (text := store.get_meta(key))]


def queue_size(store: Store) -> int:
    return (
        len(review_queue(store))
        + len(recipe_queue(store))
        + len(ingredient_queue(store))
        + len(catalog_queue(store))
        + store.count_pending_estimates()
    )


def _nutrition_values(*raw: str) -> tuple[float, ...] | None:
    """kcal, białko, węgle, tłuszcz na 100 g: liczby skończone ≥ 0, kcal ≤ MAX_KCAL_100G."""
    try:
        values = tuple(float(x.replace(",", ".")) for x in raw)
    except ValueError:
        return None
    if any(not math.isfinite(v) or v < 0 for v in values) or values[0] > MAX_KCAL_100G:
        return None
    return values


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
            sync_errors=sync_errors(store),
        )

    @router.post("/kupujemy/odswiez")
    async def refresh() -> Response:
        try:
            count = await run_in_threadpool(partial(runner, force=True))
        except Exception as exc:  # noqa: BLE001 — logujemy tylko typ (komunikat może zawierać nagłówki z tokenem)
            log.warning("ręczny sync zakończony błędem: %s", type(exc).__name__)
            store.set_meta("last_sync_error", "Nieoczekiwany błąd synchronizacji (szczegóły w logu add-onu).")
            return back("../kupujemy", error="sync")
        if count == 0 and (store.get_meta("last_sync_error") or ""):
            return back("../kupujemy", error="sync")
        return back("../kupujemy", notice="ok")

    @router.post("/kupujemy/ceny")
    async def refresh_prices() -> Response:
        try:
            count = await run_in_threadpool(
                partial(sync_shelf_queue, store, settings, date.today(), plan_only=True)
            )
        except Exception as exc:  # noqa: BLE001 — logujemy tylko typ (komunikat może zawierać nagłówki z tokenem)
            log.warning("zlecenie cen planu zakończone błędem: %s", type(exc).__name__)
            return back("../kupujemy", error="ceny")
        if store.get_meta("shelf_queue_error") or "":
            return back("../kupujemy", error="ceny")
        status = store.get_meta("shelf_queue_status") or ""
        if status == "busy":
            return back("../kupujemy", notice="ceny-busy")
        if status == "empty":
            return back("../kupujemy", notice="ceny-empty")
        return back("../kupujemy", notice=f"ceny-{count}")

    @router.get("/kupujemy/zatwierdz")
    def review(request: Request, error: str = "") -> Response:
        return page(
            request,
            "zatwierdz.html",
            page_title="Do zatwierdzenia",
            section="kupujemy",
            root="../",
            queue=review_queue(store),
            catalog_queue=catalog_queue(store),
            recipe_queue=recipe_queue(store),
            ing_queue=ingredient_queue(store),
            est_queue=estimate_queue(store),
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

    @router.post("/kupujemy/zatwierdz-katalog")
    async def decide_catalog(
        ean: str = Form(...),
        ingredient: str = Form(""),
        action: str = Form(...),
    ) -> Response:
        if ean not in {p.ean for p in catalog_queue(store)}:
            return back("zatwierdz", error="nieznany")
        if action == "exclude":
            store.exclude_catalog(ean)
        elif action == "approve":
            name = " ".join(ingredient.split()).lower()
            if not name or len(name) > 80:
                return back("zatwierdz", error="dane")
            store.approve_catalog(ean, name)
        else:
            return back("zatwierdz", error="dane")
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

    @router.post("/kupujemy/zatwierdz-skladnik")
    async def decide_ingredient(
        ingredient_id: int = Form(...),
        kcal: str = Form(...),
        protein: str = Form(...),
        carbs: str = Form(...),
        fat: str = Form(...),
    ) -> Response:
        current = store.ingredient_nutrition().get(ingredient_id)
        if current is None or current.status != "pending":
            return back("zatwierdz", error="nieznany")
        values = _nutrition_values(kcal, protein, carbs, fat)
        if values is None:
            return back("zatwierdz", error="odzywcze")
        store.save_ingredient_nutrition(
            ingredient_id,
            kcal=values[0],
            protein=values[1],
            carbs=values[2],
            fat=values[3],
            piece_g=current.piece_g,
            source="manual",
            confidence="high",
            status="approved",
        )
        # przelicza przepisy bez kcal ze źródła (bez LLM i sieci: ask=None, fetch=None)
        await run_in_threadpool(estimate_nutrition, store, None, None)
        return back("zatwierdz")

    @router.post("/kupujemy/zatwierdz-szacunek")
    async def decide_estimate(recipe_id: int = Form(...), action: str = Form(...)) -> Response:
        if not store.is_estimate_pending(recipe_id):
            return back("zatwierdz", error="nieznany")
        if action == "approve":
            if not store.set_recipe_estimate_status(recipe_id, "approved"):
                return back("zatwierdz", error="dane")
        elif action == "dismiss":
            store.set_flag(recipe_id, "dismissed", True)
        else:
            return back("zatwierdz", error="dane")
        return back("zatwierdz")

    return router
