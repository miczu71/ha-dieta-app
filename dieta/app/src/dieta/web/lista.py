"""Ekran „Lista”: zakupy z planu, „Dodaj jednak”, odhaczanie i wysyłka do todo HA.

Każda zmiana to POST + redirect 303 na ``../lista?tydzien=…&notice=…``.
Składniki i koszt liczy ``shopping``, stany (``force``/``checked``/``sent``) są w ``store``.
Wysyłkę robi ``todo``, tylko za kliknięciem.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import plan_service as svc
from .. import shopping as sh
from ..settings import Settings
from ..store import Store
from ..todo import send_items
from .kupujemy import queue_size

NOTICE_TEXT = {
    "no_plan": "Brak zatwierdzonego planu na ten tydzień.",
    "not_approved": "Plan jest szkicem. Zatwierdź go w zakładce Plan, żeby zobaczyć listę.",
    "past_week": "Ten tydzień już minął, więc lista jest pusta.",
    "no_prices": "Brak cen z Zakupów, lista bez kosztów.",
    "bad": "Nieprawidłowe żądanie, nic nie zmieniono.",
    "todo_ok": "Wysłano do listy todo.",
    "todo_failed": "Nie udało się wysłać do listy todo. Szczegóły w logu add-onu.",
    "todo_skipped": "Wysyłka do todo pominięta (brak połączenia z Home Assistant).",
    "todo_empty": "Nie ma nowych pozycji do wysłania.",
}
SHOWN_NOTICES = ("ok", "no_prices")
SOURCE_LABEL = {
    "receipt": "paragon",
    "receipt_old": "stary paragon",
    "shelf": "Biedronka",
    "promo": "promocja",
    "frisco": "szacunek",
}
TODO_COUNT_NOTICE = re.compile(r"todo_(ok|partial)-(\d+)-(\d+)", re.ASCII)


def _num(value: float) -> str:
    """Liczba z przecinkiem bez zbędnych zer: 900.0 → „900”, 12.5 → „12,5”."""
    return f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def _title(name: str) -> str:
    return name[:1].upper() + name[1:]


def _qty(line: sh.ShoppingLine) -> str:
    if line.grams is None:
        return "ilość nieznana"
    parts = [f"{_num(line.grams)} g"]
    if line.pieces:
        parts.append(f"≈ {_num(line.pieces)} szt.")
    if line.packs and line.pack_g:
        parts.append(f"≈ {line.packs} op. po {_num(line.pack_g)} g")
    return " · ".join(parts)


def _item_text(line: sh.ShoppingLine) -> str:
    return f"{_title(line.name)} — {_num(line.grams)} g" if line.grams is not None else _title(line.name)


def _short_date(value: str | None) -> str | None:
    """Data ISO (także z czasem) jako „25.11”; nieczytelna → None."""
    try:
        return date.fromisoformat(value[:10]).strftime("%d.%m") if value else None
    except ValueError:
        return None


def split_coverage(cov: dict[str, float]) -> str:
    """„paragony 40% · Biedronka 20% · szacunek 15%” — tylko źródła z masą; receipt i receipt_old razem."""
    parts: list[str] = []
    paragony = cov.get("receipt", 0.0) + cov.get("receipt_old", 0.0)
    if paragony > 0:
        parts.append(f"paragony {round(paragony * 100)}%")
    for source in ("shelf", "promo", "frisco"):
        share = cov.get(source, 0.0)
        if share > 0:
            parts.append(f"{SOURCE_LABEL[source]} {round(share * 100)}%")
    return " · ".join(parts)


def _todo_notice(code: str) -> str | None:
    """Tekst komunikatu „X z Y” z kodu ``todo_ok-X-Y`` / ``todo_partial-X-Y``; inne kody → None."""
    match = TODO_COUNT_NOTICE.fullmatch(code)
    if match is None:
        return None
    kind, sent, total = match.group(1), int(match.group(2)), int(match.group(3))
    if sent > total:
        return None
    if kind == "ok":
        return f"Wysłano {sent} z {total} pozycji do listy todo."
    return f"Wysłano {sent} z {total} pozycji, reszta się nie udała — szczegóły w logu."


def _ingredient_id(raw: str) -> int | None:
    return int(raw) if raw.isdecimal() and len(raw) < 19 else None


def make_lista_router(settings: Settings, store: Store, templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    def go(week: date, notice: str = "") -> Response:
        params = [("tydzien", week.isoformat())]
        if notice:
            params.append(("notice", notice))
        return RedirectResponse(f"../lista?{urlencode(params)}", status_code=303)

    def toggle(week: date, raw_ingredient: str, state: str) -> Response:
        ingredient = _ingredient_id(raw_ingredient)
        if ingredient is None:
            return go(week, "bad")
        key = week.isoformat()
        if ingredient in store.shopping_states(key).get(state, set()):
            store.clear_shopping_state(key, ingredient, state)
        else:
            store.set_shopping_state(key, ingredient, state)
        return go(week)

    def row(line: sh.ShoppingLine, states: dict[str, set[int]]) -> dict[str, object]:
        return {
            "title": _title(line.name),
            "qty": _qty(line),
            "cost": line.cost,
            "priced": line.priced,
            "source_label": SOURCE_LABEL.get(line.source or "") if line.priced else None,
            "promo_to": _short_date(line.promo_to),
            "estimate": line.estimate,
            "id": line.ingredient_id,
            "checked": line.ingredient_id in states.get("checked", set()),
            "sent": line.ingredient_id in states.get("sent", set()),
            "forced": line.ingredient_id in states.get("force", set()),
        }

    @router.get("/lista")
    def lista_page(request: Request, tydzien: str = "", notice: str = "") -> Response:
        today = date.today()
        week = svc.monday_of(tydzien, today)
        lst = sh.shopping_list(store, settings, week, today)
        states = store.shopping_states(week.isoformat())
        notice_text = NOTICE_TEXT.get(notice) or _todo_notice(notice) or NOTICE_TEXT.get(lst.notice, "")
        context = {
            "version": request.app.state.version,
            "queue_size": queue_size(store),
            "section": "lista",
            "root": "",
            "page_title": "Lista",
            "week": week,
            "prev_week": week - timedelta(days=7),
            "next_week": week + timedelta(days=7),
            "lst": lst,
            "shown": lst.notice in SHOWN_NOTICES,
            "to_buy": [row(ln, states) for ln in lst.to_buy],
            "at_home": [row(ln, states) for ln in lst.at_home],
            "unpriced": [row(ln, states) for ln in lst.unpriced],
            "show_todo": bool(settings.todo_entity) and lst.notice in SHOWN_NOTICES,
            "notice_text": notice_text,
            "split": split_coverage(lst.coverage_by_source),
        }
        return templates.TemplateResponse(request, "lista.html", context)

    @router.post("/lista/jednak")
    def force(tydzien: str = Form(""), skladnik: str = Form("")) -> Response:
        return toggle(svc.monday_of(tydzien, date.today()), skladnik, "force")

    @router.post("/lista/odhacz")
    def check(tydzien: str = Form(""), skladnik: str = Form("")) -> Response:
        return toggle(svc.monday_of(tydzien, date.today()), skladnik, "checked")

    @router.post("/lista/todo")
    def send_todo(tydzien: str = Form("")) -> Response:
        today = date.today()
        week = svc.monday_of(tydzien, today)
        lst = sh.shopping_list(store, settings, week, today)
        if lst.notice not in SHOWN_NOTICES:
            return go(week, lst.notice)
        key = week.isoformat()
        states = store.shopping_states(key)
        skip = states.get("checked", set()) | states.get("sent", set())
        lines = [ln for ln in (*lst.to_buy, *lst.unpriced) if ln.ingredient_id not in skip]
        if not lines:
            return go(week, "todo_empty")
        outcome = "ok"
        sent = 0
        description = f"Dieta, tydzień od {key}"
        for line in lines:
            result = send_items(settings, [(_item_text(line), description)])
            if result != "ok":
                outcome = result
                break
            # zapis po każdej pozycji: po częściowej porażce wysłane nie pójdą drugi raz
            store.set_shopping_state(key, line.ingredient_id, "sent")
            sent += 1
        if outcome == "ok":
            return go(week, f"todo_ok-{sent}-{len(lines)}")
        if outcome == "failed" and sent > 0:
            return go(week, f"todo_partial-{sent}-{len(lines)}")
        return go(week, f"todo_{outcome}")

    return router
