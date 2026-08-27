"""``Settings.from_env({})`` must yield exactly the documented defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from tourganize.dialogue import (
    DEFAULT_MAX_REASKS,
    DEFAULT_OFFER_BATCH,
    DEFAULT_OPTIONAL_ASK_LIMIT,
)
from tourganize.domain.catalog import DEFAULT_AGENDA_FAILURE_SKIP
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.settings import (
    OptionSourceProfile,
    Settings,
    unrecognised_keys,
)


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
    assert settings.priority_policy == "weighted"
    assert settings.agenda_failure_skip == 2
    assert settings.schema_dir == Path("config/catalog/schemas")
    assert settings.dialogue_max_reasks == 3
    assert settings.dialogue_optional_ask_limit == 2
    assert settings.dialogue_offer_batch == 2
    assert settings.interpreter == "keyword"
    assert settings.keyword_config_dir == Path("config/interpretation")
    assert settings.option_source_profile == OptionSourceProfile()
    assert settings.option_source_profile.for_kind("anything") == "fixture"
    assert settings.fixture_dir == Path("fixtures/options")
    assert settings.slate_size == 3
    assert settings.option_filter_strict is False
    assert settings.option_source_timeout_seconds == 10
    assert settings.surface == "terminal"
    assert settings.message_dir == Path("config/messages")
    assert settings.default_locale == "en"
    assert settings.supported_locales == ("en", "he")
    assert settings.secrets_file is None
    assert dict(settings.secrets) == {}


def test_the_agenda_skip_default_is_the_domain_s_one_definition_of_it() -> None:
    """One documented default, one definition: the rule it configures lives in the domain."""
    assert Settings.from_env({}).agenda_failure_skip == DEFAULT_AGENDA_FAILURE_SKIP


def test_the_dialogue_defaults_are_the_dialogue_s_one_definition_of_them() -> None:
    """One documented default, one definition: the rules they configure live in the dialogue."""
    settings = Settings.from_env({})

    assert settings.dialogue_max_reasks == DEFAULT_MAX_REASKS
    assert settings.dialogue_optional_ask_limit == DEFAULT_OPTIONAL_ASK_LIMIT
    assert settings.dialogue_offer_batch == DEFAULT_OFFER_BATCH


def test_the_dialogue_counts_can_each_be_tuned_by_one_key() -> None:
    settings = Settings.from_env(
        {
            "TOURGANIZE_DIALOGUE_MAX_REASKS": "5",
            "TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT": "1",
            "TOURGANIZE_DIALOGUE_OFFER_BATCH": "3",
        }
    )

    assert (settings.dialogue_max_reasks, settings.dialogue_offer_batch) == (5, 3)
    assert settings.dialogue_optional_ask_limit == 1


def test_a_dialogue_count_below_one_is_refused() -> None:
    """A re-ask limit of zero is a question nobody ever asks."""
    with pytest.raises(ConfigurationError, match="must be at least 1"):
        Settings.from_env({"TOURGANIZE_DIALOGUE_MAX_REASKS": "0"})


def test_the_keyword_config_dir_follows_the_config_dir() -> None:
    settings = Settings.from_env({"TOURGANIZE_CONFIG_DIR": "/srv/conf"})

    assert settings.keyword_config_dir == Path("/srv/conf/interpretation")


def test_an_explicit_keyword_config_dir_wins_over_the_config_dir() -> None:
    settings = Settings.from_env(
        {"TOURGANIZE_CONFIG_DIR": "/srv/conf", "TOURGANIZE_KEYWORD_CONFIG_DIR": "/etc/phrases"}
    )

    assert settings.keyword_config_dir == Path("/etc/phrases")


def test_the_interpreter_key_accepts_the_value_a_later_feature_delivers() -> None:
    """`model` resolves here and is refused by the Composition Root, which names F08."""
    assert Settings.from_env({"TOURGANIZE_INTERPRETER": "model"}).interpreter == "model"

    with pytest.raises(ConfigurationError, match="is not one of"):
        Settings.from_env({"TOURGANIZE_INTERPRETER": "regex"})


def test_the_priority_policy_can_be_swapped_by_one_key() -> None:
    assert Settings.from_env({"TOURGANIZE_PRIORITY_POLICY": "fixed"}).priority_policy == "fixed"
    assert Settings.from_env({"TOURGANIZE_AGENDA_FAILURE_SKIP": "5"}).agenda_failure_skip == 5


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


def test_the_message_dir_follows_the_config_dir() -> None:
    """The Message Catalogue lives beside the catalog it phrases, and moves with it."""
    settings = Settings.from_env({"TOURGANIZE_CONFIG_DIR": "/srv/conf"})

    assert settings.message_dir == Path("/srv/conf/messages")


def test_an_explicit_message_dir_wins_over_the_config_dir() -> None:
    settings = Settings.from_env(
        {"TOURGANIZE_CONFIG_DIR": "/srv/conf", "TOURGANIZE_MESSAGE_DIR": "/etc/wording"}
    )

    assert settings.message_dir == Path("/etc/wording")


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


def test_the_keys_this_release_reads_are_not_reported_as_unrecognised() -> None:
    """Every key added to the table has to be added to ``KNOWN_KEYS`` in the same change."""
    environ = {
        "TOURGANIZE_PRIORITY_POLICY": "fixed",
        "TOURGANIZE_AGENDA_FAILURE_SKIP": "3",
        "TOURGANIZE_DIALOGUE_MAX_REASKS": "3",
        "TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT": "2",
        "TOURGANIZE_DIALOGUE_OFFER_BATCH": "2",
        "TOURGANIZE_INTERPRETER": "keyword",
        "TOURGANIZE_KEYWORD_CONFIG_DIR": "config/interpretation",
        "TOURGANIZE_OPTION_SOURCE_PROFILE": "fixture",
        "TOURGANIZE_FIXTURE_DIR": "fixtures/options",
        "TOURGANIZE_SLATE_SIZE": "3",
        "TOURGANIZE_OPTION_FILTER_STRICT": "false",
        "TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS": "10",
        "TOURGANIZE_SURFACE": "terminal",
        "TOURGANIZE_MESSAGE_DIR": "config/messages",
        "TOURGANIZE_DEFAULT_LOCALE": "en",
        "TOURGANIZE_SUPPORTED_LOCALES": "en,he",
    }

    assert unrecognised_keys(environ) == ()


def test_secret_keys_are_not_reported_as_unrecognised() -> None:
    assert unrecognised_keys({"TOURGANIZE_PROVIDER_API_KEY": "x"}) == ()


def test_one_profile_name_covers_every_component_kind() -> None:
    profile = Settings.from_env({"TOURGANIZE_OPTION_SOURCE_PROFILE": "world"}).option_source_profile

    assert profile.default == "world"
    assert profile.for_kind("alpha") == "world"
    assert profile.names == ("world",)
    assert profile.describe() == "world"


def test_a_per_kind_profile_override_is_parsed() -> None:
    """The DoD's own spelling: two Component Kinds, each named with its own profile."""
    profile = Settings.from_env(
        {"TOURGANIZE_OPTION_SOURCE_PROFILE": "alpha=fixture,beta=world"}
    ).option_source_profile

    assert profile.for_kind("alpha") == "fixture"
    assert profile.for_kind("beta") == "world"
    assert profile.for_kind("gamma") == "fixture"  # the default, for the Kinds nobody named
    assert profile.names == ("fixture", "world")
    assert profile.describe() == "alpha=fixture,beta=world (default fixture)"


def test_a_profile_override_map_is_read_only() -> None:
    profile = Settings.from_env(
        {"TOURGANIZE_OPTION_SOURCE_PROFILE": "alpha=world"}
    ).option_source_profile

    with pytest.raises(TypeError):
        profile.per_kind["beta"] = "live"  # type: ignore[index]


def test_mixing_a_bare_profile_with_overrides_is_refused() -> None:
    """It reads as though the bare word were a fallback, and it is not one."""
    with pytest.raises(ConfigurationError, match="mixes a bare profile"):
        Settings.from_env({"TOURGANIZE_OPTION_SOURCE_PROFILE": "fixture,alpha=world"})


def test_two_bare_profiles_are_refused() -> None:
    with pytest.raises(ConfigurationError, match="names 2 profiles"):
        Settings.from_env({"TOURGANIZE_OPTION_SOURCE_PROFILE": "fixture,world"})


@pytest.mark.parametrize("value", ["postcard", "Fixture", "alpha=postcard"])
def test_a_profile_nobody_declared_is_refused(value: str) -> None:
    with pytest.raises(ConfigurationError, match="is not one of"):
        Settings.from_env({"TOURGANIZE_OPTION_SOURCE_PROFILE": value})


def test_the_profile_key_accepts_the_values_later_features_deliver() -> None:
    """`world` and `live` resolve here and are refused by the Composition Root, which names
    the feature — the same bargain ``TOURGANIZE_INTERPRETER=model`` makes."""
    for name in ("fixture", "world", "live"):
        assert (
            Settings.from_env(
                {"TOURGANIZE_OPTION_SOURCE_PROFILE": name}
            ).option_source_profile.default
            == name
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("true", True),
        ("yes", True),
        ("1", True),
        ("on", True),
        ("false", False),
        ("no", False),
        ("0", False),
        ("OFF", False),
    ],
)
def test_strict_filtering_reads_the_obvious_spellings(value: str, expected: bool) -> None:
    settings = Settings.from_env({"TOURGANIZE_OPTION_FILTER_STRICT": value})

    assert settings.option_filter_strict is expected


def test_a_value_that_is_not_a_boolean_is_refused_rather_than_coerced() -> None:
    """Reading "maybe" as truthy is exactly the setting that appears honoured and is not."""
    with pytest.raises(ConfigurationError, match="is not a boolean"):
        Settings.from_env({"TOURGANIZE_OPTION_FILTER_STRICT": "maybe"})


def test_the_slate_size_is_a_count_and_is_refused_below_one() -> None:
    assert Settings.from_env({"TOURGANIZE_SLATE_SIZE": "5"}).slate_size == 5

    with pytest.raises(ConfigurationError, match="must be at least 1"):
        Settings.from_env({"TOURGANIZE_SLATE_SIZE": "0"})


def test_the_source_timeout_is_a_number_of_seconds_above_zero() -> None:
    assert (
        Settings.from_env(
            {"TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS": "2.5"}
        ).option_source_timeout_seconds
        == 2.5
    )

    with pytest.raises(ConfigurationError, match="must be above zero"):
        Settings.from_env({"TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS": "0"})

    with pytest.raises(ConfigurationError, match="not a number of seconds"):
        Settings.from_env({"TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS": "soon"})


def test_doctor_reads_the_profile_through_describe() -> None:
    """``doctor`` prints ``Settings.describe()``, so the parsed profile has to render there."""
    described = Settings.from_env(
        {"TOURGANIZE_OPTION_SOURCE_PROFILE": "alpha=fixture,beta=fixture"}
    ).describe()

    assert described["option_source_profile"] == "alpha=fixture,beta=fixture (default fixture)"
    assert described["slate_size"] == "3"
    assert described["option_filter_strict"] == "false"
    assert described["option_source_timeout_seconds"] == "10"


def test_doctor_reads_the_surface_and_the_locales_through_describe() -> None:
    """``doctor`` prints ``Settings.describe()``, and F07's DoD asks it to report the surface,
    the locale and the message directory — so all three have to render there."""
    described = Settings.from_env(
        {
            "TOURGANIZE_SURFACE": "scripted",
            "TOURGANIZE_MESSAGE_DIR": "/etc/wording",
            "TOURGANIZE_SUPPORTED_LOCALES": "he,en",
            "TOURGANIZE_DEFAULT_LOCALE": "he",
        }
    ).describe()

    assert described["surface"] == "scripted"
    assert described["message_dir"] == "/etc/wording"
    assert described["default_locale"] == "he"
    assert described["supported_locales"] == "he,en"
