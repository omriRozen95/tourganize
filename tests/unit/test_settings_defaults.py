"""``Settings.from_env({})`` must yield exactly the documented defaults."""

from __future__ import annotations

from pathlib import Path

from tourganize.platform.settings import Settings, unrecognised_keys


def test_every_documented_default() -> None:
    settings = Settings.from_env({})

    assert settings.env == "dev"
    assert settings.log_level == "INFO"
    assert settings.log_format == "human"
    assert settings.config_dir == Path("config")
    assert settings.catalog_path == Path("config/catalog/components.yaml")
    assert settings.data_dir == Path("var")
    assert settings.telemetry_sink == "jsonl"
    assert settings.telemetry_path == Path("var/telemetry.jsonl")
    assert settings.secrets_file is None
    assert dict(settings.secrets) == {}


def test_log_format_defaults_to_json_outside_dev() -> None:
    assert Settings.from_env({"TOURGANIZE_ENV": "prod"}).log_format == "json"
    assert Settings.from_env({"TOURGANIZE_ENV": "test"}).log_format == "json"
    assert Settings.from_env({"TOURGANIZE_ENV": "dev"}).log_format == "human"


def test_explicit_log_format_wins_over_the_env_derived_default() -> None:
    settings = Settings.from_env({"TOURGANIZE_ENV": "prod", "TOURGANIZE_LOG_FORMAT": "human"})
    assert settings.log_format == "human"


def test_telemetry_path_follows_the_data_dir() -> None:
    settings = Settings.from_env({"TOURGANIZE_DATA_DIR": "/srv/state"})
    assert settings.telemetry_path == Path("/srv/state/telemetry.jsonl")


def test_the_catalog_path_follows_the_config_dir() -> None:
    settings = Settings.from_env({"TOURGANIZE_CONFIG_DIR": "/srv/conf"})
    assert settings.catalog_path == Path("/srv/conf/catalog/components.yaml")


def test_an_explicit_catalog_path_wins_over_the_config_dir() -> None:
    settings = Settings.from_env(
        {"TOURGANIZE_CONFIG_DIR": "/srv/conf", "TOURGANIZE_CATALOG_PATH": "/etc/kinds.yaml"}
    )
    assert settings.catalog_path == Path("/etc/kinds.yaml")


def test_a_missing_catalog_file_is_not_a_settings_error() -> None:
    """Absence is reported by `doctor` and refused by the command that needs it, not here."""
    settings = Settings.from_env({"TOURGANIZE_CATALOG_PATH": "/nowhere/components.yaml"})
    assert settings.catalog_path == Path("/nowhere/components.yaml")


def test_the_telemetry_path_default_does_not_depend_on_the_selected_sink() -> None:
    """The documented default is unconditional; the null sink simply never writes there."""
    settings = Settings.from_env({"TOURGANIZE_TELEMETRY_SINK": "null"})
    assert settings.telemetry_path == Path("var/telemetry.jsonl")


def test_blank_values_fall_back_to_the_default() -> None:
    settings = Settings.from_env({"TOURGANIZE_ENV": "   ", "TOURGANIZE_LOG_LEVEL": ""})
    assert settings.env == "dev"
    assert settings.log_level == "INFO"


def test_log_level_is_normalised_to_upper_case() -> None:
    assert Settings.from_env({"TOURGANIZE_LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_settings_are_frozen() -> None:
    settings = Settings.from_env({})
    try:
        settings.env = "prod"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Settings must be immutable")


def test_unrecognised_keys_are_reported_but_never_fatal() -> None:
    environ = {"TOURGANIZE_ENV": "dev", "TOURGANIZE_LGO_LEVEL": "DEBUG", "PATH": "/usr/bin"}
    assert Settings.from_env(environ).env == "dev"
    assert unrecognised_keys(environ) == ("TOURGANIZE_LGO_LEVEL",)


def test_secret_keys_are_not_reported_as_unrecognised() -> None:
    assert unrecognised_keys({"TOURGANIZE_PROVIDER_API_KEY": "x"}) == ()
