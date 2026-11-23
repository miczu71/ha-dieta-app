"""Ekran „Katalog”: przepisy z kcal/makro na porcję, znajomość względem zakupów, serce/odrzuć, odświeżanie."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from datetime import date
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..match import DEFAULT_BASICS
from ..pipeline import SOURCE_LABELS, CrawlRunner
from ..profile import IngredientStat
from ..settings import Settings
from ..store import MEAL_CATEGORIES, RecipeRow, Store
from ..sync import stats
from .kupujemy import MEAL_LABELS, queue_size

log = logging.getLogger(__name__)

MISSING_SHOWN = 4
KCAL_MODES = ("zrodlo", "szacunek", "niepelny")


def _kcal_match(r: RecipeRow, mode: str) -> bool:
    """Filtr „kcal”: ze źródła / szacunki auto+approved / niepełne szacunki (pending)."""
    if mode == "zrodlo":
        return r.kcal_source == "source"
    if mode == "szacunek":
        return r.kcal_source == "estimate" and r.est_status in ("auto", "approved")
    if mode == "niepelny":
        return r.est_status == "pending"
    return True


def make_catalog_router(
    settings: Settings, store: Store, templates: Jinja2Templates, crawler: CrawlRunner
) -> APIRouter:
    router = APIRouter()
    running: str | None = None
    lock = threading.Lock()

    def stat_map() -> Mapping[str, IngredientStat]:
        return {s.ingredient: s for s in stats(store, settings, date.today())}

    def filters(
        prog: str, kat: str, zrodlo: str, polubione: str, odrzucone: str, kcal: str = ""
    ) -> dict[str, str | int | bool]:
        try:
            threshold = int(prog)
        except ValueError:
            threshold = settings.familiar_threshold
        return {
            "prog": min(100, max(0, threshold)),
            "kat": kat if kat in MEAL_CATEGORIES else "",
            "zrodlo": zrodlo if zrodlo in SOURCE_LABELS else "",
            "polubione": polubione == "1",
            "odrzucone": odrzucone == "1",
            "kcal": kcal if kcal in KCAL_MODES else "",
        }

    def query(f: dict[str, str | int | bool]) -> str:
        pairs = {k: ("1" if v is True else v) for k, v in f.items() if v not in ("", False)}
        pairs["prog"] = f["prog"]
        return urlencode(pairs)

    def page(request: Request, name: str, **ctx: object) -> Response:
        base = {
            "version": request.app.state.version,
            "queue_size": queue_size(store),
            "meal_labels": MEAL_LABELS,
            "source_labels": SOURCE_LABELS,
            "section": "katalog",
        }
        return templates.TemplateResponse(request, name, {**base, **ctx})

    @router.get("/katalog")
    def catalog(
        request: Request,
        prog: str = "",
        kat: str = "",
        zrodlo: str = "",
        polubione: str = "",
        odrzucone: str = "",
        notice: str = "",
        kcal: str = "",
    ) -> Response:
        f = filters(prog, kat, zrodlo, polubione, odrzucone, kcal)
        rows = store.recipes(
            stat_map(),
            DEFAULT_BASICS,
            min_familiarity=int(f["prog"]) / 100,
            category=str(f["kat"]) or None,
            source=str(f["zrodlo"]) or None,
            liked_only=bool(f["polubione"]),
            show_dismissed=bool(f["odrzucone"]),
        )
        rows = [r for r in rows if _kcal_match(r, str(f["kcal"]))]
        sources = [
            {
                "name": name,
                "label": SOURCE_LABELS[name],
                "count": store.recipe_count(name),
                "last": store.get_meta(f"last_crawl_at:{name}"),
                "error": store.get_meta(f"last_crawl_error:{name}") or "",
                "errors": store.get_meta(f"last_crawl_errors:{name}") or "0",
                "skipped": store.get_meta(f"last_crawl_skipped:{name}") or "0",
                "running": running in ("*", name),
            }
            for name in settings.sources
            if name in SOURCE_LABELS
        ]
        return page(
            request,
            "katalog.html",
            page_title="Katalog",
            root="",
            rows=rows,
            f=f,
            qs=query(f),
            sources=sources,
            notice=notice,
            missing_shown=MISSING_SHOWN,
        )

    def _flag(recipe_id: int, flag: str, qs: str, to_detail: bool) -> Response:
        flags = store.recipe_flags(recipe_id)
        if flags is None:
            raise HTTPException(404)
        current = flags[0] if flag == "liked" else flags[1]
        store.set_flag(recipe_id, "liked" if flag == "liked" else "dismissed", not current)
        if to_detail:
            return RedirectResponse(f"../{recipe_id}", status_code=303)
        given = parse_qs(qs)
        safe = filters(
            *(given.get(k, [""])[0] for k in ("prog", "kat", "zrodlo", "polubione", "odrzucone", "kcal"))
        )
        return RedirectResponse(f"../../katalog?{query(safe)}", status_code=303)

    @router.post("/katalog/{recipe_id}/serce")
    def like(recipe_id: int, qs: str = Form(""), detail: str = Form("")) -> Response:
        return _flag(recipe_id, "liked", qs, detail == "1")

    @router.post("/katalog/{recipe_id}/odrzuc")
    def dismiss(recipe_id: int, qs: str = Form(""), detail: str = Form("")) -> Response:
        return _flag(recipe_id, "dismissed", qs, detail == "1")

    @router.get("/katalog/{recipe_id}")
    def detail(request: Request, recipe_id: int) -> Response:
        by_name = stat_map()
        row = store.recipe(recipe_id, by_name, DEFAULT_BASICS)
        if row is None:
            raise HTTPException(404)
        items = []
        for text, name in store.recipe_ingredient_names(recipe_id):
            if name is None:
                state = "unknown"
            elif name in DEFAULT_BASICS:
                state = "basic"
            elif name not in by_name:
                state = "missing"
            elif by_name[name].at_home:
                state = "home"
            else:
                state = "known"
            items.append({"text": text, "name": name, "state": state})
        return page(
            request,
            "przepis.html",
            page_title=row.title,
            root="../",
            r=row,
            items=items,
        )

    @router.post("/katalog/odswiez")
    async def refresh(zrodlo: str = "") -> Response:
        if zrodlo and zrodlo not in settings.sources:
            return RedirectResponse("../katalog?notice=zrodlo", status_code=303)
        key = zrodlo or "*"
        nonlocal running
        with lock:
            if running is not None:
                return RedirectResponse("../katalog?notice=trwa", status_code=303)
            running = key

        def work() -> None:
            try:
                crawler(zrodlo or None)
            except Exception as exc:  # noqa: BLE001 — logujemy tylko typ (komunikat może zawierać adresy z tokenem)
                log.warning("odświeżenie katalogu zakończone błędem: %s", type(exc).__name__)
            finally:
                nonlocal running
                with lock:
                    running = None

        threading.Thread(target=work, name="catalog-refresh", daemon=True).start()
        return RedirectResponse("../katalog?notice=start", status_code=303)

    return router
