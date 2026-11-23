"""Ekrany „Dziś” (odhaczanie posiłków) i „Bilans” (dzień i tydzień); reguły są w ``realization``.

Każda zmiana to POST + redirect 303 na ``../dzis?dzien=…&notice=…``. Tutaj walidujemy parametry formularza
(data, stan, liczby z przecinkiem), a okno edycji, status planu i migawki liczy serwis.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import plan_service as svc
from .. import realization as rz
from ..settings import Settings
from ..store import PERSON_IDS, Store
from .kupujemy import queue_size

MIN_YEAR, MAX_YEAR = 2000, 2100  # poza zakresem timedelta(±1 dzień) mogłoby przepełnić datę
STATUS_BY_STATE = {"zjedzone": "eaten", "pominiete": "skipped", "zamiennik": "substitute"}

NOTICE_TEXT = {
    rz.NOTICE_OK: "Zapisano.",
    rz.NOTICE_NO_PLAN: "Brak zatwierdzonego planu na ten tydzień.",
    rz.NOTICE_NOT_APPROVED: "Zatwierdź plan tygodnia, zanim odhaczysz posiłki.",
    rz.NOTICE_OUT_OF_WINDOW: "Poza oknem edycji (dziś i 7 dni wstecz).",
    rz.NOTICE_NO_MEAL: "Ta osoba nie ma w tym dniu posiłku do odhaczenia.",
    rz.NOTICE_BAD: "Nieprawidłowe żądanie, nic nie zmieniono.",
}


def _strict_day(raw: str) -> date | None:
    try:
        day = date.fromisoformat(raw) if raw else None
    except ValueError:
        return None
    return day if day is not None and MIN_YEAR <= day.year <= MAX_YEAR else None


def _number(raw: str) -> tuple[bool, float | None]:
    """Pusty tekst → (True, None); liczba (przecinek lub kropka) → (True, wartość); reszta → (False, None)."""
    text = raw.strip().replace(",", ".")
    if not text:
        return True, None
    try:
        value = float(text)
    except ValueError:
        return False, None
    if not math.isfinite(value):
        return False, None
    return True, value


def _base(request: Request, store: Store, section: str) -> dict[str, object]:
    return {
        "version": request.app.state.version,
        "queue_size": queue_size(store),
        "section": section,
        "root": "",
    }


def make_dzis_router(settings: Settings, store: Store, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()
    labels = [settings.person_name(pid) for pid in PERSON_IDS]

    def go(day: date, notice: str) -> Response:
        params = urlencode({"dzien": day.isoformat(), "notice": notice})
        return RedirectResponse(f"../dzis?{params}", status_code=303)

    @router.get("/dzis")
    def dzis_page(request: Request, dzien: str = "", notice: str = "") -> Response:
        today = date.today()
        day = _strict_day(dzien) or today
        view = rz.day_log(store, settings, day, today)
        return templates.TemplateResponse(
            request,
            "dzis.html",
            {
                **_base(request, store, "dzis"),
                "page_title": "Dziś",
                "v": view,
                "is_today": day == today,
                "week": svc.monday(day),
                "prev_day": day - timedelta(days=1),
                "next_day": day + timedelta(days=1),
                "day_name": svc.DAY_NAMES[day.weekday()],
                "labels": labels,
                "pids": PERSON_IDS,
                "notice_text": NOTICE_TEXT.get(notice, ""),
            },
        )

    @router.post("/dzis/odhacz")
    def log_meal(
        dzien: str = Form(""),
        osoba: str = Form(""),
        slot: str = Form(""),
        stan: str = Form(""),
        nazwa: str = Form(""),
        kcal: str = Form(""),
        bialko: str = Form(""),
        tluszcz: str = Form(""),
        wegle: str = Form(""),
    ) -> Response:
        today = date.today()
        day = _strict_day(dzien)
        if day is None:
            return go(today, rz.NOTICE_BAD)
        person = int(osoba) if osoba.isdecimal() and len(osoba) < 4 else 0
        if stan == "reset":
            return go(day, rz.clear(store, day, person, slot, today).notice)
        status = STATUS_BY_STATE.get(stan)
        ok_kcal, kcal_value = _number(kcal)
        ok_protein, protein = _number(bialko)
        ok_fat, fat = _number(tluszcz)
        ok_carbs, carbs = _number(wegle)
        if status is None or not (ok_kcal and ok_protein and ok_fat and ok_carbs):
            return go(day, rz.NOTICE_BAD)
        out = rz.mark(
            store,
            settings,
            day,
            person,
            slot,
            status,
            today,
            name=nazwa,
            kcal=kcal_value,
            protein=protein,
            fat=fat,
            carbs=carbs,
        )
        return go(day, out.notice)

    @router.get("/bilans")
    def bilans_page(request: Request, tydzien: str = "") -> Response:
        today = date.today()
        week = svc.monday_of(tydzien if _strict_day(tydzien) else "", today)
        view = rz.week_balance(store, settings, week, today)
        return templates.TemplateResponse(
            request,
            "bilans.html",
            {
                **_base(request, store, "bilans"),
                "page_title": "Bilans",
                "v": view,
            },
        )

    return router
