"""Structured logging for Tourganize.

``configure_logging`` installs exactly one handler on the ``tourganize`` logger, formatting
either JSON lines (machine-readable, the default outside dev) or a compact human line.

Correlation is done with a context, not by threading a logger through call signatures:
anything running inside ``log_context(session_id=..., turn_index=...)`` has those fields
attached to every record it emits, so F05's Director and F08's Gateway can be correlated
without either knowing about the other.

Secrets are safe here by construction: ``SecretValue`` redacts in ``str``, ``repr`` and
``format``, which is what ``%s``/``%r`` interpolation and the JSON fallback both use.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final, TextIO, final

from tourganize.platform.settings import Settings

__all__ = [
    "LOGGER_NAME",
    "ContextFilter",
    "HumanFormatter",
    "JsonLinesFormatter",
    "configure_logging",
    "current_log_context",
    "get_logger",
    "log_context",
]

LOGGER_NAME: Final = "tourganize"

#: Record attributes ``logging`` sets itself; anything else on a record is ours to emit.
_RESERVED: Final = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

#: Always present on a record, so a consumer can select on them without a null check.
_CORRELATION_FIELDS: Final = ("session_id", "turn_index")

_CONTEXT: ContextVar[Mapping[str, object]] = ContextVar("tourganize_log_context")


def current_log_context() -> Mapping[str, object]:
    """Return the fields currently bound by :func:`log_context`."""
    return _CONTEXT.get({})


@contextmanager
def log_context(**fields: object) -> Iterator[None]:
    """Bind ``fields`` onto every log record emitted inside the block.

    Nesting merges: an inner ``turn_index`` does not lose the outer ``session_id``.
    """
    merged = {**current_log_context(), **fields}
    token = _CONTEXT.set(merged)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


@final
class ContextFilter(logging.Filter):
    """Attach the bound context to each record, defaulting the correlation fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = current_log_context()
        for key, value in context.items():
            if key not in _RESERVED:
                setattr(record, key, value)
        for correlation_field in _CORRELATION_FIELDS:
            if not hasattr(record, correlation_field):
                setattr(record, correlation_field, None)
        return True


def _extras(record: logging.LogRecord) -> dict[str, object]:
    return {
        key: value
        for key, value in vars(record).items()
        if key not in _RESERVED and key not in _CORRELATION_FIELDS
    }


@final
class JsonLinesFormatter(logging.Formatter):
    """One JSON object per line, with the context fields promoted to top level."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "session_id": getattr(record, "session_id", None),
            "turn_index": getattr(record, "turn_index", None),
        }
        payload.update(_extras(record))
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info is not None:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


@final
class HumanFormatter(logging.Formatter):
    """A compact single line for a developer reading a terminal."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-7s %(name)s %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        session_id = getattr(record, "session_id", None)
        turn_index = getattr(record, "turn_index", None)
        if session_id is not None or turn_index is not None:
            line = f"{line} [session={session_id} turn={turn_index}]"
        extras = _extras(record)
        if extras:
            rendered = " ".join(f"{key}={value}" for key, value in sorted(extras.items()))
            line = f"{line} {rendered}"
        return line


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> logging.Logger:
    """Configure and return the ``tourganize`` logger. Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    handler: logging.Handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        JsonLinesFormatter() if settings.log_format == "json" else HumanFormatter()
    )
    handler.addFilter(ContextFilter())

    logger.addHandler(handler)
    logger.setLevel(settings.log_level)
    logger.propagate = False
    return logger


def get_logger(suffix: str | None = None) -> logging.Logger:
    """Return the application logger, or a named child of it."""
    if suffix is None:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}")
