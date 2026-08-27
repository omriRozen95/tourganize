"""``DialogueSettings`` — the dialogue's own slice of the resolved configuration.

The Director is handed this rather than :class:`~tourganize.platform.settings.Settings` for two
reasons. The mechanical one: ``tourganize.dialogue`` may import the standard library, the domain
and ``tourganize.ports``, and ``Settings`` is none of those. The better one: the Director needs
four numbers, and a type that says exactly which four is a type a test can build in one line
without a config directory.

The defaults live here because this is where the rules they configure live — the same bargain
:data:`~tourganize.domain.catalog.agenda.DEFAULT_AGENDA_FAILURE_SKIP` struck with
``TOURGANIZE_AGENDA_FAILURE_SKIP``. ``Settings`` reads these constants rather than spelling a
second ``3`` and ``2`` of its own, so each documented default has one definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from tourganize.domain.catalog import DEFAULT_AGENDA_FAILURE_SKIP
from tourganize.domain.errors import InvariantViolationError

__all__ = [
    "DEFAULT_MAX_REASKS",
    "DEFAULT_OFFER_BATCH",
    "DEFAULT_OPTIONAL_ASK_LIMIT",
    "DialogueSettings",
]

#: How many times one Blocking Rule is asked about before the Director offers the field's
#: example instead, and then gives up on that component. The documented default of
#: ``TOURGANIZE_DIALOGUE_MAX_REASKS``.
DEFAULT_MAX_REASKS: Final = 3

#: How many optional fields may be bundled into the single ``ask_optional`` Act that
#: accompanies a component's first slate. The documented default of
#: ``TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT``.
DEFAULT_OPTIONAL_ASK_LIMIT: Final = 2

#: How many unmentioned Component Kinds one ``offer_unmentioned`` Act may name. The documented
#: default of ``TOURGANIZE_DIALOGUE_OFFER_BATCH``.
DEFAULT_OFFER_BATCH: Final = 2


@dataclass(frozen=True, slots=True)
class DialogueSettings:
    """The four limits the state machine reads. Every one of them is at least 1."""

    max_reasks: int = DEFAULT_MAX_REASKS
    optional_ask_limit: int = DEFAULT_OPTIONAL_ASK_LIMIT
    offer_batch: int = DEFAULT_OFFER_BATCH
    failure_skip: int = DEFAULT_AGENDA_FAILURE_SKIP

    def __post_init__(self) -> None:
        for name in ("max_reasks", "optional_ask_limit", "offer_batch", "failure_skip"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise InvariantViolationError(
                    f"DialogueSettings.{name} must be at least 1, got {value!r}"
                )
