"""The checks the domain repeats: non-empty text, and a timezone-aware moment.

Both rules were written out at every construction site before this module existed — five
copies of the string check and four of the timezone check, in the domain, an adapter and the
diagnostics. One copy each, here, so a rule cannot drift into two slightly different rules.

:func:`is_aware` is a *predicate* rather than a raiser on purpose. The domain raises
:class:`~tourganize.domain.errors.InvariantViolationError`, the Clock adapters raise
``ContractViolationError`` and ``doctor`` raises nothing at all — they share the question,
not the answer.
"""

from __future__ import annotations

from datetime import datetime

from tourganize.domain.errors import InvariantViolationError

__all__ = ["is_aware", "require_aware", "require_text"]


def require_text(value: object, field: str) -> str:
    """Return ``value`` when it is a non-blank ``str``, or raise.

    ``type(...) is not str`` rather than ``isinstance``: a ``str`` subclass that overrides
    ``__eq__`` is not something an identity key should be built from.
    """
    if type(value) is not str or not value.strip():
        raise InvariantViolationError(f"{field} must be a non-empty string, got {value!r}")
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
