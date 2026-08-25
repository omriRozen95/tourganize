"""A secret must never be printable by accident, and the file must load beneath the env."""

from __future__ import annotations

import io
import json
from pathlib import Path

from tourganize.platform.logging import configure_logging
from tourganize.platform.secrets import REDACTED, SecretValue
from tourganize.platform.settings import Settings

LEAK = "hunter2-do-not-print"


def test_repr_str_and_format_all_redact() -> None:
    secret = SecretValue(LEAK)

    assert LEAK not in repr(secret)
    assert LEAK not in str(secret)
    assert LEAK not in f"{secret}"
    assert LEAK not in f"{secret!r}"
    assert LEAK not in f"{secret:>40}"
    assert LEAK not in "%s %r" % (secret, secret)  # noqa: UP031 - %-interpolation is the case under test
    assert REDACTED in repr(secret)


def test_reveal_is_the_only_way_out() -> None:
    assert SecretValue(LEAK).reveal() == LEAK


def test_equality_and_truthiness() -> None:
    assert SecretValue("a") == SecretValue("a")
    assert SecretValue("a") != SecretValue("b")
    assert SecretValue("a") != "a"
    assert bool(SecretValue("a"))
    assert not bool(SecretValue(""))


def test_a_secret_does_not_reach_the_log(tmp_path: Path) -> None:
    stream = io.StringIO()
    settings = Settings.from_env({"TOURGANIZE_LOG_FORMAT": "json", "TOURGANIZE_ENV": "test"})
    logger = configure_logging(settings, stream=stream)

    logger.warning("token=%s repr=%r", SecretValue(LEAK), SecretValue(LEAK))
    logger.warning("bundle %s", {"token": SecretValue(LEAK)})

    written = stream.getvalue()
    assert LEAK not in written
    assert REDACTED in written
    for line in written.strip().splitlines():
        json.loads(line)


def test_secret_keys_are_collected_from_the_environment() -> None:
    settings = Settings.from_env(
        {
            "TOURGANIZE_PROVIDER_API_KEY": LEAK,
            "TOURGANIZE_MODEL_TOKEN": LEAK,
            "TOURGANIZE_ENV": "dev",
        }
    )

    assert set(settings.secrets) == {"TOURGANIZE_PROVIDER_API_KEY", "TOURGANIZE_MODEL_TOKEN"}
    assert settings.secrets["TOURGANIZE_MODEL_TOKEN"].reveal() == LEAK
    assert LEAK not in repr(settings)
    assert LEAK not in str(settings.describe())


def test_the_secrets_file_loads_beneath_the_environment(tmp_path: Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "TOURGANIZE_PROVIDER_API_KEY=from-file",
                'TOURGANIZE_MODEL_TOKEN="quoted-from-file"',
                "TOURGANIZE_LOG_LEVEL=DEBUG",
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings.from_env(
        {
            "TOURGANIZE_SECRETS_FILE": str(secrets_file),
            "TOURGANIZE_PROVIDER_API_KEY": "from-environment",
        }
    )

    assert settings.secrets["TOURGANIZE_PROVIDER_API_KEY"].reveal() == "from-environment"
    assert settings.secrets["TOURGANIZE_MODEL_TOKEN"].reveal() == "quoted-from-file"
    assert settings.log_level == "DEBUG"
    assert settings.secrets_file == secrets_file


def test_describe_reports_secret_names_only() -> None:
    described = Settings.from_env({"TOURGANIZE_PROVIDER_API_KEY": LEAK}).describe()
    assert described["secrets"] == f"1 loaded (TOURGANIZE_PROVIDER_API_KEY={REDACTED})"
