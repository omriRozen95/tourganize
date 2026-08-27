"""The Mentioned-First Rule, and the seam a replaceable Priority Policy plugs into.

:func:`build_agenda` is the whole of the rule the client stated explicitly: Component Kinds
the traveller raised are planned before Kinds they never mentioned. It partitions the open
Kinds into two bands, asks the injected :class:`PriorityPolicy` to order *within* each band,
and concatenates mentioned then unmentioned. The concatenation is here and **only** here — a
policy is handed one band at a time and never learns that another band exists, so a
replacement policy is structurally unable to reorder across the bands. That split is D3 in
``docs/architecture/decisions.md``: the hard rule in code, everything else in configuration.

Two consequences of that shape are worth stating, because they are the client's rules:

* **Outcome Dependencies are soft.** ``requires_outcome_of`` constrains *ordering*, and only
  among the Kinds that are open in the same band. A traveller who wants a hotel and never
  mentioned flights is never held waiting for flights: the dependency is in the other band, so
  it does not apply at all. :func:`awaited_within` is that rule, in one place, used both to
  order a band and to record ``blocked_by`` on the entries.
* **A stalled Kind cannot deadlock the conversation.** A component that has failed to source
  ``failure_skip`` times in a row is still listed, carrying the reason code ``failed_skipped``
  so that the reason stays visible — but it is not actionable, and the Agenda moves past it.

The plannability of each component arrives as a mapping rather than as an injected analyser.
Gap analysis is a pure function of a Requirement Schema and a Requirement Set, and schemas come
from a port; taking the answers instead of the means to compute them keeps this module free of
both, and makes every ordering case testable with a dict.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from tourganize.domain.catalog.agenda import (
    AWAITS_OUTCOME,
    DEFAULT_AGENDA_FAILURE_SKIP,
    FAILED_SKIPPED,
    NOT_PLANNABLE,
    READY,
    AgendaBand,
    AgendaEntry,
    PlanningAgenda,
)
from tourganize.domain.catalog.kinds import ComponentKind, only_enabled
from tourganize.domain.errors import ContractViolationError, InvariantViolationError
from tourganize.domain.trip import ComponentStatus, PlanComponent, TripPlan

__all__ = ["PriorityPolicy", "awaited_within", "build_agenda"]


@runtime_checkable
class PriorityPolicy(Protocol):
    """Orders the Component Kinds *within* one Agenda band.

    Read this port from :mod:`tourganize.ports.catalog`, which re-exports it — that module is
    where the application's ports are listed. It is *defined* in the domain because
    :func:`build_agenda` is a domain function and the domain may import nothing but the
    standard library and itself, the same mechanical reason
    :class:`~tourganize.domain.errors.TourganizeError` lives where it does.

    The contract is narrow on purpose, because the client's "importance metric, to be defined
    later" is what will replace it: given some Component Kinds and the Trip Plan they belong
    to, return **the same** ``kind_key`` set in the order they should be planned. Inventing a
    key, dropping one or repeating one is refused at the seam by :func:`build_agenda` — a
    policy is replaceable, so its output is checked rather than trusted.

    What a policy may *not* do is decide anything about bands. It never sees more than one at
    a time, so the Mentioned-First Rule cannot be weakened by a policy that means well.
    """

    @property
    def policy_id(self) -> str:
        """A short, stable identifier for this policy, for telemetry and ``doctor``.

        The value of ``TOURGANIZE_PRIORITY_POLICY`` that selects it, where one does: an
        Agenda in a log is only explainable if the ordering that produced it is named.
        """
        ...

    def order(self, candidates: Sequence[ComponentKind], plan: TripPlan) -> Sequence[str]:
        """Return the ``kind_key`` of every candidate, in the order they should be planned."""
        ...


def awaited_within(kind: ComponentKind, open_kind_keys: Collection[str]) -> tuple[str, ...]:
    """The Outcome Dependencies of ``kind`` that are still open among ``open_kind_keys``.

    The soft-dependency rule, in one function, so the ordering and the ``blocked_by`` it is
    reported as cannot drift apart. A dependency that is settled, declined, disabled or in the
    other band is simply not in the collection it is looked up in, and therefore constrains
    nothing.
    """
    return tuple(key for key in kind.requires_outcome_of if key in open_kind_keys)


def build_agenda(
    plan: TripPlan,
    kinds: Sequence[ComponentKind],
    policy: PriorityPolicy,
    *,
    plannable: Mapping[str, bool] | None = None,
    failure_skip: int = DEFAULT_AGENDA_FAILURE_SKIP,
) -> PlanningAgenda:
    """Return the Planning Agenda of ``plan`` over the Component Kinds ``kinds`` declares.

    ``kinds`` is what a ``ComponentCatalog`` declares — disabled Kinds are dropped here rather
    than by the caller, so "a disabled Kind is not plannable" holds however the catalog was
    read. Settled Kinds (selected or declined) are dropped too: the Agenda is what is *left*.

    ``plannable`` maps ``kind_key`` to the answer of ``GapReport.is_plannable``. A Kind it does
    not mention is treated as not yet Plannable — on no information the dialogue should elicit,
    not source. Omit the mapping entirely and no entry carries that reason code.
    """
    if type(failure_skip) is not int or failure_skip < 1:
        raise InvariantViolationError(
            f"failure_skip must be at least 1, got {failure_skip!r}; a Component Kind that is "
            f"skipped before it has failed once could never be planned at all"
        )
    candidates = [kind for kind in only_enabled(kinds) if _is_open(plan, kind.kind_key)]
    mentioned = [kind for kind in candidates if _is_mentioned(plan, kind.kind_key)]
    unmentioned = [kind for kind in candidates if not _is_mentioned(plan, kind.kind_key)]
    context = _Context(plan=plan, policy=policy, plannable=plannable, failure_skip=failure_skip)
    # The Mentioned-First Rule: two bands, ordered independently, concatenated in this fixed
    # order. Nothing on these lines is configurable, and nothing else concatenates them.
    return PlanningAgenda(
        entries=(
            *_band_entries(AgendaBand.MENTIONED, mentioned, context),
            *_band_entries(AgendaBand.UNMENTIONED, unmentioned, context),
        )
    )


@dataclass(frozen=True, slots=True)
class _Context:
    """What every band of one ``build_agenda`` call is built against.

    One object rather than four arguments threaded twice: the two bands must be built from
    *identical* inputs, and a difference between the two call sites would be invisible.
    """

    plan: TripPlan
    policy: PriorityPolicy
    plannable: Mapping[str, bool] | None
    failure_skip: int


def _band_entries(
    band: AgendaBand, candidates: Sequence[ComponentKind], context: _Context
) -> tuple[AgendaEntry, ...]:
    """Order one band through the policy and turn the result into Agenda entries."""
    if not candidates:
        return ()
    by_key = {kind.kind_key: kind for kind in candidates}
    open_in_band = frozenset(by_key)
    entries: list[AgendaEntry] = []
    for rank, kind_key in enumerate(_ordered_or_raise(context.policy, candidates, context.plan)):
        awaits = awaited_within(by_key[kind_key], open_in_band)
        entries.append(
            AgendaEntry(
                kind_key=kind_key,
                band=band,
                rank=rank,
                blocked_by=awaits,
                reason_code=_reason_code(kind_key, awaits=awaits, context=context),
            )
        )
    return tuple(entries)


def _ordered_or_raise(
    policy: PriorityPolicy, candidates: Sequence[ComponentKind], plan: TripPlan
) -> tuple[str, ...]:
    """Ask the policy for an order, and refuse anything that is not a permutation.

    Policies are replaceable, so this is a port boundary and its contract is checked rather
    than assumed. A dropped Kind would silently disappear from the conversation and an invented
    one would be planned against a Kind the catalog never declared — both are worse than a
    refusal naming the policy that did it.
    """
    given = tuple(kind.kind_key for kind in candidates)
    ordered = tuple(policy.order(candidates, plan))
    if sorted(ordered) == sorted(given):
        return ordered
    expected, produced = set(given), set(ordered)
    problems = [
        f"invented {sorted(produced - expected)}" if produced - expected else "",
        f"dropped {sorted(expected - produced)}" if expected - produced else "",
        (
            f"repeated {sorted({key for key in ordered if ordered.count(key) > 1})}"
            if len(ordered) != len(produced)
            else ""
        ),
    ]
    raise ContractViolationError(
        f"PriorityPolicy {policy.policy_id!r} must return exactly the Component Kinds it was "
        f"given, in some order: it was given {sorted(given)} and returned {list(ordered)} — "
        + ", ".join(problem for problem in problems if problem)
    )


def _reason_code(kind_key: str, *, awaits: tuple[str, ...], context: _Context) -> str:
    """The one code that best explains this entry's place, most decisive first.

    Being skipped outranks everything, because it is the only code that changes what the
    dialogue may do. An open dependency comes next: it explains the *position*, which is the
    fact F05's telemetry reads the Agenda for. Plannability is last, and is the difference
    between eliciting and sourcing once an entry is reached.
    """
    if _has_stalled(context.plan, kind_key, context.failure_skip):
        return FAILED_SKIPPED
    if awaits:
        return AWAITS_OUTCOME
    if context.plannable is not None and not context.plannable.get(kind_key, False):
        return NOT_PLANNABLE
    return READY


def _has_stalled(plan: TripPlan, kind_key: str, failure_skip: int) -> bool:
    component = _component(plan, kind_key)
    return (
        component is not None
        and component.status is ComponentStatus.FAILED
        and component.consecutive_failures >= failure_skip
    )


def _is_open(plan: TripPlan, kind_key: str) -> bool:
    """A Kind nobody has touched is open; one that is selected or declined is not."""
    component = _component(plan, kind_key)
    return component is None or not component.is_settled


def _is_mentioned(plan: TripPlan, kind_key: str) -> bool:
    component = _component(plan, kind_key)
    return component is not None and component.is_mentioned


def _component(plan: TripPlan, kind_key: str) -> PlanComponent | None:
    """The plan's component for ``kind_key``, or ``None`` when it has none yet.

    The Agenda ranks Component Kinds the plan has never heard of — that is what an unmentioned
    band mostly is — so absence is an ordinary answer here rather than an error.
    """
    return plan.components.get(kind_key)
