import io
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from dieta.catalog import CrawlResult
from dieta.fetcher import USER_AGENT, FetchError, make_fetch
from dieta.pipeline import run_catalog
from dieta.settings import Settings
from dieta.store import Store


class _Resp:
    def __init__(self, body: str):
        self._body = body.encode()
        self.headers = self

    def get_content_charset(self) -> str:
        return "utf-8"

    def read(self, n: int = -1) -> bytes:
        return self._body

    def close(self) -> None:
        pass


def _opener(pages: dict[str, str], log: list[tuple[str, str]]):  # type: ignore[no-untyped-def]
    def opener(req: urllib.request.Request) -> _Resp:
        log.append((req.full_url, req.get_header("User-agent")))
        if req.full_url not in pages:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO())  # type: ignore[arg-type]
        return _Resp(pages[req.full_url])

    return opener


def test_fetch_respects_robots_throttle_and_user_agent() -> None:
    log: list[tuple[str, str]] = []
    pages = {
        "https://a.pl/robots.txt": "User-agent: *\nDisallow: /tajne/",
        "https://a.pl/ok": "<html>ok</html>",
        "https://a.pl/tajne/x": "nie",
    }
    now = [100.0]
    sleeps: list[float] = []
    fetch = make_fetch(opener=_opener(pages, log), sleep=sleeps.append, clock=lambda: now[0])
    assert fetch("https://a.pl/ok") == "<html>ok</html>"
    with pytest.raises(FetchError, match="robots"):
        fetch("https://a.pl/tajne/x")
    assert [u for u, _ in log] == ["https://a.pl/robots.txt", "https://a.pl/ok"]
    assert {ua for _, ua in log} == {USER_AGENT}
    assert sleeps and all(abs(s - 1.0) < 1e-9 for s in sleeps)  # zegar stoi → pełna sekunda przerwy


def test_fetch_missing_robots_allows_and_errors_are_clean() -> None:
    fetch = make_fetch(opener=_opener({"https://b.pl/p": "x"}, []), sleep=lambda s: None)
    assert fetch("https://b.pl/p") == "x"
    with pytest.raises(FetchError):
        fetch("https://b.pl/brak")
    with pytest.raises(FetchError):
        fetch("file:///etc/passwd")


def test_run_catalog_runs_crawl_then_mapping_then_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dieta.pipeline as pl

    order: list[str] = []

    def fake_crawl(store, source, **kw):  # type: ignore[no-untyped-def]
        order.append(f"crawl:{source.name}:{kw['limit']}")
        return CrawlResult(0, 0, 0, 0, False)

    monkeypatch.setattr(pl, "crawl", fake_crawl)
    monkeypatch.setattr(pl, "map_recipe_ingredients", lambda s, a: order.append("map") or 0)
    monkeypatch.setattr(pl, "enrich_meta", lambda s, a: order.append("meta") or 0)
    settings = Settings(
        data_dir=tmp_path, log_level="info", dev=True, sources=("respo", "policzona"), crawl_daily_limit=7
    )
    store = Store(settings.db_path)
    kw = dict(fetch=lambda u: "", ask_ingredients=None, ask_meta=None, today=date(2026, 11, 22))
    run_catalog(store, settings, **kw)  # type: ignore[arg-type]
    assert order == ["crawl:respo:7", "crawl:policzona:7", "map", "meta"]
    order.clear()
    run_catalog(store, settings, source="policzona", **kw)  # type: ignore[arg-type]
    assert order == ["crawl:policzona:7", "map", "meta"]
