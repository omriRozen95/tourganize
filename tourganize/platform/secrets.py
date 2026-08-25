"""``SecretValue`` — a string that refuses to print itself.

Secrets reach the process through the environment or through the file named by
``TOURGANIZE_SECRETS_FILE``. From the moment :class:`~tourganize.platform.settings.Settings`
is built they are only ever held in this wrapper, whose ``repr``, ``str`` and ``format``
all redact. Reading the real value requires the explicit :meth:`SecretValue.reveal` call,
which makes every use of a secret greppable.
"""

from __future__ import annotations

from typing import Final, final

__all__ = ["REDACTED", "SecretValue"]

REDACTED: Final = "***"


@final
class SecretValue:
    """A string whose value never appears in output produced by accident."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the wrapped value. The only way to read it."""
        return self._value

    def __repr__(self) -> str:
        return f"SecretValue({REDACTED})"

    def __str__(self) -> str:
        return REDACTED

    def __format__(self, format_spec: str) -> str:
        return REDACTED

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretValue):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(("SecretValue", self._value))
