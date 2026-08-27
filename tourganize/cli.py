"""The ``tourganize`` command line.

Three commands work today: ``--version``, ``doctor`` and ``catalog`` (``show``, ``validate``
and ``gaps``). The rest of the surface is registered as stubs that name the feature which
will implement them, so the shape of the finished application is discoverable from the first
release and no later feature has to invent its own entry point.

Exit codes are part of the contract:

===  ==========================================================
0    success
1    ``doctor`` found a failing check
2    the invocation was not usable: a sub-command that is
     registered but not implemented yet, an action nobody gave,
     or an argument the command cannot act on (argparse's own
     code for a bad invocation, too)
3    :class:`~tourganize.platform.errors.ConfigurationError`
===  ==========================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Final, TextIO

from tourganize import __version__
from tourganize.application.composition import Container, build_container
from tourganize.application.diagnostics import run_diagnostics
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.errors import UnknownComponentKindError, UnknownFieldError
from tourganize.domain.requirements import (
    GapReport,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
    analyse,
)
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.logging import configure_logging
from tourganize.platform.settings import Settings, unrecognised_keys
from tourganize.ports.catalog import ComponentCatalog

__all__ = ["main"]

EXIT_OK: Final = 0
EXIT_DOCTOR_FAILED: Final = 1
EXIT_NOT_IMPLEMENTED: Final = 2
#: The same code as ``EXIT_NOT_IMPLEMENTED``, deliberately: argparse owns 2 for "this
#: invocation was not usable", and an argument the command cannot act on is that, not a
#: broken installation. Two names because the two situations read nothing like each other at
#: the call site.
EXIT_USAGE_ERROR: Final = 2
EXIT_CONFIGURATION_ERROR: Final = 3

#: The ``catalog`` actions this release implements.
CATALOG_ACTIONS: Final = ("show", "validate", "gaps")

#: Sub-commands of ``catalog`` that later features implement:
#: name -> (feature, what that feature delivers). Each entry is deleted from this table by
#: the feature that implements the command.
PLANNED_CATALOG_COMMANDS: Final[Mapping[str, tuple[str, str]]] = {
    "agenda": ("F04", "the Planning Agenda, with bands and ranks"),
}

#: Top-level sub-commands that later features implement, same convention as above.
PLANNED_COMMANDS: Final[Mapping[str, tuple[str, str]]] = {
    "chat": ("F07", "the terminal Presentation Surface"),
    "resume": ("F12", "session persistence and resume"),
    "export": ("F13", "itinerary projection and rendering"),
    "docs": ("F18", "the Knowledge Corpus: add, list, query, index"),
}


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, including the stub sub-commands."""
    parser = argparse.ArgumentParser(
        prog="tourganize",
        description="Tourganize — a conversational trip-planning assistant.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", metavar="command")

    subcommands.add_parser(
        "doctor", help="print resolved settings, selected adapters and per-port health"
    )
    _add_catalog_parser(subcommands)

    for name, (feature, summary) in PLANNED_COMMANDS.items():
        stub = subcommands.add_parser(name, help=f"[{feature}] {summary}")
        stub.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    return parser


def _add_catalog_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``catalog`` and its sub-commands, including the ones later features implement."""
    catalog = subcommands.add_parser("catalog", help="inspect and validate the Component Catalog")
    actions = catalog.add_subparsers(dest="catalog_command", metavar="action")
    actions.add_parser("show", help="list the declared Component Kinds")
    actions.add_parser(
        "validate", help="load the catalog and its schemas, and report every problem found"
    )
    gaps = actions.add_parser("gaps", help="what one Component Kind still needs before planning")
    gaps.add_argument("--kind", required=True, metavar="KIND_KEY", help="the Component Kind")
    gaps.add_argument(
        "--set",
        dest="values",
        default=None,
        metavar="JSON",
        help='requirement values already known, as a JSON object: \'{"place": "Paris"}\'',
    )
    for name, (feature, summary) in PLANNED_CATALOG_COMMANDS.items():
        stub = actions.add_parser(name, help=f"[{feature}] {summary}")
        stub.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run one CLI invocation and return its exit code."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    env = environ if environ is not None else os.environ

    parser = build_parser()
    args = parser.parse_args(argv)
    command: str | None = args.command

    if command is None:
        parser.print_help(out)
        return EXIT_OK

    # Before dispatch, so that an invalid setting is exit 3 whichever sub-command was asked
    # for. A stub that answered "not implemented" while the configuration was broken would
    # report the less useful of the two problems.
    try:
        settings = Settings.from_env(env)
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=err)
        return EXIT_CONFIGURATION_ERROR

    if command == "catalog" and args.catalog_command in PLANNED_CATALOG_COMMANDS:
        feature, summary = PLANNED_CATALOG_COMMANDS[args.catalog_command]
        print(
            f"tourganize catalog {args.catalog_command} is not implemented until "
            f"{feature} ({summary}).",
            file=err,
        )
        return EXIT_NOT_IMPLEMENTED

    if command in PLANNED_COMMANDS:
        feature, summary = PLANNED_COMMANDS[command]
        print(
            f"tourganize {command} is not implemented until {feature} ({summary}).",
            file=err,
        )
        return EXIT_NOT_IMPLEMENTED

    logger = configure_logging(settings, stream=err)
    logger.debug("settings resolved", extra={"kind": "startup", "profile": settings.env})

    # A command that reads configuration may still find it broken — a catalog with a cycle in
    # it is exit 3 for the same reason a bad log level is, and `doctor` reports it as a failing
    # check instead of raising, so both answers stay honest.
    try:
        if command == "doctor":
            return _doctor(settings, env, out=out)
        if command == "catalog":
            return _catalog(
                build_container(settings),
                args.catalog_command,
                kind_key=getattr(args, "kind", ""),
                values=getattr(args, "values", None),
                out=out,
                err=err,
            )
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=err)
        return EXIT_CONFIGURATION_ERROR

    parser.print_help(err)  # pragma: no cover - argparse rejects unknown commands first
    return EXIT_NOT_IMPLEMENTED  # pragma: no cover


def _doctor(settings: Settings, env: Mapping[str, str], *, out: TextIO) -> int:
    container = build_container(settings)
    report = run_diagnostics(container, version=__version__, unrecognised=unrecognised_keys(env))
    print(report.render(), file=out)
    return EXIT_OK if report.ok else EXIT_DOCTOR_FAILED


def _catalog(
    container: Container,
    action: str | None,
    *,
    kind_key: str,
    values: str | None,
    out: TextIO,
    err: TextIO,
) -> int:
    """``catalog show``, ``validate`` and ``gaps``. All three load the catalog for real.

    The action is checked before the file is read, so ``tourganize catalog`` with no action
    says what it offers rather than reporting whatever is wrong with the catalog.
    """
    if action not in CATALOG_ACTIONS:
        print(f"tourganize catalog needs an action: {', '.join(CATALOG_ACTIONS)}", file=err)
        return EXIT_NOT_IMPLEMENTED
    if action == "gaps":
        return _catalog_gaps(container, kind_key=kind_key, values=values, out=out, err=err)

    kinds = container.component_catalog.kinds()
    origin = container.settings.catalog_path
    if action == "validate":
        # Everything is loaded before anything is printed: a run that ends in exit 3 says
        # nothing on stdout, so a caller can trust that output means "all of it is sound".
        schemas = _load_every_schema(container.component_catalog)
        enabled = sum(1 for kind in kinds if kind.enabled)
        print(
            f"{origin}: {len(kinds)} Component Kinds ({enabled} enabled), no problems found\n"
            f"{container.settings.schema_dir}: {len(schemas)} Requirement Schemas, "
            f"no problems found",
            file=out,
        )
        return EXIT_OK
    print(_render_kinds(kinds, origin=str(origin)), file=out)
    return EXIT_OK


def _load_every_schema(catalog: ComponentCatalog) -> tuple[RequirementSchema, ...]:
    """Load the Requirement Schema of every *enabled* kind, raising on the first bad one.

    Enabled only: a disabled kind is not plannable, so requiring it to still carry a schema
    would make disabling a kind harder than deleting it — which is the opposite of what the
    ``enabled`` flag is for.
    """
    return tuple(catalog.schema_for(kind.kind_key) for kind in catalog.enabled_kinds())


def _catalog_gaps(
    container: Container,
    *,
    kind_key: str,
    values: str | None,
    out: TextIO,
    err: TextIO,
) -> int:
    """``catalog gaps`` — the Gap Report for one Component Kind, with optional known values.

    Everything the dialogue will do with a Gap Report, shown as text: what still blocks
    planning, what is merely a filter, and what was supplied but cannot be used.
    """
    try:
        schema = container.component_catalog.schema_for(kind_key)
    except UnknownComponentKindError as exc:
        print(f"tourganize catalog gaps: {exc}", file=err)
        return EXIT_USAGE_ERROR
    try:
        requirements = _requirements_from(schema, values)
    except (UnknownFieldError, ValueError) as exc:
        print(f"tourganize catalog gaps: --set {exc}", file=err)
        return EXIT_USAGE_ERROR
    print(_render_gaps(analyse(schema, requirements), schema), file=out)
    return EXIT_OK


def _requirements_from(schema: RequirementSchema, values: str | None) -> RequirementSet:
    """Read ``--set`` as a JSON object of ``field name -> value``.

    Every value is recorded as coming from the traveller on turn zero, because that is what a
    value typed at a prompt is. Values that fail their field's validation are kept, not
    refused: the Gap Report is where they are reported, under ``invalid``.
    """
    empty = RequirementSet.empty(schema.component_kind)
    if values is None:
        return empty
    try:
        supplied = json.loads(values)
    except json.JSONDecodeError as exc:
        raise ValueError(f"is not valid JSON: {exc}") from exc
    if not isinstance(supplied, dict):
        raise ValueError(f"must be a JSON object of field names to values, got {supplied!r}")
    updates = [
        RequirementUpdate(field_name=str(name), value=value, raw_text=json.dumps(value))
        for name, value in supplied.items()
    ]
    return empty.with_updates(updates, schema=schema)


def _render_gaps(report: GapReport, schema: RequirementSchema) -> str:
    """Render a Gap Report as three tables and the one line that gates everything."""
    lines = [
        f"{report.component_kind} (schema {schema.schema_key})",
        "",
        f"is_plannable: {'true' if report.is_plannable else 'false'}",
        "",
        f"blocking ({len(report.blocking)}):",
    ]
    lines += _indented(
        _table(
            ("rule", "satisfied by", "next question"),
            [
                (
                    gap.rule_name,
                    "  |  ".join(" + ".join(group) for group in gap.field_names),
                    gap.next_field().prompt_message_key,
                )
                for gap in report.blocking
            ],
        )
    )
    lines += ["", f"optional ({len(report.optional)}):"]
    lines += _indented(
        _table(
            ("field", "kind", "question"),
            [
                (spec.name, spec.field_kind.value, spec.prompt_message_key)
                for spec in report.optional
            ],
        )
    )
    lines += ["", f"invalid ({len(report.invalid)}):"]
    lines += _indented(
        _table(
            ("field", "reason", "detail"),
            [(bad.field_name, bad.reason_message_key, bad.detail) for bad in report.invalid],
        )
    )
    return "\n".join(line.rstrip() for line in lines)


def _indented(lines: Sequence[str]) -> list[str]:
    """Indent a rendered table under the heading that introduces it."""
    return [f"  {line}" if line else line for line in lines]


def _render_kinds(kinds: Sequence[ComponentKind], *, origin: str) -> str:
    """Render the catalog as a table, in declaration order — the order F04 ranks ties by."""
    rows = [
        (
            kind.kind_key,
            str(kind.priority_weight),
            ", ".join(kind.requires_outcome_of) or "-",
            kind.schema_key,
            kind.message_key,
            "yes" if kind.enabled else "no",
        )
        for kind in kinds
    ]
    if not rows:
        return f"{origin}\n\nno Component Kinds declared"
    headers = ("kind_key", "weight", "awaits outcome of", "schema_key", "message_key", "enabled")
    return "\n".join([origin, "", *_table(headers, rows)])


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    """Column-align one table, header rule included. No rows renders as a single dash.

    Trailing padding is trimmed: the last column's width is nobody's business, and a line of
    invisible spaces is the kind of thing that makes a golden output file hard to diff.
    """
    if not rows:
        return ["-"]
    widths = [max(len(cell) for cell in column) for column in zip(headers, *rows, strict=True)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths, strict=True))

    rule = ["-" * width for width in widths]
    return [line(headers).rstrip(), line(rule).rstrip(), *(line(row).rstrip() for row in rows)]


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
