"""The Fixture Provider: one provider driven by data, deterministic, and never dead-ending."""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest
from conftest import write_option_fixtures

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.options.fixture import FIXTURE_SOURCE_ID, FixtureOptionSource
from tourganize.domain.options.query import NO_MATCH, SYNTHESISED, OptionQuery
from tourganize.domain.requirements import (
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
)
from tourganize.platform.errors import ConfigurationError

KIND: Final = "alpha"

SCHEMA: Final = RequirementSchema(
    schema_key="alpha.v1",
    component_kind=KIND,
    fields=(
        FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),
        FieldSpec("date_range", FieldKind.DATE_RANGE, Obligation.BLOCKING, "ask.alpha.when"),
        FieldSpec("min_score", FieldKind.SCORE, Obligation.OPTIONAL, "ask.alpha.min_score"),
    ),
    blocking_rules=(
        BlockingRule("where", (("place",),)),
        BlockingRule("when", (("date_range",),)),
    ),
)


def requirements(**values: object) -> RequirementSet:
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty(KIND).with_updates(updates, schema=SCHEMA)


def a_query(slate_size: int = 3, **values: object) -> OptionQuery:
    return OptionQuery(kind_key=KIND, requirements=requirements(**values), slate_size=slate_size)


@pytest.fixture
def source(option_fixture_dir: Path) -> FixtureOptionSource:
    return FixtureOptionSource(option_fixture_dir, FrozenClock(DEFAULT_MOMENT))


def write_one(root: Path, kind_key: str, name: str, text: str) -> Path:
    directory = root / kind_key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(text, encoding="utf-8")
    return path


# -- what it serves --------------------------------------------------------------------------


def test_the_kinds_it_holds_data_for_are_the_directories_it_finds(
    source: FixtureOptionSource,
) -> None:
    """Component Kinds are data here too: a directory, not a class and not a branch."""
    assert source.kind_keys == frozenset({"alpha", "beta"})
    assert source.source_id == FIXTURE_SOURCE_ID


def test_a_matching_query_is_answered_from_the_recording(source: FixtureOptionSource) -> None:
    result = source.search(a_query(place="Paris", date_range="2026-10-23/2026-10-28"))

    assert result.diagnostics == ()
    assert all(option.kind_key == KIND for option in result.options)
    assert all(option.provenance.source_id == FIXTURE_SOURCE_ID for option in result.options)
    assert all(option.price is not None for option in result.options)


def test_a_place_matches_whatever_case_and_accents_it_was_written_in(
    tmp_path: Path,
) -> None:
    """``paris`` and ``Paris`` are one place; ``פריז`` is a second declared spelling."""
    write_one(
        tmp_path,
        KIND,
        "zurich",
        """
        {"kind_key": "alpha", "matchable": ["place"],
         "match": {"place": ["Zürich", "ציריך"]},
         "options": [{"external_ref": "z-1", "price": {"amount_minor": 100, "currency": "EUR"}}]}
        """,
    )
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    for spelling in ("Zürich", "zurich", "ZURICH", "ציריך"):
        result = source.search(a_query(place=spelling))
        assert result.diagnostics == (), spelling


def test_a_date_range_matches_by_overlap_rather_than_equality(source: FixtureOptionSource) -> None:
    inside = source.search(a_query(place="Paris", date_range="2026-10-23/2026-10-28"))
    outside = source.search(a_query(place="Paris", date_range="2029-10-23/2029-10-28"))

    assert inside.diagnostics == ()
    assert SYNTHESISED in outside.diagnostics


def test_a_requirement_the_query_does_not_hold_never_excludes_a_file(
    source: FixtureOptionSource,
) -> None:
    """A traveller who has not said where they are going has not ruled anything out."""
    result = source.search(a_query())

    assert result.diagnostics == ()
    assert result.options


# -- determinism -----------------------------------------------------------------------------


def test_the_same_query_twice_returns_the_same_options_in_the_same_order(
    source: FixtureOptionSource,
) -> None:
    first = source.search(a_query(place="Paris"))
    second = source.search(a_query(place="Paris"))

    assert [option.option_id for option in first.options] == [
        option.option_id for option in second.options
    ]


def test_two_processes_would_agree_because_the_order_is_hashed_not_shuffled(
    option_fixture_dir: Path,
) -> None:
    """A fresh source, with a fresh cache, answers the same way: no per-process state decides."""
    first = FixtureOptionSource(option_fixture_dir, FrozenClock(DEFAULT_MOMENT))
    second = FixtureOptionSource(option_fixture_dir, FrozenClock(DEFAULT_MOMENT))

    assert [option.option_id for option in first.search(a_query(place="Paris")).options] == [
        option.option_id for option in second.search(a_query(place="Paris")).options
    ]


def test_the_digest_seeds_the_order_so_a_refinement_changes_the_candidates(
    source: FixtureOptionSource,
) -> None:
    """The property F06 asks for by name: changing what was asked for changes what comes back."""
    before = source.search(a_query(2, place="Paris"))
    after = source.search(a_query(2, place="Paris", min_score=8.5))

    assert before.options
    assert [option.option_id for option in before.options] != [
        option.option_id for option in after.options
    ]


def test_a_refinement_that_changes_nothing_changes_no_slate(source: FixtureOptionSource) -> None:
    """Two Requirement Sets that ask for the same thing share a digest, and share an answer."""
    plain = source.search(a_query(place="Paris"))
    same = source.search(a_query(place="Paris"))

    assert plain.options == same.options


# -- slate size ------------------------------------------------------------------------------


@pytest.mark.parametrize(("asked", "expected"), [(1, 1), (3, 3), (10, 5)])
def test_slate_size_is_honoured_including_when_fewer_exist(
    source: FixtureOptionSource, asked: int, expected: int
) -> None:
    """Five options are recorded for Paris, so asking for ten gets five, not an error."""
    result = source.search(a_query(asked, place="Paris"))

    assert len(result.options) == expected
    assert result.partial is (expected < 5)


# -- the synthetic fallback ------------------------------------------------------------------


def test_a_query_nothing_matches_is_answered_synthetically_and_says_so(
    source: FixtureOptionSource,
) -> None:
    result = source.search(a_query(place="Reykjavik"))

    assert result.diagnostics == (NO_MATCH, SYNTHESISED)
    assert len(result.options) == 3
    assert all(option.facts["synthesised"] is True for option in result.options)


def test_a_component_kind_with_no_recording_at_all_is_told_apart(
    source: FixtureOptionSource,
) -> None:
    """Nothing recorded and nothing matching are different silences, and the codes say which."""
    result = source.search(
        OptionQuery(kind_key="omega", requirements=RequirementSet.empty("omega"), slate_size=3)
    )

    assert result.diagnostics == (SYNTHESISED,)
    assert all(option.kind_key == "omega" for option in result.options)


def test_a_synthetic_set_is_deterministic_and_priced(source: FixtureOptionSource) -> None:
    first = source.search(a_query(place="Reykjavik"))
    second = source.search(a_query(place="Reykjavik"))

    assert [option.option_id for option in first.options] == [
        option.option_id for option in second.options
    ]
    assert [option.price for option in first.options] == [option.price for option in second.options]
    assert all(option.price is not None for option in first.options)


def test_a_synthetic_option_invents_no_travel_data(source: FixtureOptionSource) -> None:
    """A provider that invents plausible names is one whose output nobody can tell apart."""
    result = source.search(a_query(place="Reykjavik"))

    for option in result.options:
        assert set(option.facts) == {"variant", "synthesised"}


def test_a_missing_fixture_root_is_answered_rather_than_refused(tmp_path: Path) -> None:
    """A demonstration never dead-ends — and `doctor` is where the missing tree is reported."""
    source = FixtureOptionSource(tmp_path / "nowhere", FrozenClock(DEFAULT_MOMENT))

    assert source.kind_keys == frozenset()
    assert source.search(a_query(place="Paris")).diagnostics == (SYNTHESISED,)


# -- a fixture tree that is not one ----------------------------------------------------------


def test_a_file_whose_kind_disagrees_with_its_directory_is_refused(tmp_path: Path) -> None:
    write_one(tmp_path, KIND, "wrong", '{"kind_key": "beta", "options": []}')
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="its directory says"):
        source.search(a_query(place="Paris"))


def test_a_file_that_is_not_json_names_itself(tmp_path: Path) -> None:
    write_one(tmp_path, KIND, "broken", "{not json at all")
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="invalid fixture file"):
        source.search(a_query(place="Paris"))


def test_an_unknown_key_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """The catalog reader's rule: a misspelled key would silently change what is served."""
    write_one(tmp_path, KIND, "typo", '{"kind_key": "alpha", "optoins": [], "options": []}')
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="optoins"):
        source.search(a_query(place="Paris"))


def test_a_match_on_a_field_that_is_not_matchable_is_refused(tmp_path: Path) -> None:
    write_one(
        tmp_path,
        KIND,
        "mismatch",
        '{"kind_key": "alpha", "matchable": ["place"], "match": {"date_range": ["x"]},'
        ' "options": []}',
    )
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="matchable"):
        source.search(a_query(place="Paris"))


def test_two_options_sharing_an_external_ref_are_refused(tmp_path: Path) -> None:
    """One of them could never be presented: the ``option_id`` is derived from the reference."""
    write_one(
        tmp_path,
        KIND,
        "twins",
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1"}, {"external_ref": "a-1"}]}',
    )
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="external_ref"):
        source.search(a_query(place="Paris"))


def test_a_price_with_no_currency_is_refused_by_the_file_that_declares_it(
    tmp_path: Path,
) -> None:
    write_one(
        tmp_path,
        KIND,
        "bare",
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1",'
        ' "price": {"amount_minor": 100}}]}',
    )
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="ISO 4217"):
        source.search(a_query(place="Paris"))


def test_files_are_read_once_so_a_conversation_never_changes_underneath_itself(
    tmp_path: Path,
) -> None:
    write_option_fixtures(tmp_path)
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))
    before = source.search(a_query(place="Paris"))

    (tmp_path / KIND / "paris.json").write_text('{"kind_key": "alpha", "options": []}', "utf-8")

    assert source.search(a_query(place="Paris")).options == before.options


#: One malformed fixture file per rule the reader enforces, with the words its refusal must
#: contain. A fixture tree is hand-written, so every one of these is a mistake somebody will
#: actually make — and a refusal that does not name the file and the reason is a refusal that
#: costs a debugging session.
BAD_FILES: Final = {
    "not a mapping": ("[]", "must be an object"),
    "options not a list": ('{"kind_key": "alpha", "options": {}}', "must be a list"),
    "matchable not a list": (
        '{"kind_key": "alpha", "matchable": "place", "options": []}',
        "must be a list",
    ),
    "match not an object": (
        '{"kind_key": "alpha", "matchable": ["place"], "match": [], "options": []}',
        "must be an object",
    ),
    "match values not a list": (
        '{"kind_key": "alpha", "matchable": ["place"], "match": {"place": "Paris"}, "options": []}',
        "list of accepted values",
    ),
    "option not an object": ('{"kind_key": "alpha", "options": ["a-1"]}', "is not an object"),
    "option unknown key": (
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1", "colour": "blue"}]}',
        "unknown key",
    ),
    "external_ref missing": ('{"kind_key": "alpha", "options": [{}]}', "external_ref must be text"),
    "external_ref blank": (
        '{"kind_key": "alpha", "options": [{"external_ref": "  "}]}',
        "external_ref must be text",
    ),
    "facts not an object": (
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1", "facts": []}]}',
        "facts must be an object",
    ),
    "price not an object": (
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1", "price": 100}]}',
        "price must be an object",
    ),
    "price unknown key": (
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1",'
        ' "price": {"amount_minor": 1, "currency": "EUR", "vat": 2}}]}',
        "unknown key",
    ),
    "price amount not whole": (
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1",'
        ' "price": {"amount_minor": 74.5, "currency": "EUR"}}]}',
        "whole number of minor units",
    ),
    "price amount is a bool": (
        '{"kind_key": "alpha", "options": [{"external_ref": "a-1",'
        ' "price": {"amount_minor": true, "currency": "EUR"}}]}',
        "whole number of minor units",
    ),
    "kind_key missing": ('{"options": []}', "its directory says"),
}


@pytest.mark.parametrize(("text", "expected"), BAD_FILES.values(), ids=list(BAD_FILES))
def test_a_malformed_fixture_file_is_refused_by_name(
    tmp_path: Path, text: str, expected: str
) -> None:
    write_one(tmp_path, KIND, "bad", text)
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match=expected) as raised:
        source.search(a_query(place="Paris"))

    assert "bad.json" in str(raised.value), "a refusal has to name the file it is about"


def test_a_file_that_cannot_be_read_at_all_is_refused(tmp_path: Path) -> None:
    """A directory where a file should be: unreadable is a misconfigured installation."""
    (tmp_path / KIND).mkdir(parents=True)
    (tmp_path / KIND / "adirectory.json").mkdir()
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    with pytest.raises(ConfigurationError, match="could not be read"):
        source.search(a_query(place="Paris"))


def test_a_date_spelling_that_is_not_a_range_matches_nothing(tmp_path: Path) -> None:
    """A malformed range in `match` excludes the file rather than crashing the search."""
    write_one(
        tmp_path,
        KIND,
        "vague",
        '{"kind_key": "alpha", "matchable": ["date_range"],'
        ' "match": {"date_range": ["soon", "2026-13-45/2026-99-01"]},'
        ' "options": [{"external_ref": "a-1"}]}',
    )
    source = FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT))

    result = source.search(a_query(date_range="2026-10-23/2026-10-28"))

    assert SYNTHESISED in result.diagnostics


def test_a_directory_holding_no_json_is_not_a_kind_it_serves(tmp_path: Path) -> None:
    """`kind_keys` reports what was *recorded*, so an empty directory is not a claim."""
    (tmp_path / "omega").mkdir(parents=True)
    (tmp_path / "omega" / "notes.txt").write_text("nothing here", encoding="utf-8")
    write_option_fixtures(tmp_path)

    assert FixtureOptionSource(tmp_path, FrozenClock(DEFAULT_MOMENT)).kind_keys == frozenset(
        {"alpha", "beta"}
    )
