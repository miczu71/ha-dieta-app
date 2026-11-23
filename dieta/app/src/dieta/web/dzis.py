"""Ekran „Dziś” i „Bilans” — szkielet routera (treść w paczce W2)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from ..settings import Settings
from ..store import Store


def make_dzis_router(settings: Settings, store: Store, templates: Jinja2Templates) -> APIRouter:
    del settings, store, templates
    return APIRouter()
