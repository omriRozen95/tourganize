"""Every invalid setting must fail at construction, with the key named in the message."""

from __future__ import annotations

from pathlib import Path

import pytest

from tourganize.platform.errors import ConfigurationError, TourganizeError
from tourganize.platform.settings import Settings


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("TOURGANIZE_ENV", "staging"),
        ("TOURGANIZE_LOG_FORMAT", "xml"),
        ("TOURGANIZE_LOG_LEVEL", "CHATTY"),
        ("TOURGANIZE_TELEMETRY_SINK", "syslog"),
        ("TOURGANIZE_PRIORITY_POLICY", "clairvoyant"),
        ("TOURGANIZE_AGENDA_FAILURE_SKIP", "soon"),
        ("TOURGANIZE_AGENDA_FAILURE_SKIP", "0"),
        ("TOURGANIZE_AGENDA_FAILURE_SKIP", "-1"),
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


def test_an_unreadable_secrets_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({"TOURGANIZE_SECRETS_FILE": str(tmp_path / "absent.env")})
    assert "TOURGANIZE_SECRETS_FILE" in str(raised.value)


def test_a_secrets_file_key_without_the_prefix_is_refused(tmp_path: Path) -> None:
    """Silently ignoring it is worse: the caller believes the secret was loaded."""
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("ANTHROPIC_API_KEY=sk-not-ours\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({"TOURGANIZE_SECRETS_FILE": str(secrets_file)})

    message = str(raised.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "TOURGANIZE_" in message
    assert "sk-not-ours" not in message


def test_a_secrets_file_carries_prefixed_keys_into_settings(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("TOURGANIZE_PROVIDER_API_KEY=from-the-file\n", encoding="utf-8")

    settings = Settings.from_env({"TOURGANIZE_SECRETS_FILE": str(secrets_file)})

    assert settings.secrets["TOURGANIZE_PROVIDER_API_KEY"].reveal() == "from-the-file"


def test_the_environment_wins_over_the_secrets_file(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("TOURGANIZE_PROVIDER_API_KEY=from-the-file\n", encoding="utf-8")

    settings = Settings.from_env(
        {
            "TOURGANIZE_SECRETS_FILE": str(secrets_file),
            "TOURGANIZE_PROVIDER_API_KEY": "from-the-environment",
        }
    )

    assert settings.secrets["TOURGANIZE_PROVIDER_API_KEY"].reveal() == "from-the-environment"


def test_a_malformed_secrets_file_line_names_the_line(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("TOURGANIZE_A_TOKEN=fine\nnonsense\n", encoding="utf-8")

    with pytest.raises(ConfigurationError) as raised:
        Settings.from_env({"TOURGANIZE_SECRETS_FILE": str(secrets_file)})
    assert "line 2" in str(raised.value)
