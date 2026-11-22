from pathlib import Path

import pytest

from dieta.dictionary import (
    SEED_PATH,
    classify_rules,
    load_seed,
    make_llm_ask,
    map_with_llm,
    normalize_name,
)


def test_normalize_name_collapses_case_and_spaces() -> None:
    assert normalize_name("  Twaróg   KLINEK ") == "twaróg klinek"


def test_classify_rules_known_ingredient() -> None:
    assert classify_rules("Mleko UHT 1,5%") == ("mleko", "chilled")


def test_classify_rules_unknown_returns_none_pair() -> None:
    assert classify_rules("Piramidki smaku") == (None, None)


def test_load_seed_builds_entries_keyed_by_normalized_name() -> None:
    seed = load_seed(SEED_PATH)
    entry = seed["chleb tost. pszenny1"]
    assert entry.klasa == "skladnik"
    assert entry.skladnik == "chleb tostowy pszenny"
    assert "piramidki smaku" in seed
    assert seed["piramidki smaku"].klasa == "slodycze_przekaski"


def test_load_seed_duplicate_names_keep_one_entry_with_most_purchases(tmp_path: Path) -> None:
    path = tmp_path / "seed.json"
    path.write_text(
        '[{"k":"1","name":"Ser","n":2,"spend":1,"last":"2026-01-01","cat":"food","klasa":"skladnik","skladnik":"a"},'
        '{"k":"2","name":"ser ","n":5,"spend":1,"last":"2026-01-01","cat":"food",'
        '"klasa":"gotowe","skladnik":""}]',
        encoding="utf-8",
    )
    assert load_seed(path)["ser"].klasa == "gotowe"


def test_map_with_llm_batches_by_25_and_maps_result() -> None:
    calls: list[list[str]] = []

    def ask(names: list[str]) -> list[dict[str, object]]:
        calls.append(names)
        return [
            {"i": i, "klasa": "skladnik", "skladnik": "Pomidory", "trwalosc": "fresh"}
            for i in range(len(names))
        ]

    names = [f"produkt {n}" for n in range(60)]
    result = map_with_llm(names, ask=ask)
    assert [len(c) for c in calls] == [25, 25, 10]
    assert len(result) == 60
    assert result[0].name == "produkt 0"
    assert result[0].ingredient == "pomidory"
    assert result[0].durability == "fresh"


def test_map_with_llm_retries_once_then_pending_on_failure() -> None:
    calls: list[list[str]] = []

    def ask(names: list[str]) -> None:
        calls.append(names)
        return None

    result = map_with_llm(["a", "b"], ask=ask)
    assert len(calls) == 2
    assert [(r.ingredient, r.durability) for r in result] == [(None, None), (None, None)]


def test_map_with_llm_garbage_items_become_unmapped() -> None:
    def ask(names: list[str]) -> list[dict[str, object]]:
        return [{"i": 0, "klasa": "bzdura", "skladnik": 7, "trwalosc": "wieczne"}, "śmieć"]  # type: ignore[list-item]

    result = map_with_llm(["x", "y"], ask=ask)
    assert result[0].ingredient is None
    assert result[0].durability is None
    assert result[1].ingredient is None


def test_map_with_llm_non_skladnik_class_has_no_ingredient() -> None:
    def ask(names: list[str]) -> list[dict[str, object]]:
        return [{"i": 0, "klasa": "napoj", "skladnik": "", "trwalosc": ""}]

    result = map_with_llm(["cola"], ask=ask)
    assert result[0].klasa == "napoj"
    assert result[0].ingredient is None


def test_make_llm_ask_returns_none_on_network_error_without_key_in_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("dieta.dictionary.urllib.request.urlopen", boom)
    ask = make_llm_ask("http://llm.local:3003", "SEKRETNY-KLUCZ-123")
    assert ask(["mleko"]) is None
    out = capsys.readouterr()
    assert "SEKRETNY-KLUCZ-123" not in out.out + out.err
