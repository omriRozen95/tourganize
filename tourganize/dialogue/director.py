"""The Dialogue Director: the state machine that turns the inert planning domain into a
conversation.

Everything the client asked for behaviourally happens in this one class, and it is written so
that each of their rules is a named method rather than a branch inside a long one:

* :meth:`DialogueDirector._work_on` resolves blocking gaps **before** anything is sourced, one
  question per Act.
* :meth:`DialogueDirector._source` presents an Option Slate and bundles the optional filters
  alongside the *first* one, never after.
* :meth:`DialogueDirector._choose` and the ``REFINE`` branch are the Choose-or-Refine Loop, with
  no bound on the rounds and no slate ever discarded.
* :meth:`DialogueDirector._offer_or_close` makes a Proactive Offer only once the mentioned band
  of the Agenda has emptied, and closes the session when there is nothing left to offer.

Two properties are worth stating because everything else rests on them.

**No I/O.** The Director reads a ``Clock``, writes to a ``TelemetrySink``, asks a
``ComponentCatalog`` for schemas and calls two injected callables — a ``TurnInterpreter`` and an
``OptionSlatePlanner``. There is no network, no file, no model and no terminal anywhere in this
module, which is why the whole of F05's Definition of Done is unit-testable with fakes.

**No wording.** Every Act payload holds message keys, field names, ``kind_key``s, opaque codes
and structured option data. If a sentence ever appears in this file, the bilingual requirement
has been quietly lost: phrasing is F10's Message Catalogue and F08's Composition calls.

The one thing this module reaches outside itself for is ``logging``, which is standard library
and therefore inside the pure packages' import rule. It is used for the failures that must be
visible to an operator but must not reach the traveller: an interpreter that raised, a planner
that raised, an interpretation naming a field or a Component Kind nobody declared.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final

from tourganize.dialogue.ports import DialogueContext, OptionSlatePlanner, TurnInterpreter
from tourganize.dialogue.session import PendingQuestion, PlanningSession, new_session
from tourganize.dialogue.settings import DialogueSettings
from tourganize.dialogue.states import DialogueState, require_transition
from tourganize.dialogue.turns import (
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLARIFY,
    CLARIFY_INTERPRETER_FAILED,
    CLARIFY_NOT_UNDERSTOOD,
    CLARIFY_STILL_MISSING,
    CLARIFY_UNDECLARED_FIELD,
    CLARIFY_UNRESOLVED_CHOICE,
    CLOSE,
    CONFIRM_SELECTION,
    DEFAULT_LOCALE,
    DELIVER_SUMMARY,
    GREET,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_INVALID_VALUE,
    REPORT_SOURCING_FAILURE,
    SOURCING_FAILED,
    AssistantAct,
    TurnIntent,
    TurnInterpretation,
    UserTurn,
)
from tourganize.domain.catalog import AgendaBand, AgendaEntry, PlanningAgenda, build_agenda
from tourganize.domain.errors import (
    ContractViolationError,
    InvariantViolationError,
    SessionClosedError,
    UnknownFieldError,
)
from tourganize.domain.invariants import require_text
from tourganize.domain.options import OptionSlate, PlanOption
from tourganize.domain.requirements import (
    BlockingGap,
    CandidateGroup,
    GapReport,
    InvalidValue,
    RequirementSchema,
    RequirementSet,
    RequirementUpdate,
    analyse,
)
from tourganize.domain.trip import ComponentStatus, PlanComponent, Selection
from tourganize.ports.catalog import ComponentCatalog, PriorityPolicy
from tourganize.ports.platform import Clock, TelemetryEvent, TelemetrySink

__all__ = ["TURN_EVENT_KIND", "DialogueDirector"]

#: The ``kind`` of the one Telemetry Event every ``handle()`` records — the Turn Ledger entry.
#: F08 enriches its ``fields`` with model tokens and cost; it does not add a second mechanism.
TURN_EVENT_KIND: Final = "turn_ledger"

_LOGGER: Final = logging.getLogger(__name__)

#: The Component Statuses from which a component must first be walked to ``READY`` before it
#: can be sourced. Spelled out rather than inferred, because ``advance_to`` is what says which
#: edges exist and this is only the shortest legal route to ``SOURCING``.
_NEEDS_READY: Final = frozenset({ComponentStatus.PENDING, ComponentStatus.ELICITING})


@dataclass(frozen=True, slots=True)
class _AgendaStep:
    """What one step down the Planning Agenda produced, and whether the session may stop there.

    ``at_rest`` is true when the step said something the traveller is expected to answer, and
    false when this Component Kind could not be progressed at all — then the next Agenda entry
    gets its chance in the same turn. It used to travel as the second half of a bare
    ``tuple[list[AssistantAct], bool]``, which read as ``acts, True`` at the call sites and
    said nothing about which half meant what.
    """

    acts: tuple[AssistantAct, ...] = ()
    at_rest: bool = False


@dataclass(frozen=True, slots=True)
class _Obligation:
    """One unsatisfied blocking obligation, and everything there is to say about it.

    Built either from a **Blocking Gap** — nothing satisfies the rule — or from an **Invalid
    Value** — what would satisfy it cannot be used. Both are attempts on the *same* obligation,
    which is why they share one Pending Question and one re-ask count: F05's Scope calls
    ``report_invalid_value`` "a re-ask, not a rejection of the turn", and a re-ask nobody
    counted is a loop with no exit.

    ``payload`` is the Act's payload without ``attempt``, which :meth:`DialogueDirector._elicit`
    adds because only it knows the count. ``escalation`` is the ``clarify`` payload used once
    the count passes ``max_reasks`` and the example is offered instead.
    """

    rule_name: str
    field_names: tuple[str, ...]
    act: str
    payload: Mapping[str, object]
    escalation: Mapping[str, object]


class DialogueDirector:
    """The one entry point into the dialogue: :meth:`handle` per turn, and nothing else.

    Construction wires the ports and starts an empty Planning Session, so
    :attr:`session` is readable before the first turn — a surface needs the session id to label
    its output, and a test needs somewhere to look.
    """

    def __init__(
        self,
        catalog: ComponentCatalog,
        policy: PriorityPolicy,
        interpreter: TurnInterpreter,
        planner: OptionSlatePlanner,
        clock: Clock,
        telemetry: TelemetrySink,
        settings: DialogueSettings,
        *,
        session_id: str | None = None,
    ) -> None:
        self._catalog = catalog
        self._policy = policy
        self._interpreter = interpreter
        self._planner = planner
        self._clock = clock
        self._telemetry = telemetry
        self._settings = settings
        self._session = new_session(
            session_id if session_id is not None else uuid.uuid4().hex,
            clock.now(),
        )
        # Whether the *previous* turn's interpretation raised. One failure is confusion and
        # becomes a `clarify`; two in a row is a broken interpreter and propagates.
        self._interpreter_failed = False

    @property
    def session(self) -> PlanningSession:
        """The Planning Session this Director is driving. Mutated only from in here."""
        return self._session

    # -- the two entry points ---------------------------------------------------------------

    def begin(self, locale: str = DEFAULT_LOCALE) -> tuple[AssistantAct, ...]:
        """Open the conversation in ``locale`` and emit ``greet``.

        Separate from construction because greeting is something the assistant *does*, and a
        Director that greeted while being wired could not be built by ``doctor``.
        """
        require_text(locale, "begin(locale)")
        if self._session.transcript or self._session.state is not DialogueState.GREETING:
            raise InvariantViolationError(
                f"this session has already begun; it is {self._session.state.name} with "
                f"{len(self._session.transcript)} exchange(s) recorded"
            )
        self._session.locale = locale
        acts = (self._act(GREET),)
        self._session.record(None, acts)
        return acts

    def handle(self, turn: UserTurn) -> tuple[AssistantAct, ...]:
        """Read one turn, mutate the session, and return the Assistant Acts it produced."""
        session = self._session
        if session.is_closed:
            raise SessionClosedError(
                f"session {session.session_id} closed on turn {session.turn_index}; "
                f"reopening it is `resume` (F12), not another turn"
            )
        if type(turn) is not UserTurn:
            raise InvariantViolationError(f"handle expects a UserTurn, got {turn!r}")
        if turn.index <= session.turn_index:
            raise InvariantViolationError(
                f"turns arrive in order: turn {turn.index} follows turn {session.turn_index}; "
                f"a surface asks the session for `next_turn_index`"
            )

        started_at = self._clock.now()
        before = session.state
        session.turn_index = turn.index
        self._transition(DialogueState.INTERPRETING)

        interpretation = self._interpret(turn, before)
        acts = tuple(self._respond(turn, interpretation, before))

        session.record(turn, acts)
        self._record_turn(turn, interpretation, acts, before=before, started_at=started_at)
        return acts

    # -- reading the turn -------------------------------------------------------------------

    def _interpret(self, turn: UserTurn, resting_state: DialogueState) -> TurnInterpretation | None:
        """Ask the interpreter to read ``turn``. ``None`` means it raised, once.

        The second consecutive failure propagates: an interpreter that cannot read two turns in
        a row is broken, and swallowing that would leave a conversation asking for
        clarification for ever.
        """
        context = self._context(resting_state)
        try:
            interpretation = self._interpreter.interpret(turn, context)
        except Exception as exc:
            if self._interpreter_failed:
                raise
            self._interpreter_failed = True
            _LOGGER.warning(
                "the Turn Interpreter raised on turn %s (%s); asking for clarification",
                turn.index,
                type(exc).__name__,
                extra={"kind": "dialogue"},
                exc_info=exc,
            )
            return None
        if type(interpretation) is not TurnInterpretation:
            raise ContractViolationError(
                f"TurnInterpreter {type(self._interpreter).__name__!r} must return a "
                f"TurnInterpretation, got {interpretation!r}"
            )
        self._interpreter_failed = False
        return interpretation

    def _context(self, resting_state: DialogueState) -> DialogueContext:
        """Everything the interpreter is allowed to know. No session object leaks out.

        ``resting_state`` is the state the conversation was *in* when the turn arrived, not the
        transient ``INTERPRETING`` it is in while being read. That distinction is the whole
        value of the context: "a slate is on the table" is what tells an interpreter to read
        ``2`` as a choice rather than as a number, and ``INTERPRETING`` would tell it nothing.
        """
        session = self._session
        focus = session.focus_kind
        return DialogueContext(
            state=resting_state,
            locale=session.locale,
            focus_kind=focus,
            pending_question=session.pending_question,
            slate_option_refs=self._slate_option_refs(focus),
            known_kind_keys=tuple(kind.kind_key for kind in self._catalog.enabled_kinds()),
            focus_field_names=self._focus_field_names(focus),
            turn_index=session.turn_index,
        )

    def _slate_option_refs(self, kind_key: str | None) -> tuple[str, ...]:
        slate = self._latest_slate(kind_key)
        return () if slate is None else tuple(option.option_id for option in slate.options)

    def _latest_slate(self, kind_key: str | None) -> OptionSlate | None:
        if kind_key is None or not self._session.plan.has_component(kind_key):
            return None
        return self._session.plan.component(kind_key).latest_slate()

    def _focus_field_names(self, kind_key: str | None) -> tuple[str, ...]:
        if kind_key is None:
            return ()
        return self._catalog.schema_for(kind_key).field_names

    # -- dispatch ---------------------------------------------------------------------------

    def _respond(
        self,
        turn: UserTurn,
        interpretation: TurnInterpretation | None,
        before: DialogueState,
    ) -> list[AssistantAct]:
        """Turn one interpretation into Acts. The whole of the control flow starts here."""
        if interpretation is None:
            return self._rest_at(before, [self._clarify(CLARIFY_INTERPRETER_FAILED)])

        if interpretation.detected_locale is not None:
            self._session.locale = interpretation.detected_locale
        self._note_mentions(interpretation, turn.index)

        intent = interpretation.intent
        if intent is TurnIntent.END_SESSION:
            return self._summarise_and_close()
        if intent is TurnIntent.STATE_REQUEST:
            return self._rest_at(before, [self._summary_act()])
        if intent is TurnIntent.ACCEPT_OFFER:
            return self._answer_offer(interpretation, turn, accepted=True, before=before)
        if intent is TurnIntent.DECLINE_OFFER:
            return self._answer_offer(interpretation, turn, accepted=False, before=before)
        if intent is TurnIntent.CHOOSE_OPTION:
            return self._choose(interpretation, turn, before)
        if intent is TurnIntent.SMALL_TALK:
            # Nothing was asked for. Outside the greeting there is no Act in the vocabulary
            # that means "acknowledged", and inventing one would be inventing wording, so the
            # session simply stays where it was and `handle` returns an empty tuple. In the
            # greeting, carrying on means starting.
            if before is DialogueState.GREETING:
                return self._plan_next(before)
            return self._rest_at(before, [])
        if intent is TurnIntent.REFINE and before is DialogueState.AWAITING_CHOICE:
            return self._refine(interpretation, turn)
        if interpretation.requirement_updates or interpretation.mentioned_kinds:
            return self._absorb(interpretation, turn, before)
        return self._rest_at(before, [self._clarify(CLARIFY_NOT_UNDERSTOOD)])

    def _note_mentions(self, interpretation: TurnInterpretation, turn_index: int) -> None:
        """Record every Component Kind this turn raised, whatever else the turn does.

        Mentions are recorded even while a slate is on the table: the Agenda has to know what
        the traveller has asked for, and the *current* component is still finished first —
        Mentioned-First is a rule about the Agenda, not a licence to interrupt a slate.
        """
        for kind_key in interpretation.mentioned_kinds:
            if not self._declares(kind_key):
                _LOGGER.warning(
                    "the Turn Interpreter mentioned Component Kind %r, which the catalog does "
                    "not declare or has disabled; ignoring it",
                    kind_key,
                    extra={"kind": "dialogue"},
                )
                continue
            self._session.plan.mark_mentioned(kind_key, turn_index)
            self._reopen_if_declined(kind_key)

    def _reopen_if_declined(self, kind_key: str) -> None:
        """A declined Component Kind the traveller raises themselves is planned after all.

        Declining answers an *offer*; it is not a prohibition (D18). The client's rule — a
        declined Kind is never offered again in that session — still holds, and it holds
        structurally rather than by a second rule of its own: the only way back out of
        ``DECLINED`` is a mention, a mentioned Kind sits in the Agenda's *mentioned* band, and
        a Proactive Offer is drawn from the unmentioned band alone.
        """
        component = self._session.plan.component(kind_key)
        if component.status is ComponentStatus.DECLINED:
            component.advance_to(ComponentStatus.ELICITING)

    def _declares(self, kind_key: str) -> bool:
        return any(kind.kind_key == kind_key for kind in self._catalog.enabled_kinds())

    # -- absorbing values ------------------------------------------------------------------

    def _absorb(
        self, interpretation: TurnInterpretation, turn: UserTurn, before: DialogueState
    ) -> list[AssistantAct]:
        """Merge whatever the turn supplied, then work down the Agenda."""
        target = self._merge_target(interpretation, before)
        if target is None:
            return self._rest_at(before, [self._clarify(CLARIFY_NOT_UNDERSTOOD)])
        refused = self._merge_or_clarify(target, interpretation, turn)
        if refused is not None:
            return self._rest_at(before, [refused])
        return self._plan_next(before)

    def _merge_target(
        self, interpretation: TurnInterpretation, before: DialogueState
    ) -> str | None:
        """Which Plan Component this turn's values belong to.

        The component in focus wins while a slate is on the table, because a turn arriving then
        is about *that* slate even when it also raises something new. Otherwise the Kind the
        turn itself raised wins, and failing that the Agenda's own answer to "what next".
        """
        session = self._session
        if before is DialogueState.AWAITING_CHOICE and session.focus_kind is not None:
            return session.focus_kind
        for kind_key in interpretation.mentioned_kinds:
            if self._declares(kind_key) and not self._is_settled(kind_key):
                return kind_key
        if session.focus_kind is not None and not self._is_settled(session.focus_kind):
            return session.focus_kind
        # The *mentioned* band only. Filing a traveller's words against a Component Kind they
        # never raised would put requirements on something nobody asked to plan, and the Agenda
        # would then rank it first for having them.
        entry = self._next_mentioned_entry(set())
        return None if entry is None else entry.kind_key

    def _is_settled(self, kind_key: str) -> bool:
        plan = self._session.plan
        return plan.has_component(kind_key) and plan.component(kind_key).is_settled

    def _merge_or_clarify(
        self, kind_key: str, interpretation: TurnInterpretation, turn: UserTurn
    ) -> AssistantAct | None:
        """Merge this turn's values into ``kind_key``. An Act back means the merge was refused.

        The same three lines stood in :meth:`_absorb` and in the refinement branch, and the
        only thing that differed between them was which Resting State the session then went
        back to — which is the caller's business, not the merge's.
        """
        if not interpretation.requirement_updates:
            return None
        undeclared = self._merge(kind_key, interpretation.requirement_updates, turn.index)
        if undeclared is None:
            return None
        return self._clarify(CLARIFY_UNDECLARED_FIELD, {"field_name": undeclared}, kind_key)

    def _merge(
        self, kind_key: str, updates: Sequence[RequirementUpdate], turn_index: int
    ) -> str | None:
        """Merge ``updates`` into ``kind_key``'s Requirement Set. Returns an undeclared field.

        The turn index is stamped here rather than trusted from the interpreter: the merge
        precedence turns on it — "later turn wins" — and the Director is the only thing that
        knows which turn this is. An interpreter that filled it in is overruled rather than
        second-guessed, so a refinement can never lose to the value it was correcting.

        A field the schema does not declare is *reported*, never ignored: it almost always
        means an extraction prompt and a schema have drifted apart, and the whole batch is left
        unmerged so that the traveller is asked again rather than told half of it landed.
        """
        schema = self._catalog.schema_for(kind_key)
        component = self._session.plan.ensure_component(kind_key)
        stamped = tuple(replace(update, turn_index=turn_index) for update in updates)
        try:
            component.requirements = _held(component).with_updates(stamped, schema=schema)
        except UnknownFieldError as exc:
            # The field name comes off the exception rather than being re-derived by walking
            # the batch again: the raiser knew it, and a re-derivation that missed would put
            # an empty `field_name` into a `clarify` payload for a surface to render.
            _LOGGER.warning(
                "the Turn Interpreter offered a value for %r, which Requirement Schema %s does "
                "not declare: %s",
                exc.field_name,
                schema.schema_key,
                exc,
                extra={"kind": "dialogue"},
            )
            return exc.field_name
        return None

    # -- working down the Agenda ------------------------------------------------------------

    def _plan_next(
        self, resting: DialogueState, attempted: set[str] | None = None
    ) -> list[AssistantAct]:
        """Work down the Planning Agenda until the session comes to rest.

        ``attempted`` is the set of Component Kinds this turn has already tried. It is what
        makes the loop terminate: a Kind whose sourcing failed is stepped over for the rest of
        the turn, and the next Agenda entry gets its chance in the same breath — "the
        conversation never dies because a provider did".

        ``resting`` is where the turn arrived from, and it is where the session goes back to if
        the whole walk finds nothing to say. Carrying it this far down is the price of not
        inventing a Dialogue State that claims something the turn did not do.
        """
        tried = set() if attempted is None else set(attempted)
        acts: list[AssistantAct] = []
        while True:
            entry = self._next_mentioned_entry(tried)
            if entry is None:
                return acts + self._offer_or_close(resting)
            tried.add(entry.kind_key)
            step = self._work_on(entry.kind_key)
            acts += step.acts
            if step.at_rest:
                return acts

    def _next_mentioned_entry(self, attempted: set[str]) -> AgendaEntry | None:
        """The next actionable Agenda entry the traveller actually raised.

        Only the mentioned band: a Component Kind nobody asked for is *offered*, never planned
        behind their back, which is the whole point of the Proactive Offer.
        """
        return next(
            (
                entry
                for entry in self._agenda().entries
                if entry.band is AgendaBand.MENTIONED
                and entry.is_actionable
                and entry.kind_key not in attempted
            ),
            None,
        )

    def _work_on(self, kind_key: str) -> _AgendaStep:
        """Elicit or source one Plan Component."""
        session = self._session
        session.focus_kind = kind_key
        schema = self._catalog.schema_for(kind_key)
        component = session.plan.ensure_component(kind_key)
        held = _held(component)
        report = analyse(schema, held)

        # A value that is present but unusable is told about, not asked for again: the
        # traveller has already answered this, and re-asking would say we were not listening.
        # It is still an attempt on the same obligation, and it is counted as one.
        if report.blocking_invalid:
            invalid = report.blocking_invalid[0]
            return self._elicit(component, _invalid_obligation(invalid, schema))
        gap = report.next_blocking()
        if gap is not None:
            return self._elicit(component, _missing_obligation(gap, schema))
        return self._source(component, held, report)

    def _elicit(self, component: PlanComponent, obligation: _Obligation) -> _AgendaStep:
        """Say the one thing outstanding about a blocking obligation — or stop saying it.

        One question per Act, always, and **every** way of coming back to an obligation counts
        as an attempt: an ``ask_blocking`` and a ``report_invalid_value`` are two ways of
        saying the same obligation is unmet. After ``max_reasks`` attempts the field's example
        is offered instead, and if that does not help either the component is marked ``FAILED``
        and the Agenda moves on: a conversation that says the same thing for ever is worse than
        one that admits it cannot plan this component.

        The Pending Question is deliberately **kept** when the Director gives up. Clearing it
        would make the next turn's attempt look like the first one, and the component could
        re-enter the ask/clarify/fail cycle indefinitely. It is cleared in exactly one place —
        :meth:`_source`, once a slate has actually arrived.
        """
        session = self._session
        kind_key = component.kind_key
        standing = session.pending_question
        if standing is not None and standing.is_about(kind_key, obligation.rule_name):
            pending = standing.asked_again(session.turn_index)
        else:
            pending = PendingQuestion(
                kind_key=kind_key,
                rule_name=obligation.rule_name,
                field_names=obligation.field_names,
                asked_on_turn=session.turn_index,
            )
        session.pending_question = pending

        if pending.attempts > self._settings.max_reasks + 1:
            self._give_up_on(component, obligation.rule_name, pending.attempts)
            return _AgendaStep()

        self._to_eliciting(component)
        self._transition(DialogueState.ELICITING_BLOCKING)
        if pending.attempts > self._settings.max_reasks:
            act = self._clarify(CLARIFY_STILL_MISSING, obligation.escalation, kind_key)
        else:
            payload = {**obligation.payload, "attempt": pending.attempts}
            act = self._act(obligation.act, payload, kind_key)
        return _AgendaStep((act,), at_rest=True)

    def _give_up_on(self, component: PlanComponent, rule_name: str, attempts: int) -> None:
        """Stop coming back to ``rule_name`` and mark the component ``FAILED``.

        Routed through ``ELICITING`` so that a *second* give-up is a legal move — ``FAILED``
        has no edge to itself — and so each one is counted. That count is what bounds the whole
        thing: once the run reaches ``TOURGANIZE_AGENDA_FAILURE_SKIP``, F04's Agenda steps this
        Component Kind over for good and it is never worked on again.
        """
        self._to_eliciting(component)
        component.advance_to(ComponentStatus.FAILED)
        _LOGGER.warning(
            "giving up on %s: Blocking Rule %r was raised %s times (%s failure(s) in a row)",
            component.kind_key,
            rule_name,
            attempts - 1,
            component.consecutive_failures,
            extra={"kind": "dialogue"},
        )

    def _source(
        self, component: PlanComponent, held: RequirementSet, report: GapReport
    ) -> _AgendaStep:
        """Ask the planner for the next round, and present it.

        The optional filters ride along with round **zero** and no other, which is the whole of
        "asked at most once, and never blocking": a refinement never re-asks them, and a
        traveller who ignores them is never nagged.
        """
        session = self._session
        kind_key = component.kind_key
        round_index = component.round_count
        session.pending_question = None
        self._transition(DialogueState.SOURCING)
        self._to_sourcing(component)
        try:
            slate = self._planner.plan(kind_key, held, session.plan, round_index)
        except Exception as exc:
            return self._sourcing_failed(component, round_index, exc)
        self._require_slate(slate, kind_key, round_index)
        if not slate.options:
            return self._sourcing_failed(component, round_index, None)

        session.plan.record_slate(slate)
        self._transition(DialogueState.PRESENTING_SLATE)
        acts = [
            self._act(
                PRESENT_SLATE,
                {
                    "round_index": slate.round_index,
                    "option_ids": tuple(option.option_id for option in slate.options),
                    "options": tuple(_option_payload(option) for option in slate.options),
                    "requirements_digest": slate.requirements_digest,
                },
                kind_key,
            )
        ]
        optional = report.optional[: self._settings.optional_ask_limit] if round_index == 0 else ()
        if optional:
            self._transition(DialogueState.ELICITING_OPTIONAL)
            acts.append(
                self._act(
                    ASK_OPTIONAL,
                    {
                        "field_names": tuple(spec.name for spec in optional),
                        "prompt_message_keys": tuple(spec.prompt_message_key for spec in optional),
                    },
                    kind_key,
                )
            )
        self._transition(DialogueState.AWAITING_CHOICE)
        return _AgendaStep(tuple(acts), at_rest=True)

    def _require_slate(self, slate: object, kind_key: str, round_index: int) -> None:
        """Check the planner's answer at the seam. It is replaceable, so it is not trusted."""
        if type(slate) is not OptionSlate:
            raise ContractViolationError(
                f"OptionSlatePlanner {type(self._planner).__name__!r} must return an "
                f"OptionSlate, got {slate!r}"
            )
        if slate.kind_key != kind_key or slate.round_index != round_index:
            raise ContractViolationError(
                f"OptionSlatePlanner {type(self._planner).__name__!r} was asked for "
                f"{kind_key!r} round {round_index} and answered with {slate.kind_key!r} "
                f"round {slate.round_index}"
            )

    def _sourcing_failed(
        self, component: PlanComponent, round_index: int, exc: Exception | None
    ) -> _AgendaStep:
        """Record one sourcing failure and say so, without the exception's English.

        The Act carries an opaque code; the reason goes to the log, where an operator reads it.

        ``FAILED`` is where F02 counts the run of failures, so every failure passes through it —
        but a component is only *left* there once the run reaches
        ``TOURGANIZE_AGENDA_FAILURE_SKIP``, which is what F05 asks for: "marks the component
        ``FAILED`` after the configured attempts". One bad answer from a provider is not a
        failed component. Below the threshold it goes back to ``ELICITING``, which keeps the
        count — only a slate or a Selection clears it — and keeps the Kind on the Agenda for the
        next turn, so one broken provider still cannot deadlock a session.
        """
        component.advance_to(ComponentStatus.FAILED)
        stalled = component.consecutive_failures >= self._settings.failure_skip
        if not stalled:
            component.advance_to(ComponentStatus.ELICITING)
        _LOGGER.warning(
            "sourcing %s round %s failed (%s in a row, %s): %s",
            component.kind_key,
            round_index,
            component.consecutive_failures,
            "stepping the kind over" if stalled else "it will be retried",
            "the slate came back empty" if exc is None else f"{type(exc).__name__}: {exc}",
            extra={"kind": "dialogue"},
        )
        return _AgendaStep(
            (
                self._act(
                    REPORT_SOURCING_FAILURE,
                    {
                        "reason_code": SOURCING_FAILED,
                        "round_index": round_index,
                        "consecutive_failures": component.consecutive_failures,
                    },
                    component.kind_key,
                ),
            )
        )

    # -- the choose-or-refine loop ----------------------------------------------------------

    def _choose(
        self, interpretation: TurnInterpretation, turn: UserTurn, before: DialogueState
    ) -> list[AssistantAct]:
        """Record a Selection and move on — or ask again, if the reference resolved to nothing."""
        session = self._session
        kind_key = session.focus_kind
        slate = self._latest_slate(kind_key)
        if before is not DialogueState.AWAITING_CHOICE or kind_key is None or slate is None:
            return self._rest_at(before, [self._clarify(CLARIFY_NOT_UNDERSTOOD)])
        option = _resolve_choice(slate, interpretation.chosen_option_ref)
        if option is None:
            return self._rest_at(
                before,
                [
                    self._clarify(
                        CLARIFY_UNRESOLVED_CHOICE,
                        {
                            "given": interpretation.chosen_option_ref or "",
                            "option_ids": tuple(item.option_id for item in slate.options),
                        },
                        kind_key,
                    )
                ],
            )
        session.plan.record_selection(
            Selection(kind_key=kind_key, option=option, chosen_at_turn=turn.index)
        )
        session.pending_question = None
        noted = tuple(
            other
            for other in interpretation.mentioned_kinds
            if other != kind_key and self._declares(other)
        )
        acts = [
            self._act(
                CONFIRM_SELECTION,
                {
                    "option_id": option.option_id,
                    "round_index": slate.round_index,
                    "noted_kinds": noted,
                },
                kind_key,
            )
        ]
        return acts + self._plan_next(before)

    def _refine(self, interpretation: TurnInterpretation, turn: UserTurn) -> list[AssistantAct]:
        """Re-source the **same** component with the next round index.

        Unbounded: the only bookkeeping a refinement does is increment the round, and every
        round stays in the Plan Component's history. A refinement that *invalidates* a value
        sends the machine back to eliciting instead, which is why this goes through
        ``_work_on`` rather than straight to the planner.
        """
        resting = DialogueState.AWAITING_CHOICE
        kind_key = self._session.focus_kind
        if kind_key is None:  # pragma: no cover - AWAITING_CHOICE always has a focus
            return self._rest_at(resting, [self._clarify(CLARIFY_NOT_UNDERSTOOD)])
        refused = self._merge_or_clarify(kind_key, interpretation, turn)
        if refused is not None:
            return self._rest_at(resting, [refused])
        self._transition(DialogueState.REFINING)
        step = self._work_on(kind_key)
        if step.at_rest:
            return list(step.acts)
        return list(step.acts) + self._plan_next(resting, {kind_key})

    # -- proactive offers and closing -------------------------------------------------------

    def _offer_or_close(self, resting: DialogueState) -> list[AssistantAct]:
        """Offer the Component Kinds nobody raised, or summarise and close.

        The gate is the Agenda's own: offers begin only once the mentioned band has emptied, so
        a traveller is never asked about a car while the hotel they came for is unresolved. A
        mentioned Kind that is still open but could not be worked on this turn leaves the
        session listening rather than closing it.
        """
        agenda = self._agenda()
        if not agenda.is_mentioned_band_empty():
            # Something the traveller raised is still open, so offers are forbidden — but this
            # turn found nothing it could work on either. Reporting where the plan stands is the
            # only honest thing left in the vocabulary, and it does not close the session. The
            # session goes back to the Resting State the turn arrived in: `ELICITING_BLOCKING`
            # is a state the Director enters by *asking*, and claiming it here would leave F11
            # and F12 reading a session that says it is waiting for an answer to a question
            # nobody asked.
            return self._rest_at(resting, [self._summary_act()])
        offerable = tuple(
            entry.kind_key
            for entry in agenda.entries
            if entry.band is AgendaBand.UNMENTIONED and entry.is_actionable
        )
        if not offerable:
            return self._summarise_and_close()
        batch = offerable[: self._settings.offer_batch]
        self._session.offer_queue = batch
        self._transition(DialogueState.OFFERING_UNMENTIONED)
        return [
            self._act(
                OFFER_UNMENTIONED,
                {
                    "kind_keys": batch,
                    "message_keys": tuple(self._catalog.kind(key).message_key for key in batch),
                    "remaining": len(offerable) - len(batch),
                },
            )
        ]

    def _answer_offer(
        self,
        interpretation: TurnInterpretation,
        turn: UserTurn,
        *,
        accepted: bool,
        before: DialogueState,
    ) -> list[AssistantAct]:
        """Accept or decline whatever is on the table.

        A turn that names Component Kinds answers for exactly those; a bare yes or no answers
        for everything that was offered. Declining is final for *offers*: ``plan.decline``
        settles the component, and a settled Kind never appears in the Agenda again.
        """
        session = self._session
        offered = session.offer_queue
        if before is not DialogueState.OFFERING_UNMENTIONED or not offered:
            return self._rest_at(before, [self._clarify(CLARIFY_NOT_UNDERSTOOD)])
        named = tuple(key for key in interpretation.mentioned_kinds if key in offered)
        targets = named if named else offered
        session.offer_queue = ()
        for kind_key in targets:
            if accepted:
                # Accepting an offer *is* a mention: the traveller has now asked for it, so it
                # joins the mentioned band and is planned like anything else they raised.
                session.plan.mark_mentioned(kind_key, turn.index)
            else:
                session.plan.decline(kind_key)
        if accepted:
            return self._plan_next(before)
        return self._offer_or_close(before)

    def _summarise_and_close(self) -> list[AssistantAct]:
        """Report the plan honestly and close. Reachable from every state, by design."""
        self._transition(DialogueState.SUMMARISING)
        acts = [self._summary_act(), self._act(CLOSE)]
        self._session.pending_question = None
        self._session.offer_queue = ()
        self._transition(DialogueState.CLOSED)
        return acts

    def _summary_act(self) -> AssistantAct:
        """``deliver_summary``: the Plan Completeness and the Selections, as structured data.

        Emitted both by the closing sequence and by a bare "where are we?", which is why it is
        a method rather than two payloads that would drift apart.
        """
        plan = self._session.plan
        completeness = plan.completeness()
        selections: list[Mapping[str, object]] = []
        for kind_key in completeness.selected:
            component = plan.component(kind_key)
            chosen = component.selection
            if chosen is None:
                # `mark_selected` produces a chosen component with no Plan Option to name. The
                # dialogue never calls it, but a resumed plan (F12) may hold one.
                continue
            selections.append(
                {
                    "kind_key": kind_key,
                    "option_id": chosen.option_id,
                    "round_index": component.round_count - 1,
                }
            )
        return self._act(
            DELIVER_SUMMARY,
            {
                "selected": completeness.selected,
                "declined": completeness.declined,
                "open": completeness.open,
                "open_mentioned": completeness.open_mentioned,
                "is_closeable": completeness.is_closeable,
                "selections": tuple(selections),
            },
        )

    # -- the machinery ---------------------------------------------------------------------

    def _agenda(self) -> PlanningAgenda:
        """The Planning Agenda as of right now. Recomputed, never stored (F04)."""
        kinds = self._catalog.kinds()
        return build_agenda(
            self._session.plan,
            kinds,
            self._policy,
            plannable=self._plannability(),
            failure_skip=self._settings.failure_skip,
        )

    def _plannability(self) -> dict[str, bool]:
        """Which open Component Kinds could be sourced right now, by ``kind_key``."""
        plan = self._session.plan
        answers: dict[str, bool] = {}
        for kind in self._catalog.enabled_kinds():
            key = kind.kind_key
            component = plan.components.get(key)
            held = RequirementSet.empty(key) if component is None else _held(component)
            answers[key] = analyse(self._catalog.schema_for(key), held).is_plannable
        return answers

    def _transition(self, target: DialogueState) -> None:
        self._session.state = require_transition(self._session.state, target)

    def _rest_at(self, state: DialogueState, acts: list[AssistantAct]) -> list[AssistantAct]:
        """Put the session back where it was resting. A turn that changed nothing changes no
        state either, and ``INTERPRETING`` is left by exactly one edge whatever happens."""
        self._transition(state)
        return acts

    def _to_eliciting(self, component: PlanComponent) -> None:
        component.advance_to(ComponentStatus.ELICITING)

    def _to_sourcing(self, component: PlanComponent) -> None:
        """Walk a component to ``SOURCING`` by the shortest legal route.

        ``PENDING`` and ``ELICITING`` reach it through ``READY``; every other status the
        dialogue can be holding — ``AWAITING_CHOICE`` on a refinement, ``FAILED`` on a retry,
        ``SELECTED`` when a settled choice is reopened — has a direct edge (F02).
        """
        if component.status in _NEEDS_READY:
            component.advance_to(ComponentStatus.READY)
        component.advance_to(ComponentStatus.SOURCING)

    def _act(
        self,
        act: str,
        payload: Mapping[str, object] | None = None,
        kind_key: str | None = None,
    ) -> AssistantAct:
        """Build one Act in the session's locale. Every Act in this module comes from here."""
        return AssistantAct(
            act=act,
            payload={} if payload is None else payload,
            locale=self._session.locale,
            kind_key=kind_key,
        )

    def _clarify(
        self,
        reason_code: str,
        payload: Mapping[str, object] | None = None,
        kind_key: str | None = None,
    ) -> AssistantAct:
        fields: dict[str, object] = {"reason_code": reason_code}
        if payload is not None:
            fields.update(payload)
        return self._act(CLARIFY, fields, kind_key)

    def _record_turn(
        self,
        turn: UserTurn,
        interpretation: TurnInterpretation | None,
        acts: tuple[AssistantAct, ...],
        *,
        before: DialogueState,
        started_at: datetime,
    ) -> None:
        """One Turn Ledger entry per ``handle()``.

        F08 enriches these fields with model tokens and cost; F11 asserts on them.
        """
        session = self._session
        finished_at = self._clock.now()
        self._telemetry.record(
            TelemetryEvent(
                kind=TURN_EVENT_KIND,
                session_id=session.session_id,
                occurred_at=finished_at,
                fields={
                    "turn_index": turn.index,
                    "locale": session.locale,
                    "state_before": before.name,
                    "state_after": session.state.name,
                    "intent": None if interpretation is None else interpretation.intent.value,
                    "confidence": None if interpretation is None else interpretation.confidence,
                    "focus_kind": session.focus_kind,
                    "acts": tuple(act.act for act in acts),
                    "agenda": self._agenda().explain(),
                    "latency_ms": _elapsed_ms(started_at, finished_at),
                },
            )
        )


def _held(component: PlanComponent) -> RequirementSet:
    """A component's Requirement Set, empty rather than ``None`` before the first value.

    ``requirements is None`` means "nothing has been said about this yet", and three callers
    all needed the same two lines to turn that into something ``analyse`` and ``with_updates``
    can read.
    """
    held = component.requirements
    return RequirementSet.empty(component.kind_key) if held is None else held


def _missing_obligation(gap: BlockingGap, schema: RequirementSchema) -> _Obligation:
    """The obligation a Blocking Gap leaves unmet: nothing satisfies the rule yet."""
    preferred = _preferred_group(gap)
    prompts = tuple(spec.prompt_message_key for spec in preferred.missing_fields)
    return _Obligation(
        rule_name=gap.rule_name,
        field_names=_asked_field_names(gap),
        act=ASK_BLOCKING,
        payload={
            "rule_name": gap.rule_name,
            "field_groups": gap.field_names,
            "preferred_fields": tuple(spec.name for spec in preferred.missing_fields),
            "prompt_message_keys": prompts,
            "schema_key": schema.schema_key,
        },
        escalation={
            "rule_name": gap.rule_name,
            "field_names": preferred.field_names,
            "example_message_keys": _example_message_keys(preferred),
            "prompt_message_keys": prompts,
        },
    )


def _invalid_obligation(invalid: InvalidValue, schema: RequirementSchema) -> _Obligation:
    """The obligation a present-but-unusable value fails to satisfy.

    Keyed on the **Blocking Rule** that reads the field rather than on the field itself, so
    that "you have not said when" and "the range you gave runs backwards" count as attempts on
    one obligation rather than as two independent loops.
    """
    rule_name = _rule_reading(schema, invalid.field_name)
    spec = schema.field(invalid.field_name)
    return _Obligation(
        rule_name=rule_name,
        field_names=(invalid.field_name,),
        act=REPORT_INVALID_VALUE,
        payload={
            "field_name": invalid.field_name,
            "reason_message_key": invalid.reason_message_key,
        },
        escalation={
            "rule_name": rule_name,
            "field_names": (invalid.field_name,),
            "example_message_keys": (
                ()
                if spec is None or spec.example_message_key is None
                else (spec.example_message_key,)
            ),
            "prompt_message_keys": () if spec is None else (spec.prompt_message_key,),
        },
    )


def _rule_reading(schema: RequirementSchema, field_name: str) -> str:
    """The Blocking Rule that reads ``field_name``, or the field's own name as a last resort.

    A schema that has been through ``schema_problems`` always has one — a blocking field no
    rule references is refused at load — but a schema built in code need not have been, and a
    Pending Question needs *some* stable name to be about.
    """
    return next(
        (rule.name for rule in schema.blocking_rules if field_name in rule.referenced_fields),
        field_name,
    )


def _asked_field_names(gap: BlockingGap) -> tuple[str, ...]:
    """Every field that would help satisfy ``gap``, in declaration order, deduplicated.

    Stored on the Pending Question so that an answer can be recognised without re-deriving it
    from the schema — and so an interpreter can be told which field names are in play.
    """
    names: list[str] = []
    for group in gap.candidates:
        names += [name for name in group.field_names if name not in names]
    return tuple(names)


def _preferred_group(gap: BlockingGap) -> CandidateGroup:
    """Which of a rule's candidate groups to pursue: the cheapest one to finish.

    Gap analysis deliberately stops short of this — *which* way to satisfy an obligation is
    asking policy, and asking policy is the dialogue's. Fewest missing fields first, ties
    broken by the order the rule declares its groups in, so the question does not change
    between two turns for no reason the traveller can see.
    """
    return min(gap.candidates, key=lambda group: len(group.missing))


def _example_message_keys(group: CandidateGroup) -> tuple[str, ...]:
    """The example keys of a group's missing fields, for the Act that offers an illustration."""
    return tuple(
        spec.example_message_key
        for spec in group.missing_fields
        if spec.example_message_key is not None
    )


def _resolve_choice(slate: OptionSlate, reference: str | None) -> PlanOption | None:
    """Read a choice reference against one slate: an ``option_id``, or a 1-based ordinal."""
    if reference is None:
        return None
    named = slate.option(reference)
    if named is not None:
        return named
    stripped = reference.strip()
    if stripped.isdigit():
        ordinal = int(stripped)
        if 1 <= ordinal <= len(slate.options):
            return slate.options[ordinal - 1]
    return None


def _option_payload(option: PlanOption) -> Mapping[str, object]:
    """One Plan Option as structured data. No prose: there is none on a Plan Option to find.

    ``filter_notes`` are carried through rather than dropped. They are the optional filters this
    option fails (F06), and a slate that showed a €160 room to someone who asked for under €150
    *without* saying so would read as not having listened — which is the whole risk soft
    filtering runs. Field names, not sentences: the wording is the Message Catalogue's.
    """
    return {
        "option_id": option.option_id,
        "price": (
            None
            if option.price is None
            else {"amount_minor": option.price.amount_minor, "currency": option.price.currency}
        ),
        "facts": dict(option.facts),
        "source_id": option.provenance.source_id,
        "filter_notes": option.filter_notes,
    }


def _elapsed_ms(started_at: datetime, finished_at: datetime) -> float:
    """Milliseconds between two moments read from the ``Clock``.

    A frozen clock answers 0.0, which is the honest number for a replayed conversation: the
    latency that matters is the one that was recorded, not the one a replay happens to take.
    """
    return round((finished_at - started_at).total_seconds() * 1000, 3)
