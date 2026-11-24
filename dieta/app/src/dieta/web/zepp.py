"""Trasy „Konto Zepp” (łączenie, kod e-mail, wybór członka, synchronizacja, rozłączenie).

Każda trasa to POST + redirect 303 na stronę główną z ``?zepp=<kod>[&n=<liczba>]&p=<osoba>``. Hasło,
e-mail, kod ani tokeny nie trafiają do adresów ani do odpowiedzi. Sieć idzie w wątku (``to_thread``).
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from ..settings import Settings
from ..store import PERSON_IDS, Store
from ..zepp_service import Outcome, ZeppService

# kod → (rodzaj, tekst); rodzaj „status” = role="status", „alert” = role="alert"
BANNERS: dict[str, tuple[str, str]] = {
    "otp": ("status", "Xiaomi wymaga kodu. Kliknij „Wyślij kod”, potem wpisz kod z e-maila."),
    "sent": ("status", "Kod weryfikacyjny wysłany e-mailem."),
    "ok": ("status", "Połączono z Zepp."),
    "wrong_code": ("alert", "Zły kod weryfikacyjny."),
    "login_failed": ("alert", "Logowanie nie powiodło się. Sprawdź e-mail i hasło."),
    "expired": ("alert", "Sesja wygasła — połącz ponownie."),
    "error": ("alert", "Nie udało się połączyć z Zepp. Spróbuj później."),
    "disconnected": ("status", "Konto Zepp rozłączone (historia pomiarów zostaje)."),
}


def banner(code: str, n: str, pid: str) -> dict[str, object] | None:
    """Baner z parametrów zapytania; nieznany kod → ``None`` (nic z zapytania nie trafia do HTML)."""
    entry = BANNERS.get(code)
    if entry is None:
        return None
    kind, text = entry
    if code == "ok" and n.isdigit():
        text = f"Zaimportowano nowych pomiarów: {int(n)}."
    return {"kind": kind, "text": text, "pid": int(pid) if pid.isdigit() else 0}


def _redirect(person_id: int, out: Outcome) -> RedirectResponse:
    # Adres względny od katalogu POST-a (/zepp/<id>/): dwa segmenty w górę = strona główna (też pod Ingress).
    query = {"zepp": out.code}
    if out.n is not None:
        query["n"] = str(out.n)
    query["p"] = str(person_id)
    return RedirectResponse("../../?" + urlencode(query), status_code=303)


def make_zepp_router(
    settings: Settings, store: Store, templates: Jinja2Templates, service: ZeppService
) -> APIRouter:
    del settings, store, templates  # podpis wspólny z pozostałymi routerami
    router = APIRouter()

    def _person(person_id: int) -> int:
        if person_id not in PERSON_IDS:
            raise HTTPException(404)
        return person_id

    @router.post("/zepp/{person_id}/start")
    async def start(person_id: int, email: str = Form(""), password: str = Form("")) -> Response:
        _person(person_id)
        return _redirect(person_id, await asyncio.to_thread(service.start, person_id, email, password))

    @router.post("/zepp/{person_id}/send")
    async def send(person_id: int) -> Response:
        _person(person_id)
        return _redirect(person_id, await asyncio.to_thread(service.send, person_id))

    @router.post("/zepp/{person_id}/verify")
    async def verify(person_id: int, code: str = Form("")) -> Response:
        _person(person_id)
        return _redirect(person_id, await asyncio.to_thread(service.verify, person_id, code))

    @router.post("/zepp/{person_id}/member")
    async def member(person_id: int, fuid: str = Form("")) -> Response:
        _person(person_id)
        try:
            chosen = int(fuid)
        except ValueError:
            return _redirect(person_id, Outcome("error"))
        return _redirect(person_id, await asyncio.to_thread(service.member, person_id, chosen))

    @router.post("/zepp/{person_id}/sync")
    async def sync(person_id: int) -> Response:
        _person(person_id)
        return _redirect(person_id, await asyncio.to_thread(service.sync, person_id))

    @router.post("/zepp/{person_id}/disconnect")
    async def disconnect(person_id: int) -> Response:
        _person(person_id)
        return _redirect(person_id, await asyncio.to_thread(service.disconnect, person_id))

    return router
