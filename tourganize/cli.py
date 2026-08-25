"""The ``tourganize`` command line.

Three commands work today: ``--version``, ``doctor`` and ``catalog`` (``show`` and
``validate``). The rest of the surface is registered as stubs that name the feature which
will implement them, so the shape of the finished application is discoverable from the first
release and no later feature has to invent its own entry point.

Exit codes are part of the contract:

===  ==========================================================
0    success
1    ``doctor`` found a failing check
2    the sub-command is registered but not implemented yet, or
     it needs an action nobody gave it (argparse's own code too)
3    :class:`~tourganize.platform.errors.ConfigurationError`
===  ==========================================================
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Final, TextIO

from tourganize import __version__
from tourganize.application.composition import Container, build_container
from tourganize.application.diagnostics import run_diagnostics
from tourganize.domain.catalog import ComponentKind
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.logging import configure_logging
from tourganize.platform.settings import Settings, unrecognised_keys

__all__ = ["main"]

EXIT_OK: Final = 0
EXIT_DOCTOR_FAILED: Final = 1
EXIT_NOT_IMPLEMENTED: Final = 2
EXIT_CONFIGURATION_ERROR: Final = 3

#: Sub-command -> (feature, what that feature delivers). Each entry is deleted from this
#: table by the feature that implements the command.
#: Sub-commands of ``catalog`` that later features implement, same convention as above.
PLANNED_CATALOG_COMMANDS: Final[Mapping[str, tuple[str, str]]] = {
    "gaps": ("F03", "the Gap Report for a Component Kind"),
    "agenda": ("F04", "the Planning Agenda, with bands and ranks"),
}

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
    actions.add_parser("validate", help="load the catalog and report every problem found")
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
            return _catalog(build_container(settings), args.catalog_command, out=out, err=err)
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


def _catalog(container: Container, action: str | None, *, out: TextIO, err: TextIO) -> int:
    """``catalog show`` and ``catalog validate``. Both load the catalog for real.

    The action is checked before the file is read, so ``tourganize catalog`` with no action
    says what it offers rather than reporting whatever is wrong with the catalog.
    """
    if action not in {"show", "validate"}:
        print("tourganize catalog needs an action: show, validate", file=err)
        return EXIT_NOT_IMPLEMENTED

    kinds = container.component_catalog.kinds()
    origin = container.settings.catalog_path
    if action == "validate":
        enabled = sum(1 for kind in kinds if kind.enabled)
        print(
            f"{origin}: {len(kinds)} Component Kinds ({enabled} enabled), no problems found",
            file=out,
        )
        return EXIT_OK
    print(_render_kinds(kinds, origin=str(origin)), file=out)
    return EXIT_OK


def _render_kinds(kinds: Sequence[ComponentKind], *, origin: str) -> str:
    """Render the catalog as a table, in declaration order — the order F04 ranks ties by."""
    headers = ("kind_key", "weight", "awaits outcome of", "schema_key", "message_key", "enabled")
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
    widths = [max(len(cell) for cell in column) for column in zip(headers, *rows, strict=True)]
    lines = [f"{origin}", ""]
    for row in (headers, *rows):
        lines.append("  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)))
        if row is headers:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(line.rstrip() for line in lines)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
