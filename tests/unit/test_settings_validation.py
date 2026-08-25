"""Every invalid setting must fail at construction, with the key named in the message."""

from __future__ import annotations

from pathlib import Path

import pytest

from tourganize.platform.errors import ConfigurationError, TourganizeError
from tourganize.platform.settings import Settings, _integer


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("TOURGANIZE_ENV", "staging"),
        ("TOURGANIZE_LOG_FORMAT", "xml"),
        ("TOURGANIZE_LOG_LEVEL", "CHATTY"),
        ("TOURGANIZE_TELEMETRY_SINK", "syslog"),
    ],
)
def test_invalid_enum_values_are_rejected(key: str, value: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({key: value})
    assert key in str(raised.value)
    assert isinstance(raised.value, TourganizeError)


@pytest.mark.parametrize(
    "key",
    ["TOURGANIZE_CONFIG_DIR", "TOURGANIZE_DATA_DIR"],
)
def test_a_directory_key_pointing_at_a_file_is_rejected(tmp_path: Path, key: str) -> None:
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("", encoding="utf-8")

    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({key: str(file_path)})
    assert key in str(raised.value)


@pytest.mark.parametrize(
    "key",
    ["TOURGANIZE_TELEMETRY_PATH", "TOURGANIZE_SECRETS_FILE"],
)
def test_a_file_key_pointing_at_a_directory_is_rejected(tmp_path: Path, key: str) -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({key: str(tmp_path)})
    assert key in str(raised.value)


def test_a_malformed_integer_is_rejected() -> None:
    # No integer key exists yet; later features route theirs through this one parser.
    with pytest.raises(ConfigurationError) as raised:
        _integer({"TOURGANIZE_SLATE_SIZE": "three"}, "TOURGANIZE_SLATE_SIZE", 3)
    assert "TOURGANIZE_SLATE_SIZE" in str(raised.value)

    with pytest.raises(ConfigurationError):
        _integer({"TOURGANIZE_SLATE_SIZE": "-1"}, "TOURGANIZE_SLATE_SIZE", 3, minimum=1)

    assert _integer({}, "TOURGANIZE_SLATE_SIZE", 3) == 3
    assert _integer({"TOURGANIZE_SLATE_SIZE": "5"}, "TOURGANIZE_SLATE_SIZE", 3) == 5


def test_an_unreadable_secrets_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({"TOURGANIZE_SECRETS_FILE": str(tmp_path / "absent.env")})
    assert "TOURGANIZE_SECRETS_FILE" in str(raised.value)


def test_a_malformed_secrets_file_line_names_the_line(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("TOURGANIZE_A_TOKEN=fine\nnonsense\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({"TOURGANIZE_SECRETS_FILE": str(secrets_file)})
    assert "line 2" in str(raised.value)
