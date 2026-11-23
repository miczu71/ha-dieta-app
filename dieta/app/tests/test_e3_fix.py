"""Poprawki po code-review E3: mapowanie przy awarii LLM, crawl, fetcher, pętla nocna, blokady."""

import asyncio
import io
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dieta.pipeline as pl
import dieta.web.app as webapp
from dieta.catalog import CrawlResult, crawl
from dieta.dictionary import map_with_llm
from dieta.fetcher import MAX_BYTES, FetchError, SameHostRedirect, make_fetch
from dieta.settings import Settings
from dieta.sources.base import RawIngredient, Recipe
from dieta.sources.jsonld import parse_jsonld_recipe
from dieta.store import RecipeMeta, Store
from dieta.sync import map_recipe_ingredients

TODAY = date(2026, 11, 22)
META = RecipeMeta("lunch", 4, True, "rule")


def _recipe(sid: str = "u1", names: tuple[str, ...] = ("ryż",), title: str = "Przepis") -> Recipe:
    return Recipe(
        source="respo",
        source_id=sid,
        title=title,
        url=sid,
        image=None,
        servings=2,
        kcal=300.0,
        protein=1,
        carbs=1,
        fat=1,
        minutes=5,
        tags=(),
        category_hint=None,
        ingredients=tuple(RawIngredient(n, None, None, n) for n in names),
    )


def _settings(tmp_path: Path, **kw) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(data_dir=tmp_path, log_level="info", dev=True, **kw)


# ---- A: mapowanie składników przepisów ----


def test_map_with_llm_flags_unanswered() -> None:
    res = map_with_llm(["a"], ask=lambda names: None)
    assert [r.answered for r in res] == [False]
    ok = map_with_llm(["a"], ask=lambda names: [{"i": 0, "klasa": "skladnik", "skladnik": "x"}])
    assert [r.answered for r in ok] == [True]


def test_llm_failure_writes_nothing_and_retries(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_recipe(_recipe(names=("tahini",)), META)
    assert map_recipe_ingredients(store, lambda names: None) == 0
    assert "ing:tahini" not in store.mapped_codes()
    calls: list[list[str]] = []

    def ok(names: list[str]) -> list[dict[str, object]]:
        calls.append(names)
        return [{"i": 0, "klasa": "skladnik", "skladnik": "tahini", "trwalosc": "dry"}]

    assert map_recipe_ingredients(store, ok) == 1
    assert calls == [["tahini"]]


def test_non_ingredient_class_is_excluded_not_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_recipe(_recipe(names=("woda niegazowana",)), META)
    ask = lambda names: [{"i": 0, "klasa": "napoj", "skladnik": "", "trwalosc": ""}]  # noqa: E731
    assert map_recipe_ingredients(store, ask) == 0
    rows = {r.code: r for r in store.mappings()}
    assert rows["ing:woda niegazowana"].status == "excluded"


# ---- B: crawl ----


class Src:
    name = "respo"

    def __init__(self, urls: list[str], parsed: dict[str, Recipe | None] | None = None):
        self.urls = urls
        self.parsed = parsed or {}

    def prefetched(self, url: str) -> str | None:
        return None

    def list_urls(self, *, fetch):  # type: ignore[no-untyped-def]
        return list(self.urls)

    def parse(self, url: str, html: str) -> Recipe | None:
        return self.parsed.get(url, _recipe(url))


def test_crawl_permanent_errors_are_skipped_transient_are_not(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")

    def fetch(url: str) -> str:
        if url == "gone":
            raise FetchError("HTTPError 404", status=404)
        if url == "robots":
            raise FetchError("zablokowane przez robots.txt", blocked=True)
        if url == "busy":
            raise FetchError("HTTPError 429", status=429)
        if url == "srv":
            raise FetchError("HTTPError 503", status=503)
        raise FetchError("URLError")

    src = Src(["gone", "robots", "busy", "srv", "net"])
    r = crawl(store, src, fetch=fetch, today=TODAY, limit=10)
    assert r.errors == 5
    skips = store.meta_keys("crawl_skip:respo:")
    assert skips == {"crawl_skip:respo:gone", "crawl_skip:respo:robots"}
    assert "404" in (store.get_meta("crawl_skip:respo:gone") or "")
    r2 = crawl(store, src, fetch=fetch, today=TODAY, limit=10)
    assert r2.already_known == 2 and r2.errors == 3


def test_crawl_skip_for_non_recipe_expires_after_30_days(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    src = Src(["blog"], parsed={"blog": None})
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        return ""

    crawl(store, src, fetch=fetch, today=TODAY, limit=10)
    crawl(store, src, fetch=fetch, today=date(2026, 12, 21), limit=10)  # 29 dni
    assert calls == ["blog"]
    crawl(store, src, fetch=fetch, today=date(2026, 12, 22), limit=10)  # 30 dni
    assert calls == ["blog", "blog"]


def test_crawl_empty_url_list_is_error_without_last_crawl_at(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    r = crawl(store, Src([]), fetch=lambda u: "", today=TODAY, limit=10)
    assert r.errors == 1
    assert store.get_meta("last_crawl_error:respo") == "sitemapa nie zawiera przepisów"
    assert not store.get_meta("last_crawl_at:respo")


def test_crawl_stores_error_and_skipped_counts(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    src = Src(["a", "blog", "bad"], parsed={"blog": None})

    def fetch(url: str) -> str:
        if url == "bad":
            raise FetchError("URLError")
        return ""

    crawl(store, src, fetch=fetch, today=TODAY, limit=10)
    assert store.get_meta("last_crawl_errors:respo") == "1"
    assert store.get_meta("last_crawl_skipped:respo") == "1"


def test_jsonld_skips_recipe_node_without_name() -> None:
    page = (
        '<script type="application/ld+json">[{"@type":"Recipe"},'
        '{"@type":"Recipe","name":"Zupa","recipeIngredient":["1 marchew"]}]</script>'
    )
    r = parse_jsonld_recipe("respo", "u", page)
    assert r is not None and r.title == "Zupa"


def test_katalog_shows_error_and_skipped_counts(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    store = Store(s.db_path)
    store.set_meta("last_crawl_at:respo", "2026-11-22")
    store.set_meta("last_crawl_errors:respo", "3")
    store.set_meta("last_crawl_skipped:respo", "7")
    client = TestClient(webapp.create_app(s, sync_runner=lambda: 0, crawl_runner=lambda src: {}))
    html = client.get("/katalog").text
    assert "błędów: 3" in html and "pominiętych: 7" in html


# ---- C: fetcher ----


class Resp:
    def __init__(self, body: bytes, charset: str | None = "utf-8"):
        self.body = body
        self.charset = charset
        self.headers = self
        self.reads: list[int] = []

    def get_content_charset(self) -> str | None:
        return self.charset

    def read(self, n: int = -1) -> bytes:
        self.reads.append(n)
        return self.body if n < 0 else self.body[:n]

    def close(self) -> None:
        pass


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO())  # type: ignore[arg-type]


def test_fetch_error_carries_status() -> None:
    def opener(req: urllib.request.Request) -> Resp:
        if req.full_url.endswith("robots.txt"):
            raise _http_error(req.full_url, 404)
        raise _http_error(req.full_url, 410)

    fetch = make_fetch(opener=opener, sleep=lambda s: None)
    with pytest.raises(FetchError) as ei:
        fetch("https://a.pl/x")
    assert ei.value.status == 410 and "HTTPError 410" in str(ei.value)


def test_fetch_unknown_charset_falls_back_to_utf8() -> None:
    fetch = make_fetch(
        opener=lambda req: Resp("zażółć".encode(), charset="nie-ma-takiego"), sleep=lambda s: None
    )
    assert fetch("https://a.pl/robots.txt") == "zażółć"


def test_fetch_size_limit_boundary() -> None:
    resps: list[Resp] = []

    def opener_for(size: int):  # type: ignore[no-untyped-def]
        def opener(req: urllib.request.Request) -> Resp:
            r = Resp(b"x" * size)
            resps.append(r)
            return r

        return opener

    assert len(make_fetch(opener=opener_for(MAX_BYTES), sleep=lambda s: None)("https://a.pl/robots.txt")) == (
        MAX_BYTES
    )
    with pytest.raises(FetchError):
        make_fetch(opener=opener_for(MAX_BYTES + 1), sleep=lambda s: None)("https://a.pl/robots.txt")
    assert resps[-1].reads == [MAX_BYTES + 1]


def test_redirect_cross_host_rejected_same_host_allowed() -> None:
    handler = SameHostRedirect(lambda url: True)
    req = urllib.request.Request("https://a.pl/x")
    with pytest.raises(FetchError):
        handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://evil.pl/y")  # type: ignore[arg-type]
    with pytest.raises(FetchError):
        handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "file:///etc/passwd")  # type: ignore[arg-type]
    new = handler.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://a.pl/z")  # type: ignore[arg-type]
    assert new is not None and new.full_url == "https://a.pl/z"
    blocked = SameHostRedirect(lambda url: False)
    with pytest.raises(FetchError, match="robots"):
        blocked.redirect_request(req, io.BytesIO(), 302, "Found", {}, "https://a.pl/z")  # type: ignore[arg-type]


def test_robots_5xx_blocks_host_4xx_allows() -> None:
    def opener(req: urllib.request.Request) -> Resp:
        if req.full_url == "https://down.pl/robots.txt":
            raise _http_error(req.full_url, 503)
        if req.full_url == "https://none.pl/robots.txt":
            raise _http_error(req.full_url, 404)
        return Resp(b"ok")

    fetch = make_fetch(opener=opener, sleep=lambda s: None)
    with pytest.raises(FetchError, match="robots niedostępny"):
        fetch("https://down.pl/p")
    with pytest.raises(FetchError, match="robots niedostępny"):
        fetch("https://down.pl/q")
    assert fetch("https://none.pl/p") == "ok"


def test_robots_exact_url_exemption_only() -> None:
    log: list[str] = []

    def opener(req: urllib.request.Request) -> Resp:
        log.append(req.full_url)
        if req.full_url == "https://a.pl/robots.txt":
            return Resp(b"User-agent: *\nDisallow: /x/")
        return Resp(b"ok")

    fetch = make_fetch(opener=opener, sleep=lambda s: None)
    with pytest.raises(FetchError, match="robots"):
        fetch("https://a.pl/x/robots.txt")


# ---- D: _nightly, lifespan, locki ----


class _Stop(Exception):
    pass


def _run_nightly(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, sync, crawl_fn, iterations: int
) -> None:  # type: ignore[no-untyped-def]
    count = [0]

    async def fake_sleep(_: float) -> None:
        count[0] += 1
        if count[0] > iterations:
            raise _Stop

    monkeypatch.setattr(webapp.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(webapp, "seconds_until", lambda now, clock: 0)
    with pytest.raises(_Stop):
        asyncio.run(webapp._nightly(settings, sync, crawl_fn))


def test_nightly_without_token_skips_sync_but_crawls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    _run_nightly(
        monkeypatch, _settings(tmp_path), lambda: calls.append("sync") or 0,
        lambda src: calls.append("crawl") or {}, 1,
    )  # fmt: skip
    assert calls == ["crawl"]


def test_nightly_order_and_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(tmp_path, zakupy_url="http://z", zakupy_token="t")
    calls: list[str] = []

    def sync() -> int:
        calls.append("sync")
        raise RuntimeError("boom")

    def crawl_fn(src: str | None) -> dict[str, CrawlResult]:
        calls.append("crawl")
        if calls.count("crawl") == 1:
            raise RuntimeError("boom")
        return {}

    _run_nightly(monkeypatch, s, sync, crawl_fn, 2)
    assert calls == ["sync", "crawl", "sync", "crawl"]  # wyjątki nie blokują kroków ani pętli


def test_lifespan_starts_nightly_only_without_injected_runners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[int] = []

    async def fake_nightly(*a, **k):  # type: ignore[no-untyped-def]
        started.append(1)

    monkeypatch.setattr(webapp, "_nightly", fake_nightly)
    with TestClient(webapp.create_app(_settings(tmp_path))):
        pass
    assert started == [1]
    with TestClient(webapp.create_app(_settings(tmp_path), sync_runner=lambda: 0, crawl_runner=lambda s: {})):
        pass
    assert started == [1]


def test_default_crawl_runner_serializes_and_releases_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = _settings(tmp_path)
    store = Store(s.db_path)
    active = [0]
    peak = [0]
    guard = threading.Lock()
    fail = [True]

    def fake_run_catalog(*a, **k):  # type: ignore[no-untyped-def]
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        try:
            time.sleep(0.05)
            if fail[0]:
                raise RuntimeError("boom")
            return {}
        finally:
            with guard:
                active[0] -= 1

    monkeypatch.setattr(pl, "run_catalog", fake_run_catalog)
    monkeypatch.setattr(pl, "make_fetch", lambda: lambda u: "")
    runner = pl.default_crawl_runner(s, store)
    errors: list[Exception] = []

    def call() -> None:
        try:
            runner(None)
        except RuntimeError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1 and len(errors) == 2  # wyjątek pierwszego nie zablokował drugiego
    fail[0] = False
    assert runner(None) == {}


def test_catalog_running_flag_cleared_after_crawler_exception(tmp_path: Path) -> None:
    ran = threading.Event()

    def boom(src: str | None) -> dict[str, CrawlResult]:
        ran.set()
        raise RuntimeError("boom")

    s = _settings(tmp_path)
    client = TestClient(
        webapp.create_app(s, sync_runner=lambda: 0, crawl_runner=boom), follow_redirects=False
    )
    first = client.post("/katalog/odswiez")
    assert first.headers["location"].endswith("notice=start")
    assert ran.wait(2)
    deadline = time.time() + 2
    location = ""
    while time.time() < deadline:
        location = client.post("/katalog/odswiez").headers["location"]
        if location.endswith("notice=start"):
            break
        time.sleep(0.02)
    assert location.endswith("notice=start")


def test_meta_keys_escapes_like_wildcards(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    for key in ("crawlXskip:respo:a", "crawl_skip:respo:b", "100%:x"):
        store.set_meta(key, "1")
    assert store.meta_keys("crawl_skip:respo:") == {"crawl_skip:respo:b"}
    assert store.meta_keys("100%:") == {"100%:x"}


def test_reupsert_keeps_flags_and_replaces_ingredients(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = store.upsert_recipe(_recipe(names=("ryż", "sól")), META)
    store.set_flag(rid, "liked", True)
    again = store.upsert_recipe(_recipe(names=("mąka",)), META)
    assert again == rid
    assert store.recipe_flags(rid) == (True, False)
    assert [t for t, _ in store.recipe_ingredient_names(rid)] == ["mąka"]
