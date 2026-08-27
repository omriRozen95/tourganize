"""The errors the planning domain raises — and the root of the whole hierarchy.

The root lives here rather than beside the platform errors for one mechanical reason: the
domain may import nothing but the standard library and itself, so it cannot inherit from a
class defined in ``tourganize.platform``. ``tourganize.platform.errors`` therefore imports
:class:`TourganizeError` from this module and re-exports it, which keeps the documented
import path — ``from tourganize.platform.errors import TourganizeError`` — working and keeps
the promise that *every* deliberate error has one base class.

:class:`ContractViolationError` is here for the same mechanical reason. It is a *port*
failure by meaning, and ``tourganize.platform.errors`` is still where it is read from and
imported from — but the seam that checks a replaceable ``PriorityPolicy``'s output lives in
``tourganize.domain.catalog.prioritization`` (F04), so the class has to be reachable from the
domain to be raised there at all.

F05's two dialogue errors — :class:`IllegalDialogueTransitionError` and
:class:`SessionClosedError` — are here for the third variation on the same theme.
``tourganize.dialogue`` is a pure package too: it may import the standard library, the domain
and ``tourganize.ports``, and nothing else. An error it raises therefore has to live where it
can reach it, and one file listing the deliberate failures is worth more than three.

The split to remember when adding an error later: a rule the domain or the dialogue owns
raises from here; a failure of configuration, a port or an adapter raises from
``tourganize.platform.errors``.
"""

from __future__ import annotations

__all__ = [
    "ContractViolationError",
    "IllegalDialogueTransitionError",
    "IllegalTransitionError",
    "InvariantViolationError",
    "RequirementValueError",
    "SessionClosedError",
    "TourganizeError",
    "UnknownComponentKindError",
    "UnknownFieldError",
    "UnknownOptionError",
]


class TourganizeError(Exception):
    """Base class for every error Tourganize raises deliberately."""


class ContractViolationError(TourganizeError):
    """Data crossing a port boundary did not satisfy the declared contract.

    Read from :mod:`tourganize.platform.errors`, which re-exports it: the hierarchy is
    documented in one place. It is *defined* here because the domain may import nothing but
    the standard library and itself, and the Planning Agenda's check of a replaceable
    ``PriorityPolicy`` — a policy that invents or drops a ``kind_key`` — is a domain function.
    """


class InvariantViolationError(TourganizeError):
    """A domain value object or aggregate was handed data it forbids.

    Raised at construction time — a ``Money`` built from a float, a slate whose options
    belong to another Component Kind — so that an impossible object never exists to be
    passed on. The caller is a programming error, not a traveller.
    """


class IllegalTransitionError(TourganizeError):
    """A Plan Component was asked for a Component Status it cannot legally reach.

    The legal transitions are data in ``tourganize.domain.trip.component``; this is what
    they refuse.
    """


class UnknownComponentKindError(TourganizeError):
    """A ``kind_key`` was used that the Component Catalog does not declare."""


class UnknownOptionError(TourganizeError):
    """A Selection named a Plan Option that is not on the component's latest Option Slate."""


class UnknownFieldError(TourganizeError):
    """A Requirement Update named a field the Requirement Schema does not declare.

    Deliberately not ignored. An update for a field nobody declared almost always means an
    extraction prompt (F08) and a schema have drifted apart, and a silently dropped value is
    a traveller repeating themselves while the assistant appears not to listen.
    """


class RequirementValueError(TourganizeError):
    """A value failed the validation of its Field Spec's Field Kind.

    Carries :attr:`field_name` and :attr:`reason_message_key` rather than only a sentence,
    because the dialogue turns this into a re-ask in the traveller's own language: the key is
    what the Message Catalogue phrases, and :attr:`detail` is a diagnostic for the log that a
    traveller never sees.
    """

    def __init__(self, field_name: str, reason_message_key: str, detail: str) -> None:
        super().__init__(f"{field_name}: {detail} [{reason_message_key}]")
        self.field_name = field_name
        self.reason_message_key = reason_message_key
        self.detail = detail


class IllegalDialogueTransitionError(TourganizeError):
    """The Dialogue Director was asked for a Dialogue State it cannot legally reach.

    Always a bug in the Director, never a traveller's fault: every state a turn can lead to is
    a declared edge of ``tourganize.dialogue.states.TRANSITIONS``, so reaching this means the
    machine and its table have come apart. It is raised rather than logged for exactly that
    reason — a state machine that quietly ignores an impossible move is one that will later
    produce an impossible conversation.
    """


class SessionClosedError(TourganizeError):
    """A turn arrived for a Planning Session that has already closed.

    Not a silent reopen: a closed session stays closed, and picking a conversation back up is
    F12's ``resume``, which loads the stored session deliberately.
    """
