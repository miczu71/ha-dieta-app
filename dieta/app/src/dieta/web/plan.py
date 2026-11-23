"""Ekran „Plan”: tydzień pon–niedz, generacja, zatwierdzenie, wymiana sesji/dnia, cofnięcie.

Każda zmiana to POST + redirect 303 na ``../plan?tydzien=…``; ostrzeżenia planera wracają w parametrach ``w``
(krótko, bez stanu po stronie serwera).
"""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import plan_service as svc
from ..planner import SLOTS
from ..settings import Settings
from ..shopping import plan_cost
from ..store import Store
from .kupujemy import queue_size
from .lista import split_coverage

log = logging.getLogger(__name__)

MAX_WARNINGS = 5
MAX_WARNING_CHARS = 200
MODES = {"los": "random", "polubione": "liked"}
ZAKRESY = ("sesja", "dzien")

NOTICE_TEXT = {
    svc.NOTICE_GENERATED: "Plan wygenerowany. Sprawdź bilans dnia i zatwierdź, gdy pasuje.",
    svc.NOTICE_NEEDS_CONFIRM: (
        "Ten tydzień jest zatwierdzony, więc nic nie zmieniono. Wygeneruj od nowa tylko po potwierdzeniu."
    ),
    svc.NOTICE_NO_PROFILES: "Uzupełnij profile (kcal i wagę), żeby zaplanować tydzień.",
    svc.NOTICE_NO_PLAN: "Najpierw wygeneruj plan na ten tydzień.",
    svc.NOTICE_APPROVED: "Plan zatwierdzony. Zmiany nadal możesz wymieniać, bilans się przelicza.",
    svc.NOTICE_UNDONE: "Cofnięto ostatnią zmianę.",
    svc.NOTICE_NOTHING_TO_UNDO: "Nie ma czego cofnąć.",
    svc.NOTICE_SWAPPED: "Wymieniono.",
    svc.NOTICE_UNCHANGED: "Plan bez zmian.",
    svc.NOTICE_NOT_FOUND: "Nie znaleziono pozycji planu.",
    "bad": "Nieprawidłowe żądanie, nic nie zmieniono.",
}


def make_plan_router(settings: Settings, store: Store, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def go(week: date, notice: str = "", warnings: tuple[str, ...] = ()) -> Response:
        params: list[tuple[str, str]] = [("tydzien", week.isoformat())]
        if notice:
            params.append(("notice", notice))
        params += [("w", w[:MAX_WARNING_CHARS]) for w in warnings[:MAX_WARNINGS]]
        return RedirectResponse(f"../plan?{urlencode(params)}", status_code=303)

    def week_of(raw: str) -> date:
        return svc.monday_of(raw, date.today())

    @router.get("/plan")
    def plan_page(
        request: Request,
        tydzien: str = "",
        notice: str = "",
    ) -> Response:
        week = week_of(tydzien)
        warnings = request.query_params.getlist("w")
        view = svc.view(store, settings, week)
        cost = None
        if view.plan is not None:
            try:
                cost = plan_cost(store, settings, week)
            except Exception:  # koszt to panel poboczny — nie blokuje planu
                log.exception("Koszt planu niedostępny (tydzień %s)", week)
        base = {
            "version": request.app.state.version,
            "queue_size": queue_size(store),
            "section": "plan",
        }
        return templates.TemplateResponse(
            request,
            "plan.html",
            {
                **base,
                "page_title": "Plan",
                "root": "",
                "v": view,
                "notice_text": NOTICE_TEXT.get(notice, ""),
                "warnings": [x[:MAX_WARNING_CHARS] for x in warnings[:MAX_WARNINGS]],
                "modes": MODES,
                "cost": cost,
                "cost_split": split_coverage(cost.coverage_by_source) if cost is not None else "",
                "session_costs": {s.session_id: s for s in cost.sessions} if cost is not None else {},
            },
        )

    @router.post("/plan/generuj")
    def generate(tydzien: str = Form(""), potwierdz: str = Form("")) -> Response:
        week = week_of(tydzien)
        out = svc.generate(store, settings, week, confirm=potwierdz == "1")
        return go(week, out.notice, out.warnings)

    @router.post("/plan/zatwierdz")
    def approve(tydzien: str = Form("")) -> Response:
        week = week_of(tydzien)
        return go(week, svc.approve(store, week).notice)

    @router.post("/plan/wymien")
    def swap(
        tydzien: str = Form(""),
        zakres: str = Form(""),
        tryb: str = Form(""),
        id: str = Form(""),  # noqa: A002 — nazwa pola z kontraktu
    ) -> Response:
        week = week_of(tydzien)
        mode = MODES.get(tryb)
        if mode is None or zakres not in ZAKRESY:
            return go(week, "bad")
        if zakres == "sesja":
            if not id.isdecimal():
                return go(week, "bad")
            out = svc.swap_session(store, settings, week, int(id), mode)
        else:
            day_raw, _, slot = id.partition(":")
            if not day_raw.isdecimal() or int(day_raw) > 6 or slot not in SLOTS:
                return go(week, "bad")
            out = svc.swap_day(store, settings, week, int(day_raw), slot, mode)
        return go(week, out.notice, out.warnings)

    @router.post("/plan/cofnij")
    def undo(tydzien: str = Form("")) -> Response:
        week = week_of(tydzien)
        return go(week, svc.undo(store, week).notice)

    return router
