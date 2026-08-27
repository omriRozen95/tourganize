"""The ``tourganize`` command line.

Five commands work today: ``--version``, ``doctor``, ``catalog`` (``show``, ``validate``,
``gaps`` and ``agenda``), ``options`` (``search``) and ``chat`` — the first one that runs a whole
conversation. The rest of the surface is registered as stubs that name the feature which will
implement them, so the shape of the finished application is discoverable from the first release
and no later feature has to invent its own entry point.

Exit codes are part of the contract:

===  ==========================================================
0    success
1    ``doctor`` found a failing check, or a ``chat`` session
     ended in an error nobody expected
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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from tourganize import __version__
from tourganize.application import session_runner
from tourganize.application.composition import (
    Container,
    build_container,
    build_dialogue_settings,
    build_surface,
    read_script_file,
    run_on_surface,
    surface_transcript,
)
from tourganize.application.diagnostics import run_diagnostics
from tourganize.dialogue import DialogueDirector
from tourganize.domain.catalog import ComponentKind, PlanningAgenda, build_agenda
from tourganize.domain.errors import (
    IllegalTransitionError,
    UnknownComponentKindError,
    UnknownFieldError,
)
from tourganize.domain.options import Money, OptionSlate, PlanOption
from tourganize.domain.requirements import (
    GapReport,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
    analyse,
)
from tourganize.domain.trip import TripPlan
from tourganize.platform.errors import ConfigurationError, PortUnavailableError
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
#: The same code as ``EXIT_DOCTOR_FAILED``, and for the same reason the two names above
#: share theirs: 1 is "the command ran and the answer is bad news". A session that ended in
#: an exception nobody expected is that, and it is not a broken invocation (2) nor a broken
#: installation (3).
EXIT_SESSION_FAILED: Final = 1
EXIT_CONFIGURATION_ERROR: Final = 3

#: The ``catalog`` actions this release implements. F04 implemented the last one that was
#: awaiting a feature, so ``catalog`` has no stub actions left; the convention lives on in
#: :data:`PLANNED_COMMANDS`, and a later feature that plans a new ``catalog`` action follows it.
CATALOG_ACTIONS: Final = ("show", "validate", "gaps", "agenda")

#: The ``options`` actions this release implements. One, for now: the client's model is a short
#: slate and a refinement, so there is no ``more`` and no pagination to add later.
OPTIONS_ACTIONS: Final = ("search",)

#: Top-level sub-commands that later features implement:
#: name -> (feature, what that feature delivers). Each entry is deleted from this table by the
#: feature that implements the command.
PLANNED_COMMANDS: Final[Mapping[str, tuple[str, str]]] = {
    "resume": ("F12", "session persistence and resume"),
    "export": ("F13", "itinerary projection and rendering"),
    "docs": ("F18", "the Knowledge Corpus: add, list, query, index"),
}


@dataclass(frozen=True, slots=True)
class _AgendaArgument:
    """One ``catalog agenda`` flag: the help text it offers, and what it does to a Trip Plan.

    The two live together because they are the whole of what the flag *is*. Parsing is shared
    — every one of them is a comma-separated list of ``kind_key`` — so a fourth flag is one
    row of :data:`_AGENDA_ARGUMENTS` and the small function it names, and no edit to the
    parser, the dispatch or either function that builds the plan.
    """

    help_text: str
    apply: Callable[[TripPlan, Sequence[str]], None]


def _mention_each(plan: TripPlan, kind_keys: Sequence[str]) -> None:
    """Mention order becomes turn order: the first ``--mentioned`` Kind was raised on turn 0.

    That is what a conversation would have recorded, and what F05 reads.
    """
    for turn_index, kind_key in enumerate(kind_keys):
        plan.mark_mentioned(kind_key, turn_index)


def _select_each(plan: TripPlan, kind_keys: Sequence[str]) -> None:
    """Describe each Kind as already chosen.

    ``catalog agenda`` describes a plan rather than building one, so there is no slate here for
    a Selection to name — the aggregate's own ``mark_selected`` is what produces the state, and
    nothing out here walks a Component Status edge. Sourcing an actual slate is
    ``options search``, which is a different command about a different question.
    """
    for kind_key in kind_keys:
        plan.mark_selected(kind_key)


def _decline_each(plan: TripPlan, kind_keys: Sequence[str]) -> None:
    for kind_key in kind_keys:
        plan.decline(kind_key)


#: What ``catalog agenda`` accepts: one comma-separated list of ``kind_key`` per state a Plan
#: Component can be in before a conversation starts. Order matters and is this order: a mention
#: is a fact about a turn rather than a Component Status, and selecting before declining is what
#: makes naming one Kind in both an illegal transition rather than a silently accepted one.
_AGENDA_ARGUMENTS: Final[Mapping[str, _AgendaArgument]] = {
    "mentioned": _AgendaArgument(
        "Component Kinds the traveller raised, earliest first", _mention_each
    ),
    "selected": _AgendaArgument("Component Kinds already chosen", _select_each),
    "declined": _AgendaArgument("Component Kinds the traveller turned down", _decline_each),
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
    _add_options_parser(subcommands)
    _add_chat_parser(subcommands)

    for name, (feature, summary) in PLANNED_COMMANDS.items():
        stub = subcommands.add_parser(name, help=f"[{feature}] {summary}")
        stub.add_argument("rest", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)

    return parser


def _add_catalog_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``catalog`` and its four actions: ``show``, ``validate``, ``gaps`` and ``agenda``."""
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
    agenda = actions.add_parser("agenda", help="the Planning Agenda: what to plan next, and why")
    for name, argument in _AGENDA_ARGUMENTS.items():
        agenda.add_argument(f"--{name}", default="", metavar="K1,K2", help=argument.help_text)


def _add_options_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``options search`` — the first command that shows real option data."""
    options = subcommands.add_parser("options", help="source Plan Options for a Component Kind")
    actions = options.add_subparsers(dest="options_command", metavar="action")
    search = actions.add_parser("search", help="the Option Slate one Component Kind would be shown")
    search.add_argument("--kind", required=True, metavar="KIND_KEY", help="the Component Kind")
    search.add_argument(
        "--set",
        dest="values",
        default=None,
        metavar="JSON",
        help='requirement values already known, as a JSON object: \'{"place": "Paris"}\'',
    )


def _add_chat_parser(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """``chat`` — the whole conversation, on whichever Presentation Surface is selected."""
    chat = subcommands.add_parser("chat", help="plan a trip in conversation")
    chat.add_argument(
        "--locale",
        default=None,
        metavar="TAG",
        help="Locale Tag to talk in; must be one of TOURGANIZE_SUPPORTED_LOCALES",
    )
    chat.add_argument(
        "--script",
        default=None,
        type=Path,
        metavar="FILE",
        help="replay a transcript file, one turn per line, headlessly (implies the "
        "scripted surface); with TOURGANIZE_SURFACE=scripted and no --script the "
        "script is read from standard input",
    )
    chat.add_argument(
        "--debug-status",
        action="store_true",
        help="show the Dialogue State in the terminal status line",
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
    stdin: TextIO | None = None,
) -> int:
    """Run one CLI invocation and return its exit code."""
    out = stdout if stdout is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    src = stdin if stdin is not None else sys.stdin
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

    # A command that reads configuration may still find it broken — a catalog with a cycle in
    # it is exit 3 for the same reason a bad log level is, and `doctor` reports it as a failing
    # check instead of raising, so both answers stay honest.
    try:
        if command == "doctor":
            return _doctor(settings, env, out=out)
        if command == "catalog" and args.catalog_command == "gaps":
            return _catalog_gaps(
                build_container(settings), kind_key=args.kind, values=args.values, out=out, err=err
            )
        if command == "catalog" and args.catalog_command == "agenda":
            return _catalog_agenda(
                build_container(settings),
                supplied={name: str(getattr(args, name)) for name in _AGENDA_ARGUMENTS},
                out=out,
                err=err,
            )
        if command == "catalog":
            return _catalog(build_container(settings), args.catalog_command, out=out, err=err)
        if command == "chat":
            return _chat(
                settings,
                locale=args.locale,
                script_path=args.script,
                debug_status=args.debug_status,
                out=out,
                err=err,
                src=src,
            )
        if command == "options":
            return _options(
                build_container(settings),
                args.options_command,
                kind_key=getattr(args, "kind", None),
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


def _chat(
    settings: Settings,
    *,
    locale: str | None,
    script_path: Path | None,
    debug_status: bool,
    out: TextIO,
    err: TextIO,
    src: TextIO,
) -> int:
    """``chat`` — one whole planning conversation, from the greeting to the summary.

    The first command that wires everything: the Composition Root builds the ports, the
    Dialogue Director drives the conversation, the Presentation Surface shows it, and the
    Session Runner is the twenty lines where the two meet. Nothing about the dialogue is
    decided here — this function chooses a locale, finds the turns if they were scripted, and
    turns the outcome into an exit code.

    A session that ends in an exception is exit 1 **naming the session id**, because that id
    is what finds the turn-by-turn record in telemetry: an error message on its own says what
    broke, and the id says what the traveller had said by the time it did.
    """
    if locale is not None and locale not in settings.supported_locales:
        supported = ", ".join(settings.supported_locales)
        print(
            f"tourganize chat: --locale {locale} is not one of the Locale Tags "
            f"TOURGANIZE_SUPPORTED_LOCALES declares: {supported}",
            file=err,
        )
        return EXIT_USAGE_ERROR
    if script_path is not None and not script_path.is_file():
        # Exit 2 and one line: a transcript file that is not there is a mistyped argument,
        # and a traceback about it would say the same thing at ten times the length.
        print(f"tourganize chat: --script {script_path}: no such transcript file", file=err)
        return EXIT_USAGE_ERROR
    try:
        script = _script(settings, script_path, src)
    except OSError as exc:
        print(f"tourganize chat: --script {script_path}: {exc.strerror or exc}", file=err)
        return EXIT_USAGE_ERROR
    if script is None and settings.surface == "terminal" and not _is_terminal(src):
        # Refused rather than attempted. A terminal application started against a pipe does
        # not fail — it *waits*, for a keystroke that is never coming, and a test suite or a
        # CI job that hangs is a worse answer than any error message. The two ways out are
        # named because both are one flag away.
        print(
            "configuration error: TOURGANIZE_SURFACE=terminal needs a terminal, and stdin is "
            "not one. Pass --script FILE to replay a transcript, or set "
            "TOURGANIZE_SURFACE=scripted to read turns from stdin. In a container, "
            "`docker compose --profile dev-cpu run --rm app tourganize chat` allocates a TTY "
            "and `run -T` deliberately does not.",
            file=err,
        )
        return EXIT_CONFIGURATION_ERROR

    speaking = locale if locale is not None else settings.default_locale
    container = build_container(settings)
    director = DialogueDirector(
        container.component_catalog,
        container.priority_policy,
        container.turn_interpreter,
        container.option_slate_planner,
        container.clock,
        container.telemetry_sink,
        build_dialogue_settings(settings),
    )
    surface = build_surface(
        container,
        locale=speaking,
        script=script,
        session_id=director.session.session_id,
        debug_status=debug_status,
    )
    # Through ``run_on_surface`` rather than straight into the runner: a terminal interface
    # has to own the thread the process started on, and the session loop is what moves. A
    # headless surface runs the pump where it stands, so this line is the same line either way.
    outcome = run_on_surface(
        surface, lambda: session_runner.run(director, surface, locale=speaking)
    )
    # A headless surface has been talking to itself: what it recorded is the only output the
    # run has, and printing it is what makes `echo ... | tourganize chat` a usable command
    # rather than a silent exit code. A surface that drew on a terminal has nothing to add.
    transcript = surface_transcript(surface)
    if transcript:
        print(transcript, file=out)
    if outcome.error is not None:
        print(
            f"tourganize chat: session {outcome.session_id} ended after {outcome.turns} turn(s) "
            f"in {outcome.error}",
            file=err,
        )
        return EXIT_SESSION_FAILED
    return EXIT_OK


def _is_terminal(stream: TextIO) -> bool:
    """Whether ``stream`` is an interactive terminal.

    A stream that cannot answer the question is not one: a surface handed a stand-in stream by
    a test is being driven headlessly, which is exactly the case this guard exists for.
    """
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _script(settings: Settings, path: Path | None, src: TextIO) -> tuple[str, ...] | None:
    """The turns to replay, or ``None`` when a person is going to type them.

    ``--script FILE`` is read by the Scripted Surface's own rules, through the Composition
    Root, because ``tourganize.cli`` may not import an adapter. Standard input is read the
    same way and means the same thing, which is what lets ``echo ... | tourganize chat`` and
    CI — which has no terminal at all — drive the identical path a transcript file does.
    """
    if path is not None:
        return read_script_file(path)
    if settings.surface == "scripted":
        return _turns(src.read())
    return None


def _turns(text: str) -> tuple[str, ...]:
    """Read piped text as turns: one per line, without the blanks and the ``#`` comments.

    An empty line is not something anybody typed, and a script worth keeping has room for a
    note saying what it demonstrates.
    """
    lines = (line.strip() for line in text.splitlines())
    return tuple(line for line in lines if line and not line.startswith("#"))


def _catalog(container: Container, action: str | None, *, out: TextIO, err: TextIO) -> int:
    """``catalog show`` and ``validate``. Both load the catalog for real.

    ``gaps`` is dispatched by :func:`main`, which is where its two arguments exist; this
    function would otherwise have to reach for arguments three quarters of its callers never
    pass. The action is still checked here, before the file is read, so ``tourganize catalog``
    with no action says what it offers rather than reporting whatever is wrong with the
    catalog.
    """
    if action not in CATALOG_ACTIONS:
        print(f"tourganize catalog needs an action: {', '.join(CATALOG_ACTIONS)}", file=err)
        return EXIT_NOT_IMPLEMENTED

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


def _options(
    container: Container,
    action: str | None,
    *,
    kind_key: str | None,
    values: str | None,
    out: TextIO,
    err: TextIO,
) -> int:
    """``options search`` — source one Component Kind's first round and print the slate.

    Round zero, always: this command exists to show what a traveller would be shown when they
    first ask, and a refinement is a *conversation*, not a flag. What changes the slate is the
    ``--set`` values, which is exactly what changes it in a conversation too.
    """
    if action not in OPTIONS_ACTIONS:
        print(f"tourganize options needs an action: {', '.join(OPTIONS_ACTIONS)}", file=err)
        return EXIT_NOT_IMPLEMENTED
    if kind_key is None:  # pragma: no cover - argparse requires --kind on every action
        print("tourganize options search: --kind is required", file=err)
        return EXIT_USAGE_ERROR
    try:
        schema = container.component_catalog.schema_for(kind_key)
    except UnknownComponentKindError as exc:
        print(f"tourganize options search: {exc}", file=err)
        return EXIT_USAGE_ERROR
    try:
        requirements = _requirements_from(schema, values)
    except (UnknownFieldError, ValueError) as exc:
        print(f"tourganize options search: --set {exc}", file=err)
        return EXIT_USAGE_ERROR
    plan = TripPlan(plan_id="cli", created_at=container.clock.now())
    try:
        slate = container.option_slate_planner.plan(kind_key, requirements, plan, 0)
    except PortUnavailableError as exc:
        # Every source failed. In a conversation this is a `report_sourcing_failure` Act and the
        # next Agenda entry; on the command line there is no next entry, so it is exit 3 — a
        # broken installation, told apart from an empty slate, which is exit 0 and says so.
        print(f"tourganize options search: {exc}", file=err)
        return EXIT_CONFIGURATION_ERROR
    print(_render_slate(slate, schema), file=out)
    return EXIT_OK


def _render_slate(slate: OptionSlate, schema: RequirementSchema) -> str:
    """Render one Option Slate as a table: what it costs, what is known about it, where it came
    from, and which of the traveller's own filters it fails."""
    lines = [
        f"{slate.kind_key} (schema {schema.schema_key}, round {slate.round_index})",
        "",
        f"requirements_digest: {slate.requirements_digest}",
        f"diagnostics: {', '.join(slate.diagnostics) or 'none'}",
        "",
        f"options ({len(slate.options)}):",
    ]
    lines += _indented(
        _table(
            ("option_id", "price", "facts", "source", "fails"),
            [
                (
                    option.option_id,
                    _money(option.price),
                    _facts(option.facts),
                    _provenance(option),
                    ", ".join(option.filter_notes) or "-",
                )
                for option in slate.options
            ],
        )
    )
    return "\n".join(line.rstrip() for line in lines)


def _money(price: Money | None) -> str:
    """A price as ``74000 EUR``: minor units, because the domain holds no other kind of amount.

    Formatting an amount for a human — a symbol, a decimal separator, a currency's own number
    of minor digits — is the Presentation context's, and F10 is where a locale is known.
    """
    return "-" if price is None else f"{price.amount_minor} {price.currency}"


def _facts(facts: Mapping[str, object]) -> str:
    """The declared facts as ``key=value``, in the order the source declared them."""
    return " ".join(f"{name}={value}" for name, value in facts.items()) or "-"


def _provenance(option: PlanOption) -> str:
    """Where this option came from: the source, and the source's own reference for it."""
    reference = option.provenance.external_ref
    source_id = option.provenance.source_id
    return source_id if reference is None else f"{source_id} ({reference})"


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

    No ``raw_text``: that field holds the traveller's *own words*, and a JSON fragment typed at
    a shell prompt is not them. Re-serialising the value into it would give F05 something to
    quote back that nobody ever said.
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
        RequirementUpdate(field_name=str(name), value=value) for name, value in supplied.items()
    ]
    return empty.with_updates(updates, schema=schema)


def _catalog_agenda(
    container: Container,
    *,
    supplied: Mapping[str, str],
    out: TextIO,
    err: TextIO,
) -> int:
    """``catalog agenda`` — the Planning Agenda of a plan described on the command line.

    The closest thing to watching the dialogue decide what to do next before the dialogue
    exists: name what the traveller raised, what is already chosen and what they turned down,
    and the bands, ranks, reason codes and the entry that would be worked on now are printed.

    ``supplied`` is the raw value of every flag :data:`_AGENDA_ARGUMENTS` declares, keyed by
    its name — one argument rather than one per flag, so a fourth flag does not reach this
    signature at all.
    """
    catalog = container.component_catalog
    try:
        plan = _plan_from(container, supplied)
    except (UnknownComponentKindError, IllegalTransitionError) as exc:
        # An unknown or disabled Kind, or arguments that contradict each other — the same Kind
        # both selected and declined. Both are the invocation's fault, not the catalog's.
        print(f"tourganize catalog agenda: {exc}", file=err)
        return EXIT_USAGE_ERROR
    agenda = build_agenda(
        plan,
        catalog.kinds(),
        container.priority_policy,
        plannable=_plannability(catalog),
        failure_skip=container.settings.agenda_failure_skip,
    )
    print(
        _render_agenda(
            agenda,
            container.priority_policy.policy_id,
            origin=str(container.settings.catalog_path),
        ),
        file=out,
    )
    return EXIT_OK


def _kind_keys(supplied: str) -> tuple[str, ...]:
    """Read ``--mentioned k1,k2`` as Component Kinds, in the order they were given."""
    return tuple(key.strip() for key in supplied.split(",") if key.strip())


def _plan_from(container: Container, supplied: Mapping[str, str]) -> TripPlan:
    """Build the Trip Plan the arguments describe, refusing a Kind the catalog does not declare.

    Every named Kind is checked before anything is applied, so a typo in the last flag does not
    leave half a plan behind. Each flag is then applied by the function its row of
    :data:`_AGENDA_ARGUMENTS` names, in that table's order.
    """
    catalog = container.component_catalog
    parsed = {name: _kind_keys(value) for name, value in supplied.items()}
    for kind_keys in parsed.values():
        for kind_key in kind_keys:
            catalog.kind(kind_key)  # raises for an unknown or disabled Kind, naming the declared
    plan = TripPlan(plan_id="cli", created_at=container.clock.now())
    for name, kind_keys in parsed.items():
        _AGENDA_ARGUMENTS[name].apply(plan, kind_keys)
    return plan


def _plannability(catalog: ComponentCatalog) -> dict[str, bool]:
    """Which Component Kinds could be sourced right now, knowing nothing about the traveller.

    Nothing has been said, so every Kind whose Requirement Schema has a Blocking Rule answers
    ``false`` — which is the honest answer, and the reason the reason code is ``not_plannable``
    rather than ``ready``: the dialogue would elicit, not source.
    """
    return {
        kind.kind_key: analyse(
            catalog.schema_for(kind.kind_key),
            RequirementSet.empty(kind.kind_key),
        ).is_plannable
        for kind in catalog.enabled_kinds()
    }


def _render_agenda(agenda: PlanningAgenda, policy_id: str, *, origin: str) -> str:
    """Render the Agenda as one table, plus the two lines the dialogue actually reads."""
    lines = [f"{origin} (policy {policy_id})", ""]
    lines += _table(
        ("kind_key", "band", "rank", "awaits", "reason"),
        [
            (
                entry.kind_key,
                entry.band.name,
                str(entry.rank),
                ", ".join(entry.blocked_by) or "-",
                entry.reason_code,
            )
            for entry in agenda.entries
        ],
    )
    actionable = agenda.next_actionable()
    lines += [
        "",
        f"next_actionable: {actionable.kind_key if actionable is not None else 'none'}",
        f"mentioned_band_empty: {'true' if agenda.is_mentioned_band_empty() else 'false'}",
    ]
    return "\n".join(line.rstrip() for line in lines)


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
            ("rule", "satisfied by", "questions"),
            [
                (
                    gap.rule_name,
                    "  |  ".join(" + ".join(group) for group in gap.field_names),
                    ", ".join(gap.prompt_message_keys),
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
            ("field", "blocks", "reason", "detail"),
            [
                (
                    bad.field_name,
                    "yes" if bad.blocks else "no",
                    bad.reason_message_key,
                    bad.detail,
                )
                for bad in report.invalid
            ],
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
