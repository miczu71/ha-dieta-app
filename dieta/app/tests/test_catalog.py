from datetime import date
from pathlib import Path

from dieta.catalog import RATE_LIMIT_STOP, classify_meal, crawl, enrich_meta
from dieta.fetcher import FetchError
from dieta.sources.base import RawIngredient, Recipe
from dieta.store import RecipeMeta, Store

TODAY = date(2026, 11, 22)


def _recipe(
    url: str,
    title: str = "Przepis",
    kcal: float | None = 400.0,
    tags: tuple[str, ...] = (),
    hint: str | None = None,
) -> Recipe:
    return Recipe(
        source="respo",
        source_id=url,
        title=title,
        url=url,
        image=None,
        servings=2,
        kcal=kcal,
        protein=20,
        carbs=40,
        fat=10,
        minutes=30,
        tags=tags,
        category_hint=hint,
        ingredients=(RawIngredient("200 g ryżu", 200, "g", "ryż"),),
    )


class FakeSource:
    name = "respo"

    def __init__(self, urls: list[str], bad: set[str] | None = None, notrecipe: set[str] | None = None):
        self.urls = urls
        self.bad = bad or set()
        self.notrecipe = notrecipe or set()
        self.parsed: list[str] = []

    def list_urls(self, *, fetch):  # type: ignore[no-untyped-def]
        return list(self.urls)

    def parse(self, url: str, html: str) -> Recipe | None:
        self.parsed.append(url)
        if url in self.notrecipe:
            return None
        return _recipe(url, title="Zupa krem z dyni" if url.endswith("1") else "Coś")


def _fetch_factory(fail: set[str] | None = None):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if fail and url in fail:
            raise OSError("boom")
        return "<html/>"

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_classify_meal_rules() -> None:
    assert classify_meal(_recipe("u", "Zupa pomidorowa")) == ("lunch", 4, True)
    assert classify_meal(_recipe("u", "Gulasz wołowy")) == ("lunch", 4, True)
    assert classify_meal(_recipe("u", "Sałatka grecka")) == ("lunch", 1, False)
    assert classify_meal(_recipe("u", "Cokolwiek", hint="Śniadanie"))[0] == "breakfast"
    assert classify_meal(_recipe("u", "Cokolwiek")) == (None, None, None)


def test_crawl_adds_and_is_idempotent(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    source = FakeSource(["u1", "u2"])
    fetch = _fetch_factory()
    first = crawl(store, source, fetch=fetch, today=TODAY, limit=10)
    assert first.added == 2
    again = crawl(store, source, fetch=fetch, today=TODAY, limit=10)
    assert again.added == 0 and again.already_known == 2
    assert fetch.calls == ["u1", "u2"]  # type: ignore[attr-defined]


def test_crawl_respects_daily_limit_across_runs(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    source = FakeSource(["u1", "u2", "u3"])
    fetch = _fetch_factory()
    r1 = crawl(store, source, fetch=fetch, today=TODAY, limit=2)
    assert (r1.added, r1.limit_reached) == (2, True)
    r2 = crawl(store, source, fetch=fetch, today=TODAY, limit=2)
    assert r2.added == 0 and r2.limit_reached
    r3 = crawl(store, source, fetch=fetch, today=date(2026, 11, 23), limit=2)
    assert r3.added == 1


def test_crawl_error_on_one_url_does_not_stop(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    source = FakeSource(["u1", "u2", "u3"])
    fetch = _fetch_factory(fail={"u2"})
    result = crawl(store, source, fetch=fetch, today=TODAY, limit=10)
    assert (result.added, result.errors) == (2, 1)
    assert "u2" in (store.get_meta("last_crawl_error:respo") or "")
    assert store.recipe_source_ids("respo") == {"u1", "u3"}


def test_crawl_remembers_non_recipe_pages(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    source = FakeSource(["u1", "blog"], notrecipe={"blog"})
    fetch = _fetch_factory()
    r = crawl(store, source, fetch=fetch, today=TODAY, limit=10)
    assert (r.added, r.skipped) == (1, 1)
    crawl(store, source, fetch=fetch, today=date(2026, 11, 23), limit=10)
    assert fetch.calls == ["u1", "blog"]  # type: ignore[attr-defined]


def test_crawl_list_urls_error_is_recorded(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")

    class Broken(FakeSource):
        def list_urls(self, *, fetch):  # type: ignore[no-untyped-def]
            raise OSError("sitemap down")

    r = crawl(store, Broken([]), fetch=_fetch_factory(), today=TODAY, limit=5)
    assert r.errors == 1
    assert "sitemap down" in (store.get_meta("last_crawl_error:respo") or "")


def test_crawl_never_overwrites_approved_meta(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = store.upsert_recipe(_recipe("u2"), RecipeMeta(None, None, None, "pending"))
    store.approve_recipe_meta(rid, "dinner", 5, True)
    store.upsert_recipe(_recipe("u2", "Zupa"), RecipeMeta("lunch", 4, False, "rule"))
    row = store.recipe(rid, {}, frozenset())
    assert row is not None
    assert (row.meal_category, row.storage_days, row.freezable, row.meta_status) == (
        "dinner",
        5,
        True,
        "approved",
    )
    assert row.title == "Zupa"


def test_enrich_meta_ai_fills_and_junk_goes_to_pending(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    ok = store.upsert_recipe(_recipe("a", "Dobry"), RecipeMeta(None, None, None, "pending"))
    junk = store.upsert_recipe(_recipe("b", "Śmieć"), RecipeMeta(None, None, None, "pending"))
    asked: list[list[str]] = []

    def ask(titles: list[str]) -> list[dict[str, object]] | None:
        asked.append(titles)
        return [
            {"i": 0, "kategoria": "dinner", "dni": 3, "mrozenie": True},
            {"i": 1, "kategoria": "kosmos", "dni": 999, "mrozenie": "tak"},
        ]

    assert enrich_meta(store, ask) == 1
    rows = {r.id: r for r in store.recipes({}, frozenset())}
    assert rows[ok].meta_status == "ai" and rows[ok].meal_category == "dinner"
    assert rows[junk].meta_status == "pending"
    assert [p.id for p in store.pending_recipe_meta()] == [junk]
    enrich_meta(store, ask)
    assert len(asked) == 1  # śmieci nie są pytane ponownie


def test_enrich_meta_llm_failure_keeps_items_for_retry(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    rid = store.upsert_recipe(_recipe("a", "Dobry"), RecipeMeta(None, None, None, "pending"))
    calls: list[int] = []

    def down(titles: list[str]) -> list[dict[str, object]] | None:
        calls.append(1)
        return None

    assert enrich_meta(store, down) == 0
    assert store.get_meta(f"recipe_meta_asked:{rid}") is None
    ok = lambda titles: [{"i": 0, "kategoria": "dinner", "dni": 3, "mrozenie": True}]  # noqa: E731
    assert enrich_meta(store, ok) == 1


def test_enrich_meta_without_ask_is_noop(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    store.upsert_recipe(_recipe("a"), RecipeMeta(None, None, None, "pending"))
    assert enrich_meta(store, None) == 0
    assert len(store.pending_recipe_meta()) == 1


def _fetch_429(ok: set[str]):  # type: ignore[no-untyped-def]
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url in ok:
            return "<html/>"
        raise FetchError("HTTPError 429", status=429)

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def test_crawl_stops_after_consecutive_429_and_refunds_limit(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    urls = [f"u{i}" for i in range(20)]
    source = FakeSource(urls)
    fetch = _fetch_429(ok={"u0"})
    result = crawl(store, source, fetch=fetch, today=TODAY, limit=50)
    assert result.added == 1
    assert len(fetch.calls) == 1 + RATE_LIMIT_STOP  # type: ignore[attr-defined]
    assert "429" in (store.get_meta("last_crawl_error:respo") or "")
    assert store.meta_keys("crawl_skip:respo:") == set()  # 429 nie jest trwałym pominięciem
    assert store.get_meta("crawl_used:respo") == f"{TODAY.isoformat()}:1"  # 429 nie zjada dziennego limitu
    again = crawl(store, source, fetch=_fetch_429(ok=set(urls)), today=TODAY, limit=50)
    assert again.added == 19


def test_crawl_isolated_429_does_not_stop(tmp_path: Path) -> None:
    store = Store(tmp_path / "d.db")
    source = FakeSource(["u1", "u2", "u3", "u4"])
    fetch = _fetch_429(ok={"u1", "u3", "u4"})
    result = crawl(store, source, fetch=fetch, today=TODAY, limit=50)
    assert (result.added, result.errors) == (3, 1)
