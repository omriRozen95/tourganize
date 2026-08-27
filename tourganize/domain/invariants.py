"""The checks the domain repeats: non-empty text, an identifier key, a timezone-aware moment.

Every rule here was written out at each construction site before this module existed — five
copies of the string check, four of the timezone check and two of the key check, in the
domain, an adapter and the diagnostics. One copy each, here, so a rule cannot drift into two
slightly different rules.

:data:`MESSAGE_KEY_PATTERN` lives here for the same reason: a Component Kind and a Field Spec
both carry message keys, and two regexes for one shape is exactly the drift this module
exists to prevent.

:func:`is_aware` is a *predicate* rather than a raiser on purpose. The domain raises
:class:`~tourganize.domain.errors.InvariantViolationError`, the Clock adapters raise
``ContractViolationError`` and ``doctor`` raises nothing at all — they share the question,
not the answer.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Final

from tourganize.domain.errors import InvariantViolationError

__all__ = [
    "MESSAGE_KEY_PATTERN",
    "is_aware",
    "require_aware",
    "require_key",
    "require_text",
]

#: The shape of every message key: lower snake case, dot separated. One pattern, because a
#: key the Message Catalogue (F10) can look up must read the same wherever it was declared.
MESSAGE_KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_.]*")


def require_text(value: object, field: str) -> str:
    """Return ``value`` when it is a non-blank ``str``, or raise.

    ``type(...) is not str`` rather than ``isinstance``: a ``str`` subclass that overrides
    ``__eq__`` is not something an identity key should be built from.
    """
    if type(value) is not str or not value.strip():
        raise InvariantViolationError(f"{field} must be a non-empty string, got {value!r}")
    return value


def require_key(value: str, field: str, pattern: re.Pattern[str]) -> str:
    """Return ``value`` when it is non-blank text matching ``pattern`` in full, or raise.

    The identifier check every declared key shares — ``kind_key``, ``schema_key``, a field
    name, a rule name, a message key. The *pattern* differs per key; the rule that a key is
    text, is not blank, and matches its pattern end to end does not.
    """
    require_text(value, field)
    if not pattern.fullmatch(value):
        raise InvariantViolationError(f"{field} must match {pattern.pattern!r}, got {value!r}")
    return value


def is_aware(moment: datetime) -> bool:
    """True when ``moment`` carries a usable UTC offset.

    ``tzinfo`` alone is not enough: a ``tzinfo`` whose ``utcoffset`` returns ``None`` is as
    naive as no ``tzinfo`` at all, and only the second check catches it.
    """
    return moment.tzinfo is not None and moment.tzinfo.utcoffset(moment) is not None


def require_aware(moment: datetime, field: str) -> datetime:
    """Return ``moment`` when it is timezone-aware, or raise."""
    if not is_aware(moment):
        raise InvariantViolationError(f"{field} must be timezone-aware, got {moment!r}")
    return moment
