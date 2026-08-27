"""The errors the planning domain raises — and the root of the whole hierarchy.

The root lives here rather than beside the platform errors for one mechanical reason: the
domain may import nothing but the standard library and itself, so it cannot inherit from a
class defined in ``tourganize.platform``. ``tourganize.platform.errors`` therefore imports
:class:`TourganizeError` from this module and re-exports it, which keeps the documented
import path — ``from tourganize.platform.errors import TourganizeError`` — working and keeps
the promise that *every* deliberate error has one base class.

The split to remember when adding an error later: a rule the domain owns raises from here; a
failure of configuration, a port or an adapter raises from ``tourganize.platform.errors``.
"""

from __future__ import annotations

__all__ = [
    "IllegalTransitionError",
    "InvariantViolationError",
    "RequirementValueError",
    "TourganizeError",
    "UnknownComponentKindError",
    "UnknownFieldError",
    "UnknownOptionError",
]


class TourganizeError(Exception):
    """Base class for every error Tourganize raises deliberately."""


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
