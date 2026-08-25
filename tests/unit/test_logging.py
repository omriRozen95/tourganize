"""Structured logging: parseable JSON, a human line, and correlation without plumbing."""

from __future__ import annotations

import io
import json
import logging

from tourganize.platform.logging import (
    LOGGER_NAME,
    configure_logging,
    current_log_context,
    get_logger,
    log_context,
)
from tourganize.platform.settings import Settings


def _json_lines(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().strip().splitlines()]


def test_json_format_emits_one_object_per_record() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json"}), stream=stream)

    logger.info("first")
    logger.warning("second")

    records = _json_lines(stream)
    assert [record["message"] for record in records] == ["first", "second"]
    assert [record["level"] for record in records] == ["INFO", "WARNING"]
    assert all(record["logger"] == LOGGER_NAME for record in records)


def test_the_context_reaches_the_record_without_being_passed_in() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json"}), stream=stream)

    with log_context(session_id="session-7"):
        with log_context(turn_index=2):
            logger.info("inside")
        logger.info("outer")
    logger.info("outside")

    inner, outer, outside = _json_lines(stream)
    assert (inner["session_id"], inner["turn_index"]) == ("session-7", 2)
    assert (outer["session_id"], outer["turn_index"]) == ("session-7", None)
    assert (outside["session_id"], outside["turn_index"]) == (None, None)


def test_the_context_is_restored_after_the_block() -> None:
    with log_context(session_id="a"):
        assert dict(current_log_context()) == {"session_id": "a"}
    assert dict(current_log_context()) == {}


def test_extra_fields_are_promoted_to_top_level() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json"}), stream=stream)

    logger.info("sourced", extra={"kind": "slate", "round_index": 2})

    record = _json_lines(stream)[0]
    assert record["kind"] == "slate"
    assert record["round_index"] == 2


def test_non_serialisable_values_do_not_break_the_line() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json"}), stream=stream)

    logger.info("odd", extra={"kind": object()})

    assert isinstance(_json_lines(stream)[0]["kind"], str)


def test_hebrew_is_written_unescaped() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json"}), stream=stream)

    logger.info("city %s", "פריז")

    assert "פריז" in stream.getvalue()


def test_human_format_carries_the_correlation_fields() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "human"}), stream=stream)

    with log_context(session_id="session-7", turn_index=1):
        logger.info("hello")

    line = stream.getvalue().strip()
    assert "session=session-7" in line
    assert "turn=1" in line
    assert "hello" in line


def test_exceptions_are_recorded() -> None:
    stream = io.StringIO()
    logger = configure_logging(Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json"}), stream=stream)

    try:
        raise ValueError("nope")
    except ValueError:
        logger.exception("failed")

    record = _json_lines(stream)[0]
    assert "ValueError: nope" in str(record["exception"])


def test_configuring_twice_does_not_duplicate_handlers() -> None:
    settings = Settings.from_env({})
    configure_logging(settings, stream=io.StringIO())
    configure_logging(settings, stream=io.StringIO())

    assert len(logging.getLogger(LOGGER_NAME).handlers) == 1


def test_the_level_comes_from_settings() -> None:
    stream = io.StringIO()
    logger = configure_logging(
        Settings.from_env({"TOURGANIZE_LOG_LEVEL": "WARNING", "TOURGANIZE_LOG_FORMAT": "json"}),
        stream=stream,
    )

    logger.info("suppressed")
    logger.warning("kept")

    assert [record["message"] for record in _json_lines(stream)] == ["kept"]


def test_get_logger_returns_children_of_one_root() -> None:
    assert get_logger().name == LOGGER_NAME
    assert get_logger("telemetry").name == f"{LOGGER_NAME}.telemetry"
