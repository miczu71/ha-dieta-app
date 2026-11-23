"""Punkt wejścia add-onu: panel Ingress na porcie 8099; ``estimate --validate <db>`` = pomiar metody."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from .settings import load_settings
from .web.app import create_app


def serve() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    uvicorn.run(
        create_app(settings), host="0.0.0.0", port=8099, log_level=settings.log_level, access_log=False
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        serve()
        return 0
    parser = argparse.ArgumentParser(prog="python -m dieta")
    sub = parser.add_subparsers(dest="command", required=True)
    est = sub.add_parser("estimate", help="pomiar szacunku kcal na przepisach z kcal ze źródła")
    est.add_argument("--validate", metavar="DB", type=Path, required=True, help="baza SQLite (tylko odczyt)")
    parsed = parser.parse_args(args)
    from .validate import validate

    return validate(parsed.validate, sys.stdout)


if __name__ == "__main__":
    sys.exit(main())
