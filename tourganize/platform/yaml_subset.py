"""A deliberately small YAML reader for the files in ``config/``.

Why not a YAML library: the base install is dependency-free on purpose (F01), so that the
CPU image builds with nothing to fetch and ``tourganize doctor`` runs in it unmodified. The
configuration this application reads — the Component Catalog, later the Requirement Schemas,
the prompt manifests and the Message Catalogue — uses a small, boring corner of YAML, and
that corner is what this module reads.

The subset, in full:

* block mappings (``key: value``, or ``key:`` followed by an indented block);
* block sequences (``- item``), indented under their key or at the key's own indentation,
  with mappings, scalars or nested blocks as items;
* single-line flow collections of scalars — ``[a, b]``, ``{a: 1}``, ``[]``, ``{}``;
* scalars: integers, floats, ``true``/``false``, ``null``/``~``/empty, single- and
  double-quoted strings, and plain strings;
* ``#`` comments (outside quotes), blank lines, and one optional leading ``---``.

Everything else — anchors, aliases, tags, block scalars (``|``, ``>``), multiple documents,
tab indentation, a plain scalar containing ``": "`` — is **refused** with a
:class:`~tourganize.platform.errors.ConfigurationError` naming the file and the line. Being
strict is the point: an unsupported construct must be a loud failure at load, never a quiet
mis-reading of what someone meant.

This is plumbing, not a port. It lives in ``platform`` rather than in one adapter so that
every configuration-reading adapter can use it without importing a sibling adapter, which the
independence contract forbids. Swapping it for a real YAML library is a change to this one
module plus a dependency line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn

from tourganize.platform.errors import ConfigurationError

__all__ = ["read_config_file", "read_yaml_subset"]

_INTEGER: Final = re.compile(r"[-+]?[0-9]+")
_FLOAT: Final = re.compile(r"[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+]?[0-9]+)?")
_NULLS: Final = frozenset({"", "null", "Null", "NULL", "~"})
_TRUES: Final = frozenset({"true", "True", "TRUE"})
_FALSES: Final = frozenset({"false", "False", "FALSE"})
_ESCAPES: Final = {"\\": "\\", '"': '"', "/": "/", "n": "\n", "t": "\t", "r": "\r", "0": "\0"}
_REFUSED_PREFIXES: Final = ("&", "*", "!", "|", ">", "%", "@", "`")


@dataclass(frozen=True, slots=True)
class _Line:
    """One significant line: its number in the file, its indent, and its content."""

    number: int
    indent: int
    text: str


def read_config_file(path: Path) -> object:
    """Read and parse one configuration file, or raise ``ConfigurationError``."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigurationError(f"{path} does not exist") from exc
    except OSError as exc:
        raise ConfigurationError(f"{path} could not be read: {exc.strerror or exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigurationError(f"{path} is not valid UTF-8: {exc.reason}") from exc
    return read_yaml_subset(text, origin=str(path))


def read_yaml_subset(text: str, *, origin: str) -> object:
    """Parse ``text`` as the documented YAML subset. ``origin`` appears in error messages."""
    lines = _scan(text, origin)
    if not lines:
        return None
    if lines[0].indent != 0:
        _fail(origin, lines[0], "the document must start at column 1")
    value, index = _parse_block(lines, 0, 0, origin)
    if index != len(lines):
        _fail(origin, lines[index], "unexpected content after the end of the document")
    return value


def _scan(text: str, origin: str) -> list[_Line]:
    """Drop comments and blank lines; measure indentation; refuse tabs and second documents."""
    lines: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        body = _strip_comment(raw).rstrip()
        if not body.strip():
            continue
        stripped = body.strip()
        if stripped == "---":
            if lines:
                _fail(origin, _Line(number, 0, stripped), "multiple documents are not supported")
            continue
        if stripped == "...":
            _fail(origin, _Line(number, 0, stripped), "a document end marker is not supported")
        indent = len(body) - len(body.lstrip(" \t"))
        if "\t" in body[:indent]:
            _fail(origin, _Line(number, indent, stripped), "indent with spaces, not tabs")
        lines.append(_Line(number, indent, body[indent:]))
    return lines


def _strip_comment(text: str) -> str:
    """Remove a trailing ``#`` comment, ignoring ``#`` inside quotes."""
    quote = ""
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index]
        index += 1
    return text


def _parse_block(lines: list[_Line], index: int, indent: int, origin: str) -> tuple[object, int]:
    if _is_sequence_item(lines[index].text):
        return _parse_sequence(lines, index, indent, origin)
    return _parse_mapping(lines, index, indent, origin)


def _is_sequence_item(text: str) -> bool:
    return text == "-" or text.startswith("- ")


def _parse_mapping(
    lines: list[_Line], index: int, indent: int, origin: str
) -> tuple[dict[str, object], int]:
    mapping: dict[str, object] = {}
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if _is_sequence_item(line.text):
            _fail(origin, line, "a sequence item where a `key: value` pair was expected")
        split = _split_key(line.text, line, origin)
        if split is None:
            _fail(origin, line, f"expected `key: value`, got {line.text!r}")
        key, rest = split
        if key in mapping:
            _fail(origin, line, f"duplicate key {key!r}")
        if rest:
            mapping[key] = _scalar(rest, line, origin)
            index += 1
            continue
        mapping[key], index = _parse_value_of(lines, index, indent, origin)
    return mapping, index


def _parse_value_of(lines: list[_Line], index: int, indent: int, origin: str) -> tuple[object, int]:
    """Parse the value of a ``key:`` whose value is on the following lines, if any."""
    following = index + 1
    if following >= len(lines):
        return None, following
    nested = lines[following]
    if nested.indent > indent:
        return _parse_block(lines, following, nested.indent, origin)
    if nested.indent == indent and _is_sequence_item(nested.text):
        return _parse_sequence(lines, following, indent, origin)
    return None, following


def _parse_sequence(
    lines: list[_Line], index: int, indent: int, origin: str
) -> tuple[list[object], int]:
    items: list[object] = []
    while index < len(lines) and lines[index].indent == indent:
        line = lines[index]
        if not _is_sequence_item(line.text):
            break
        content = line.text[1:].lstrip()
        if not content:
            item, index = _parse_value_of(lines, index, indent, origin)
            items.append(item)
            continue
        if _split_key(content, line, origin) is not None:
            # `- key: value` opens a mapping whose remaining keys line up with `key`. The
            # item is re-read as a mapping line at that column, so one code path handles
            # both the inline first key and the indented rest.
            inline = _Line(line.number, indent + (len(line.text) - len(content)), content)
            spliced = [*lines[:index], inline, *lines[index + 1 :]]
            item, index = _parse_mapping(spliced, index, inline.indent, origin)
            items.append(item)
            continue
        items.append(_scalar(content, line, origin))
        index += 1
    return items, index


def _split_key(text: str, line: _Line, origin: str) -> tuple[str, str] | None:
    """Split ``key: value`` at the first structural colon, or return ``None``."""
    quote = ""
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ":" and depth == 0 and (index + 1 == len(text) or text[index + 1] in " \t"):
            key = text[:index].strip()
            if not key:
                _fail(origin, line, "a mapping key may not be empty")
            return _key_of(key, line, origin), text[index + 1 :].strip()
        index += 1
    return None


def _key_of(text: str, line: _Line, origin: str) -> str:
    value = _scalar(text, line, origin)
    if not isinstance(value, str):
        _fail(origin, line, f"a mapping key must be a string, got {text!r}")
    return value


def _scalar(raw: str, line: _Line, origin: str) -> object:
    text = raw.strip()
    if text.startswith("["):
        return _flow_sequence(text, line, origin)
    if text.startswith("{"):
        return _flow_mapping(text, line, origin)
    if text.startswith(_REFUSED_PREFIXES):
        _fail(origin, line, f"anchors, tags and block scalars are not supported: {text!r}")
    if _is_sequence_item(text):
        _fail(origin, line, "an inline nested sequence is not supported")
    if text[:1] in {'"', "'"}:
        return _quoted(text, line, origin)
    if text in _NULLS:
        return None
    if text in _TRUES:
        return True
    if text in _FALSES:
        return False
    if _INTEGER.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    if ": " in text:
        _fail(origin, line, f"quote a plain string containing ': ' — {text!r}")
    return text


def _quoted(text: str, line: _Line, origin: str) -> str:
    quote = text[0]
    body: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if quote == "'" and text[index + 1 : index + 2] == "'":
                body.append("'")
                index += 2
                continue
            if index + 1 != len(text):
                _fail(origin, line, "unexpected content after a quoted string")
            return "".join(body)
        if quote == '"' and char == "\\":
            escape = text[index + 1 : index + 2]
            if escape not in _ESCAPES:
                _fail(origin, line, f"unsupported escape '\\{escape}'")
            body.append(_ESCAPES[escape])
            index += 2
            continue
        body.append(char)
        index += 1
    _fail(origin, line, f"unterminated quoted string: {text!r}")


def _flow_sequence(text: str, line: _Line, origin: str) -> list[object]:
    inner = _flow_body(text, "]", line, origin)
    if not inner:
        return []
    return [_scalar(part, line, origin) for part in _split_flow(inner, line, origin)]


def _flow_mapping(text: str, line: _Line, origin: str) -> dict[str, object]:
    inner = _flow_body(text, "}", line, origin)
    mapping: dict[str, object] = {}
    if not inner:
        return mapping
    for part in _split_flow(inner, line, origin):
        split = _split_key(part, line, origin)
        if split is None:
            _fail(origin, line, f"expected `key: value` inside a flow mapping, got {part!r}")
        key, value = split
        mapping[key] = _scalar(value, line, origin)
    return mapping


def _flow_body(text: str, closing: str, line: _Line, origin: str) -> str:
    if not text.endswith(closing):
        _fail(origin, line, f"unterminated flow collection: {text!r}")
    return text[1:-1].strip()


def _split_flow(inner: str, line: _Line, origin: str) -> list[str]:
    parts: list[str] = []
    quote = ""
    depth = 0
    start = 0
    for index, char in enumerate(inner):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(inner[start:index].strip())
            start = index + 1
    parts.append(inner[start:].strip())
    if any(not part for part in parts):
        _fail(origin, line, f"empty entry in a flow collection: {inner!r}")
    return parts


def _fail(origin: str, line: _Line, message: str) -> NoReturn:
    raise ConfigurationError(f"{origin} line {line.number}: {message}")
