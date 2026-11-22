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


def _opener_429(plan: list[object], log: list[str]):  # type: ignore[no-untyped-def]
    """plan: kolejne odpowiedzi dla /p — int = HTTPError z kodem, str = treść; robots.txt = 404."""

    def opener(req: urllib.request.Request):  # type: ignore[no-untyped-def]
        if req.full_url.endswith("/robots.txt"):
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO())  # type: ignore[arg-type]
        log.append(req.full_url)
        item = plan.pop(0)
        if isinstance(item, tuple):
            code, headers = item
            raise urllib.error.HTTPError(req.full_url, code, "x", headers, io.BytesIO())  # type: ignore[arg-type]
        resp = io.BytesIO(str(item).encode())
        resp.headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()  # type: ignore[attr-defined]
        return resp

    return opener


def test_fetch_backs_off_on_429_and_honours_retry_after() -> None:
    sleeps: list[float] = []
    log: list[str] = []
    plan: list[object] = [(429, {"Retry-After": "7"}), "ok"]
    fetch = make_fetch(opener=_opener_429(plan, log), sleep=sleeps.append, clock=lambda: 0.0)
    assert fetch("https://a.pl/p") == "ok"
    assert len(log) == 2
    assert 7 in sleeps  # Retry-After respektowany


def test_fetch_gives_up_after_retries_and_slows_down() -> None:
    sleeps: list[float] = []
    log: list[str] = []
    now = [0.0]
    plan: list[object] = [(429, {})] * 3 + ["ok"]
    fetch = make_fetch(opener=_opener_429(plan, log), sleep=sleeps.append, clock=lambda: now[0])
    with pytest.raises(FetchError) as err:
        fetch("https://a.pl/p")
    assert err.value.status == 429 and not err.value.permanent
    assert len(log) == 3  # próba + 2 ponowienia
    assert max(sleeps) >= 10  # domyślny backoff bez Retry-After
    sleeps.clear()
    assert fetch("https://a.pl/p") == "ok"
    assert max(sleeps, default=0) >= 2  # tempo zwolnione po 429 (odstęp ×2)
