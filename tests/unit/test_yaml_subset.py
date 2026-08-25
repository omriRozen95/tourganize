"""The configuration reader: the subset it accepts, and everything it refuses loudly."""

from __future__ import annotations

from pathlib import Path

import pytest

from tourganize.platform.errors import ConfigurationError
from tourganize.platform.yaml_subset import read_config_file, read_yaml_subset


def read(text: str) -> object:
    return read_yaml_subset(text, origin="test.yaml")


def test_a_mapping_of_scalars() -> None:
    assert read("version: 1\nname: catalog\nready: true\nabsent: null\n") == {
        "version": 1,
        "name": "catalog",
        "ready": True,
        "absent": None,
    }


def test_an_empty_document_is_none() -> None:
    assert read("") is None
    assert read("# only a comment\n\n") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("value: 0", 0),
        ("value: -12", -12),
        ("value: +12", 12),
        ("value: 8.7", 8.7),
        ("value: -0.5", -0.5),
        ("value: 1e3", "1e3"),
        ("value: true", True),
        ("value: FALSE", False),
        ("value: ~", None),
        ("value:", None),
        ("value: text", "text"),
        ("value: 'quoted: colon'", "quoted: colon"),
        ('value: "escaped \\"quote\\""', 'escaped "quote"'),
        ("value: 'it''s'", "it's"),
        ("value: he_IL", "he_IL"),
        ("value: 'true'", "true"),
        ("value: '42'", "42"),
    ],
)
def test_scalars(text: str, expected: object) -> None:
    assert read(text) == {"value": expected}


def test_a_hash_inside_quotes_is_not_a_comment() -> None:
    assert read('value: "a # b"  # trailing') == {"value": "a # b"}


def test_a_block_sequence_of_mappings_indented_under_its_key() -> None:
    document = read(
        "kinds:\n"
        "  - kind_key: alpha\n"
        "    weight: 300\n"
        "  - kind_key: beta\n"
        "    weight: 200\n"
    )

    assert document == {
        "kinds": [{"kind_key": "alpha", "weight": 300}, {"kind_key": "beta", "weight": 200}]
    }


def test_a_block_sequence_at_the_indentation_of_its_key() -> None:
    document = read("kinds:\n- kind_key: alpha\n- kind_key: beta\n")

    assert document == {"kinds": [{"kind_key": "alpha"}, {"kind_key": "beta"}]}


def test_a_block_sequence_of_scalars() -> None:
    assert read("keys:\n  - alpha\n  - beta\n") == {"keys": ["alpha", "beta"]}


def test_flow_collections() -> None:
    assert read("a: []\nb: [alpha, beta]\nc: {}\nd: {min: 0, max: 10}\n") == {
        "a": [],
        "b": ["alpha", "beta"],
        "c": {},
        "d": {"min": 0, "max": 10},
    }


def test_a_quoted_key_in_a_flow_mapping() -> None:
    assert read('constraints: {"min": 0, "max": 10}') == {"constraints": {"min": 0, "max": 10}}


def test_nested_blocks() -> None:
    document = read(
        "outer:\n"
        "  inner:\n"
        "    leaf: 1\n"
        "  list:\n"
        "    - one\n"
        "    - two\n"
        "after: done\n"
    )

    assert document == {
        "outer": {"inner": {"leaf": 1}, "list": ["one", "two"]},
        "after": "done",
    }


def test_a_leading_document_marker_is_accepted() -> None:
    assert read("---\nversion: 1\n") == {"version": 1}


def test_hebrew_content_survives_unchanged() -> None:
    """Hebrew is a first-class content language; the reader must not touch the bytes."""
    assert read("place: פריז\nquoted: 'תל אביב'\n") == {"place": "פריז", "quoted": "תל אביב"}


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("value: |\n  a block scalar\n", "block scalars"),
        ("value: >\n  folded\n", "block scalars"),
        ("anchor: &a 1\n", "anchors"),
        ("alias: *a\n", "anchors"),
        ("tagged: !!str 1\n", "anchors"),
        ("one: 1\n---\ntwo: 2\n", "multiple documents"),
        ("one: 1\n...\n", "document end marker"),
        ("outer:\n\tinner: 1\n", "spaces, not tabs"),
        ("no colon here\n", "expected `key: value`"),
        ("value: [unterminated\n", "unterminated flow collection"),
        ("value: 'unterminated\n", "unterminated quoted string"),
        ("value: [a, , b]\n", "empty entry"),
        ("value: bare: colon\n", "quote a plain string"),
        ("dup: 1\ndup: 2\n", "duplicate key"),
        ("value: {broken}\n", "expected `key: value` inside a flow mapping"),
        ('value: "bad \\escape"\n', "unsupported escape"),
        ("  indented: 1\n", "must start at column 1"),
        ("- one\nrogue: 2\n", "unexpected content"),
        ("items:\n  - - nested\n", "inline nested sequence"),
    ],
)
def test_everything_outside_the_subset_is_refused_with_the_line(text: str, reason: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        read(text)

    assert reason in str(raised.value)
    assert "test.yaml line " in str(raised.value)


def test_a_file_that_does_not_exist_says_so(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as raised:
        read_config_file(tmp_path / "absent.yaml")

    assert "does not exist" in str(raised.value)


def test_a_directory_is_not_a_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        read_config_file(tmp_path)


def test_a_file_that_is_not_utf8_says_so(tmp_path: Path) -> None:
    path = tmp_path / "latin.yaml"
    path.write_bytes(b"place: Z\xfcrich\n")

    with pytest.raises(ConfigurationError) as raised:
        read_config_file(path)

    assert "not valid UTF-8" in str(raised.value)


def test_a_real_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "kinds.yaml"
    path.write_text("version: 1\nkinds:\n  - kind_key: alpha\n", encoding="utf-8")

    assert read_config_file(path) == {"version": 1, "kinds": [{"kind_key": "alpha"}]}
