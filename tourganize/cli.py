"""The ``tourganize`` command line.

Two commands work today: ``--version`` and ``doctor``. The rest of the surface is
registered as stubs that name the feature which will implement them, so the shape of the
finished application is discoverable from the first release and no later feature has to
invent its own entry point.

Exit codes are part of the contract:

===  ==========================================================
0    success
1    ``doctor`` found a failing check
2    the sub-command is registered but not implemented yet
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
from tourganize.application.composition import build_container
from tourganize.application.diagnostics import run_diagnostics
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
PLANNED_COMMANDS: Final[Mapping[str, tuple[str, str]]] = {
    "catalog": ("F02", "the Component Catalog: show, validate, gaps, agenda"),
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

    for name, (feature, summary) in PLANNED_COMMANDS.items():
        stub = subcommands.add_parser(name, help=f"[{feature}] {summary}")
        stub.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    return parser


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

    if command in PLANNED_COMMANDS:
        feature, summary = PLANNED_COMMANDS[command]
        print(
            f"tourganize {command} is not implemented until {feature} ({summary}).",
            file=err,
        )
        return EXIT_NOT_IMPLEMENTED

    logger = configure_logging(settings, stream=err)
    logger.debug("settings resolved", extra={"kind": "startup", "profile": settings.env})

    if command == "doctor":
        return _doctor(settings, env, out=out)

    parser.print_help(err)  # pragma: no cover - argparse rejects unknown commands first
    return EXIT_NOT_IMPLEMENTED  # pragma: no cover


def _doctor(settings: Settings, env: Mapping[str, str], *, out: TextIO) -> int:
    container = build_container(settings)
    report = run_diagnostics(container, version=__version__, unrecognised=unrecognised_keys(env))
    print(report.render(), file=out)
    return EXIT_OK if report.ok else EXIT_DOCTOR_FAILED


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
