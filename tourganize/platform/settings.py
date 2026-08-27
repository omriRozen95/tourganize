"""Typed, validated configuration.

One frozen :class:`Settings` object is built once, by :meth:`Settings.from_env`, from a
mapping (normally ``os.environ``). Every key is ``TOURGANIZE_*`` and has a documented
default; anything invalid raises :class:`~tourganize.platform.errors.ConfigurationError`
at construction time rather than at first use.

Later features append fields and rows to :data:`KNOWN_KEYS` — they never invent a second
way of loading configuration.

| Key | Meaning | Default |
|---|---|---|
| ``TOURGANIZE_ENV`` | Runtime profile: ``dev``/``test``/``prod`` | ``dev`` |
| ``TOURGANIZE_LOG_LEVEL`` | Python log level name | ``INFO`` |
| ``TOURGANIZE_LOG_FORMAT`` | ``json`` or ``human`` | ``human`` in dev, ``json`` otherwise |
| ``TOURGANIZE_CONFIG_DIR`` | Root of ``catalog/``, ``prompts/``, ``messages/`` | ``config`` |
| ``TOURGANIZE_CATALOG_PATH`` | Component Catalog file | ``$CONFIG_DIR/catalog/components.yaml`` |
| ``TOURGANIZE_SCHEMA_DIR`` | Requirement Schema files | ``$CONFIG_DIR/catalog/schemas`` |
| ``TOURGANIZE_DATA_DIR`` | Writable state (sessions, exports, indexes) | ``var`` |
| ``TOURGANIZE_SECRETS_FILE`` | Optional ``KEY=value`` file merged *under* the environment | unset |
| ``TOURGANIZE_TELEMETRY_SINK`` | ``null`` or ``jsonl`` | ``jsonl`` |
| ``TOURGANIZE_TELEMETRY_PATH`` | Where the JSONL sink writes | ``$DATA_DIR/telemetry.jsonl`` |
| ``TOURGANIZE_PRIORITY_POLICY`` | ``weighted`` or ``fixed`` | ``weighted`` |
| ``TOURGANIZE_AGENDA_FAILURE_SKIP`` | Failures in a row before a Kind is skipped | ``2`` |
| ``TOURGANIZE_DIALOGUE_MAX_REASKS`` | Asks on one Blocking Rule before giving up | ``3`` |
| ``TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT`` | Optional fields in one ``ask_optional`` | ``2`` |
| ``TOURGANIZE_DIALOGUE_OFFER_BATCH`` | Kinds named in one ``offer_unmentioned`` | ``2`` |
| ``TOURGANIZE_INTERPRETER`` | Turn Interpreter: ``keyword`` or ``model`` | ``keyword`` |
| ``TOURGANIZE_KEYWORD_CONFIG_DIR`` | Keyword phrase tables | ``$CONFIG_DIR/interpretation`` |
| ``TOURGANIZE_OPTION_SOURCE_PROFILE`` | Source Profile, or per-kind overrides | ``fixture`` |
| ``TOURGANIZE_FIXTURE_DIR`` | Root of fixture option data | ``fixtures/options`` |
| ``TOURGANIZE_SLATE_SIZE`` | Options presented per round | ``3`` |
| ``TOURGANIZE_OPTION_FILTER_STRICT`` | Optional filters discard instead of demote | ``false`` |
| ``TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS`` | Per-source time budget | ``10`` |

Three keys are worth a word on. ``TOURGANIZE_CATALOG_PATH`` and ``TOURGANIZE_SCHEMA_DIR``
follow ``TOURGANIZE_CONFIG_DIR`` unless they are set explicitly, so moving the configuration
directory moves the catalog and its Requirement Schemas with it; a catalog or a schema that is
not *there* is not an error here, because a missing file is a runtime condition that
``doctor`` reports and the command that needs it refuses.
``TOURGANIZE_TELEMETRY_PATH`` defaults to its documented value whichever sink is selected —
the ``null`` sink simply never writes there — so nothing downstream has to re-derive it. And a
secrets file may only set ``TOURGANIZE_*`` keys: a stray key is refused rather than ignored,
because a secret believed to be loaded is worse than one that is missing.

``TOURGANIZE_AGENDA_FAILURE_SKIP`` takes its default from the domain
(:data:`~tourganize.domain.catalog.DEFAULT_AGENDA_FAILURE_SKIP`) rather than spelling a
second ``2`` here: the rule it configures lives there, and a documented default has one
definition. Anything below 1 is refused — a Component Kind skipped before it has failed even
once could never be planned at all. The three ``TOURGANIZE_DIALOGUE_*`` counts follow the same
convention, from :mod:`tourganize.dialogue.settings`, and are refused below 1 for the same
reason: a re-ask limit of zero is a question nobody ever asks.

``TOURGANIZE_INTERPRETER`` names a *value set* wider than what this release can build.
``keyword`` is wired; ``model`` is F08's, and asking for it is a
:class:`~tourganize.platform.errors.ConfigurationError` raised by the Composition Root, which
names the feature that delivers it. The alternative — leaving ``model`` out of the choice
list — would answer "not one of 'keyword'", which is true and useless.
``TOURGANIZE_OPTION_SOURCE_PROFILE`` follows exactly that convention for ``world`` (F17) and
``live`` (F24), and adds one thing of its own: it accepts either a single profile name for
every Component Kind, or a comma-separated list of ``kind_key=profile`` overrides, because a
client with an account for one topic and none for another has to be able to mix them. See
:class:`OptionSourceProfile`.

``TOURGANIZE_DEFAULT_LOCALE`` is checked against ``TOURGANIZE_SUPPORTED_LOCALES`` here rather
than at first use: a default nobody supports is a Message Catalogue that will never be found,
and finding that out when a traveller has already typed something is too late. The supported
list is refused empty and de-duplicated in the order it was written, because it is also the
list ``doctor`` probes and the order a report reads in.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, TypeVar

from tourganize.dialogue.settings import (
    DEFAULT_MAX_REASKS,
    DEFAULT_OFFER_BATCH,
    DEFAULT_OPTIONAL_ASK_LIMIT,
)
from tourganize.domain.catalog import DEFAULT_AGENDA_FAILURE_SKIP
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.secrets import REDACTED, SecretValue

__all__ = [
    "KNOWN_KEYS",
    "SECRET_KEY_SUFFIXES",
    "Env",
    "InterpreterName",
    "LogFormat",
    "OptionSourceProfile",
    "PriorityPolicyName",
    "Settings",
    "SourceProfileName",
    "SurfaceName",
    "TelemetrySinkName",
    "default_catalog_path",
    "default_keyword_config_dir",
    "default_message_dir",
    "default_schema_dir",
    "default_telemetry_path",
    "unrecognised_keys",
]

Env = Literal["dev", "test", "prod"]
LogFormat = Literal["json", "human"]
TelemetrySinkName = Literal["null", "jsonl"]
PriorityPolicyName = Literal["weighted", "fixed"]
InterpreterName = Literal["keyword", "model"]
SourceProfileName = Literal["fixture", "world", "live"]
SurfaceName = Literal["terminal", "scripted"]

PREFIX: Final = "TOURGANIZE_"

_ENV_VALUES: Final[tuple[Env, ...]] = ("dev", "test", "prod")
_LOG_FORMATS: Final[tuple[LogFormat, ...]] = ("json", "human")
_TELEMETRY_SINKS: Final[tuple[TelemetrySinkName, ...]] = ("null", "jsonl")
_PRIORITY_POLICIES: Final[tuple[PriorityPolicyName, ...]] = ("weighted", "fixed")
_INTERPRETERS: Final[tuple[InterpreterName, ...]] = ("keyword", "model")
_SOURCE_PROFILES: Final[tuple[SourceProfileName, ...]] = ("fixture", "world", "live")
_SURFACES: Final[tuple[SurfaceName, ...]] = ("terminal", "scripted")

DEFAULT_CONFIG_DIR: Final = Path("config")
DEFAULT_DATA_DIR: Final = Path("var")
DEFAULT_LOG_LEVEL: Final = "INFO"
TELEMETRY_FILENAME: Final = "telemetry.jsonl"
CATALOG_RELATIVE_PATH: Final = Path("catalog") / "components.yaml"
SCHEMAS_RELATIVE_PATH: Final = Path("catalog") / "schemas"
INTERPRETATION_RELATIVE_PATH: Final = Path("interpretation")
DEFAULT_FIXTURE_DIR: Final = Path("fixtures") / "options"
DEFAULT_SLATE_SIZE: Final = 3
DEFAULT_SOURCE_TIMEOUT_SECONDS: Final = 10.0
MESSAGES_RELATIVE_PATH: Final = Path("messages")
#: The Locale Tags this release ships a Message Catalogue for. English is the code and
#: documentation language; Hebrew is a first-class *content* language, and shipping it from
#: the first surface is what keeps the RTL path from being retrofitted (F10).
DEFAULT_SUPPORTED_LOCALES: Final = ("en", "he")

#: A ``TOURGANIZE_*`` key ending in one of these is treated as a secret: it is wrapped in
#: :class:`SecretValue` and never rendered by ``doctor`` or the logs.
SECRET_KEY_SUFFIXES: Final = (
    "_API_KEY",
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIALS",
)

_ChoiceT = TypeVar("_ChoiceT", bound=str)

#: The spellings a boolean-valued key accepts, either way.
_TRUE_VALUES: Final = frozenset({"true", "yes", "1", "on"})
_FALSE_VALUES: Final = frozenset({"false", "no", "0", "off"})

#: Every non-secret key this version understands. ``doctor`` reports ``TOURGANIZE_*`` keys
#: that are in neither this set nor the secret convention, which catches typos.
KNOWN_KEYS: Final = frozenset(
    {
        "TOURGANIZE_ENV",
        "TOURGANIZE_LOG_LEVEL",
        "TOURGANIZE_LOG_FORMAT",
        "TOURGANIZE_CONFIG_DIR",
        "TOURGANIZE_CATALOG_PATH",
        "TOURGANIZE_SCHEMA_DIR",
        "TOURGANIZE_DATA_DIR",
        "TOURGANIZE_SECRETS_FILE",
        "TOURGANIZE_TELEMETRY_SINK",
        "TOURGANIZE_TELEMETRY_PATH",
        "TOURGANIZE_PRIORITY_POLICY",
        "TOURGANIZE_AGENDA_FAILURE_SKIP",
        "TOURGANIZE_DIALOGUE_MAX_REASKS",
        "TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT",
        "TOURGANIZE_DIALOGUE_OFFER_BATCH",
        "TOURGANIZE_INTERPRETER",
        "TOURGANIZE_KEYWORD_CONFIG_DIR",
        "TOURGANIZE_OPTION_SOURCE_PROFILE",
        "TOURGANIZE_FIXTURE_DIR",
        "TOURGANIZE_SLATE_SIZE",
        "TOURGANIZE_OPTION_FILTER_STRICT",
        "TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS",
        "TOURGANIZE_SURFACE",
        "TOURGANIZE_MESSAGE_DIR",
        "TOURGANIZE_DEFAULT_LOCALE",
        "TOURGANIZE_SUPPORTED_LOCALES",
    }
)


@dataclass(frozen=True, slots=True)
class OptionSourceProfile:
    """Which Option Sources serve which Component Kinds — one value of one key.

    ``TOURGANIZE_OPTION_SOURCE_PROFILE=fixture`` sets the profile for everything.
    ``TOURGANIZE_OPTION_SOURCE_PROFILE=alpha=live,beta=fixture`` sets it per Component
    Kind, with :attr:`default` for the Kinds nobody named. Mixing the two spellings in one
    value is refused: ``fixture,alpha=live`` reads as though the bare word were a fallback,
    and a configuration that *looks* like it means something it does not is worse than one that
    is rejected.

    ``kind_key``s are not validated against the Component Catalog here. Settings are resolved
    before any file is read — that is the whole reason ``doctor`` exists — so an override for a
    Kind nobody declares is reported by ``doctor``, beside everything else that is wrong with
    the installation, rather than making every command exit 3.
    """

    default: SourceProfileName = "fixture"
    per_kind: Mapping[str, SourceProfileName] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "per_kind", MappingProxyType(dict(self.per_kind)))

    def for_kind(self, kind_key: str) -> SourceProfileName:
        """The profile in force for ``kind_key``."""
        return self.per_kind.get(kind_key, self.default)

    @property
    def names(self) -> tuple[SourceProfileName, ...]:
        """Every profile this value can select, in a stable order — what has to be buildable."""
        return tuple(sorted({self.default, *self.per_kind.values()}))

    def describe(self) -> str:
        """How ``doctor`` prints it: the value as written, normalised."""
        if not self.per_kind:
            return self.default
        overrides = ",".join(f"{key}={self.per_kind[key]}" for key in sorted(self.per_kind))
        return f"{overrides} (default {self.default})"


@dataclass(frozen=True, slots=True)
class Settings:
    """The resolved configuration of one process."""

    env: Env
    log_level: str
    log_format: LogFormat
    config_dir: Path
    catalog_path: Path
    schema_dir: Path
    data_dir: Path
    telemetry_sink: TelemetrySinkName
    telemetry_path: Path | None
    priority_policy: PriorityPolicyName
    agenda_failure_skip: int
    dialogue_max_reasks: int
    dialogue_optional_ask_limit: int
    dialogue_offer_batch: int
    interpreter: InterpreterName
    keyword_config_dir: Path
    option_source_profile: OptionSourceProfile
    fixture_dir: Path
    slate_size: int
    option_filter_strict: bool
    option_source_timeout_seconds: float
    surface: SurfaceName
    message_dir: Path
    default_locale: str
    supported_locales: tuple[str, ...]
    secrets_file: Path | None = None
    secrets: Mapping[str, SecretValue] = field(default_factory=dict)
    # Later features append fields here; they never re-invent loading.

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Settings:
        """Build settings from ``environ``, merging any secrets file underneath it."""
        secrets_file = _file(environ, "TOURGANIZE_SECRETS_FILE", None)
        merged = _merge(_read_secrets_file(secrets_file), environ)

        env = _choice(merged, "TOURGANIZE_ENV", _ENV_VALUES, "dev")
        log_format_default: LogFormat = "human" if env == "dev" else "json"
        sink = _choice(merged, "TOURGANIZE_TELEMETRY_SINK", _TELEMETRY_SINKS, "jsonl")
        data_dir = _directory(merged, "TOURGANIZE_DATA_DIR", DEFAULT_DATA_DIR)
        config_dir = _directory(merged, "TOURGANIZE_CONFIG_DIR", DEFAULT_CONFIG_DIR)
        locales = _locale_tags(merged, "TOURGANIZE_SUPPORTED_LOCALES", DEFAULT_SUPPORTED_LOCALES)
        default_locale = _supported_locale(
            merged, "TOURGANIZE_DEFAULT_LOCALE", locales, "TOURGANIZE_SUPPORTED_LOCALES"
        )

        return cls(
            env=env,
            log_level=_log_level(merged, "TOURGANIZE_LOG_LEVEL", DEFAULT_LOG_LEVEL),
            log_format=_choice(merged, "TOURGANIZE_LOG_FORMAT", _LOG_FORMATS, log_format_default),
            config_dir=config_dir,
            catalog_path=_required_file(
                merged, "TOURGANIZE_CATALOG_PATH", default_catalog_path(config_dir)
            ),
            schema_dir=_directory(merged, "TOURGANIZE_SCHEMA_DIR", default_schema_dir(config_dir)),
            data_dir=data_dir,
            telemetry_sink=sink,
            telemetry_path=_file(
                merged, "TOURGANIZE_TELEMETRY_PATH", default_telemetry_path(data_dir)
            ),
            priority_policy=_choice(
                merged, "TOURGANIZE_PRIORITY_POLICY", _PRIORITY_POLICIES, "weighted"
            ),
            agenda_failure_skip=_at_least_one(
                merged, "TOURGANIZE_AGENDA_FAILURE_SKIP", DEFAULT_AGENDA_FAILURE_SKIP
            ),
            dialogue_max_reasks=_at_least_one(
                merged, "TOURGANIZE_DIALOGUE_MAX_REASKS", DEFAULT_MAX_REASKS
            ),
            dialogue_optional_ask_limit=_at_least_one(
                merged, "TOURGANIZE_DIALOGUE_OPTIONAL_ASK_LIMIT", DEFAULT_OPTIONAL_ASK_LIMIT
            ),
            dialogue_offer_batch=_at_least_one(
                merged, "TOURGANIZE_DIALOGUE_OFFER_BATCH", DEFAULT_OFFER_BATCH
            ),
            interpreter=_choice(merged, "TOURGANIZE_INTERPRETER", _INTERPRETERS, "keyword"),
            keyword_config_dir=_directory(
                merged,
                "TOURGANIZE_KEYWORD_CONFIG_DIR",
                default_keyword_config_dir(config_dir),
            ),
            option_source_profile=_source_profile(merged, "TOURGANIZE_OPTION_SOURCE_PROFILE"),
            fixture_dir=_directory(merged, "TOURGANIZE_FIXTURE_DIR", DEFAULT_FIXTURE_DIR),
            slate_size=_at_least_one(merged, "TOURGANIZE_SLATE_SIZE", DEFAULT_SLATE_SIZE),
            option_filter_strict=_flag(merged, "TOURGANIZE_OPTION_FILTER_STRICT", False),
            option_source_timeout_seconds=_positive_number(
                merged,
                "TOURGANIZE_OPTION_SOURCE_TIMEOUT_SECONDS",
                DEFAULT_SOURCE_TIMEOUT_SECONDS,
            ),
            surface=_choice(merged, "TOURGANIZE_SURFACE", _SURFACES, "terminal"),
            message_dir=_directory(
                merged, "TOURGANIZE_MESSAGE_DIR", default_message_dir(config_dir)
            ),
            default_locale=locales[0] if default_locale is None else default_locale,
            supported_locales=locales,
            secrets_file=secrets_file,
            secrets=_collect_secrets(merged),
        )

    def describe(self) -> Mapping[str, str]:
        """Return every setting as a display string, with secret values redacted.

        This is the single renderer of settings for humans: ``doctor`` and the logs both go
        through it, so a secret cannot leak by one of them formatting the object directly.
        """
        described = {
            "env": self.env,
            "log_level": self.log_level,
            "log_format": self.log_format,
            "config_dir": str(self.config_dir),
            "catalog_path": str(self.catalog_path),
            "schema_dir": str(self.schema_dir),
            "data_dir": str(self.data_dir),
            "telemetry_sink": self.telemetry_sink,
            "telemetry_path": "unset" if self.telemetry_path is None else str(self.telemetry_path),
            "priority_policy": self.priority_policy,
            "agenda_failure_skip": str(self.agenda_failure_skip),
            "dialogue_max_reasks": str(self.dialogue_max_reasks),
            "dialogue_optional_ask_limit": str(self.dialogue_optional_ask_limit),
            "dialogue_offer_batch": str(self.dialogue_offer_batch),
            "interpreter": self.interpreter,
            "keyword_config_dir": str(self.keyword_config_dir),
            "option_source_profile": self.option_source_profile.describe(),
            "fixture_dir": str(self.fixture_dir),
            "slate_size": str(self.slate_size),
            "option_filter_strict": "true" if self.option_filter_strict else "false",
            "option_source_timeout_seconds": _plain_seconds(self.option_source_timeout_seconds),
            "surface": self.surface,
            "message_dir": str(self.message_dir),
            "default_locale": self.default_locale,
            "supported_locales": ",".join(self.supported_locales),
            "secrets_file": "unset" if self.secrets_file is None else str(self.secrets_file),
            "secrets": _describe_secrets(self.secrets),
        }
        return MappingProxyType(described)


def default_telemetry_path(data_dir: Path) -> Path:
    """Where the JSONL sink writes unless ``TOURGANIZE_TELEMETRY_PATH`` says otherwise.

    The Composition Root needs this too, so it lives here rather than being spelled out
    twice: the documented default has one definition.
    """
    return data_dir / TELEMETRY_FILENAME


def default_catalog_path(config_dir: Path) -> Path:
    """Where the Component Catalog lives unless ``TOURGANIZE_CATALOG_PATH`` says otherwise."""
    return config_dir / CATALOG_RELATIVE_PATH


def default_schema_dir(config_dir: Path) -> Path:
    """Where Requirement Schemas live unless ``TOURGANIZE_SCHEMA_DIR`` says otherwise."""
    return config_dir / SCHEMAS_RELATIVE_PATH


def default_message_dir(config_dir: Path) -> Path:
    """Where the Message Catalogue and Display Profiles live, absent an explicit setting."""
    return config_dir / MESSAGES_RELATIVE_PATH


def default_keyword_config_dir(config_dir: Path) -> Path:
    """Where the keyword interpreter's phrase tables live, absent an explicit setting."""
    return config_dir / INTERPRETATION_RELATIVE_PATH


def unrecognised_keys(environ: Mapping[str, str]) -> tuple[str, ...]:
    """Return the ``TOURGANIZE_*`` keys this version neither reads nor treats as secret."""
    return tuple(
        sorted(
            key
            for key in environ
            if key.startswith(PREFIX) and key not in KNOWN_KEYS and not _is_secret_key(key)
        )
    )


def _describe_secrets(secrets: Mapping[str, SecretValue]) -> str:
    if not secrets:
        return "none loaded"
    names = ", ".join(f"{key}={REDACTED}" for key in sorted(secrets))
    return f"{len(secrets)} loaded ({names})"


def _is_secret_key(key: str) -> bool:
    return key.startswith(PREFIX) and key.endswith(SECRET_KEY_SUFFIXES)


def _collect_secrets(merged: Mapping[str, str]) -> Mapping[str, SecretValue]:
    found = {key: SecretValue(value) for key, value in merged.items() if _is_secret_key(key)}
    return MappingProxyType(found)


def _merge(under: Mapping[str, str], over: Mapping[str, str]) -> Mapping[str, str]:
    merged = dict(under)
    merged.update(over)
    return merged


def _read_secrets_file(path: Path | None) -> Mapping[str, str]:
    """Parse a ``KEY=value`` file. Comments (``#``) and blank lines are ignored."""
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"TOURGANIZE_SECRETS_FILE={path} could not be read: {exc.strerror or exc}"
        ) from exc

    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(
                f"TOURGANIZE_SECRETS_FILE={path} line {number} is not KEY=value: {raw!r}"
            )
        key, _, value = line.partition("=")
        name = key.strip()
        if not name.startswith(PREFIX):
            raise ConfigurationError(
                f"TOURGANIZE_SECRETS_FILE={path} line {number} sets {name!r}, which is not a "
                f"{PREFIX}* key. Every setting this application reads is {PREFIX}*, so a key "
                f"without that prefix would be loaded and never used."
            )
        values[name] = value.strip().strip("'\"")
    return values


def _raw(environ: Mapping[str, str], key: str) -> str | None:
    value = environ.get(key)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _choice(
    environ: Mapping[str, str],
    key: str,
    allowed: Sequence[_ChoiceT],
    default: _ChoiceT,
) -> _ChoiceT:
    value = _raw(environ, key)
    if value is None:
        return default
    for candidate in allowed:
        if value == candidate:
            return candidate
    raise ConfigurationError(
        f"{key}={value!r} is not one of {', '.join(repr(item) for item in allowed)}"
    )


def _at_least_one(environ: Mapping[str, str], key: str, default: int) -> int:
    """A count-valued key: a whole number, one or more, or a refusal naming the key."""
    value = _raw(environ, key)
    if value is None:
        return default
    try:
        number = int(value)
    except ValueError:
        raise ConfigurationError(f"{key}={value!r} is not a whole number") from None
    if number < 1:
        raise ConfigurationError(f"{key}={number} must be at least 1")
    return number


def _flag(environ: Mapping[str, str], key: str, default: bool) -> bool:
    """A boolean-valued key. ``true``/``false`` and their obvious spellings, nothing else.

    Refused rather than coerced: the classic reading of ``"false"`` as truthy is exactly the
    kind of setting that appears to be honoured and is not, and ``TOURGANIZE_OPTION_FILTER_STRICT``
    decides whether a traveller sees options at all.
    """
    value = _raw(environ, key)
    if value is None:
        return default
    lowered = value.lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{key}={value!r} is not a boolean; write one of "
        f"{', '.join(sorted(_TRUE_VALUES | _FALSE_VALUES))}"
    )


def _positive_number(environ: Mapping[str, str], key: str, default: float) -> float:
    """A seconds-valued key: a number above zero, or a refusal naming the key."""
    value = _raw(environ, key)
    if value is None:
        return default
    try:
        number = float(value)
    except ValueError:
        raise ConfigurationError(f"{key}={value!r} is not a number of seconds") from None
    if number <= 0:
        raise ConfigurationError(f"{key}={value} must be above zero")
    return number


def _locale_tags(environ: Mapping[str, str], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """A comma-separated list of Locale Tags: refused empty, de-duplicated in written order."""
    value = _raw(environ, key)
    if value is None:
        return default
    seen: list[str] = []
    for item in value.split(","):
        tag = item.strip()
        if tag and tag not in seen:
            seen.append(tag)
    if not seen:
        raise ConfigurationError(
            f"{key}={value!r} names no Locale Tag; write a comma-separated list such as "
            f"{','.join(default)!r}"
        )
    return tuple(seen)


def _supported_locale(
    environ: Mapping[str, str], key: str, supported: tuple[str, ...], supported_key: str
) -> str | None:
    """One Locale Tag that ``supported`` has to contain, or ``None`` when nothing is set."""
    value = _raw(environ, key)
    if value is None:
        return None
    if value not in supported:
        raise ConfigurationError(
            f"{key}={value!r} is not in {supported_key}={','.join(supported)}; a default locale "
            f"nobody supports is a Message Catalogue that will never be found"
        )
    return value


def _source_profile(environ: Mapping[str, str], key: str) -> OptionSourceProfile:
    """Read ``fixture`` or ``k1=profile,k2=profile`` into an :class:`OptionSourceProfile`."""
    value = _raw(environ, key)
    if value is None:
        return OptionSourceProfile()
    entries = [item.strip() for item in value.split(",") if item.strip()]
    if not entries:
        return OptionSourceProfile()
    if not any("=" in entry for entry in entries):
        if len(entries) > 1:
            raise ConfigurationError(
                f"{key}={value!r} names {len(entries)} profiles for every Component Kind; "
                f"write one profile, or one kind_key=profile per Kind"
            )
        return OptionSourceProfile(default=_profile_name(key, entries[0]))
    per_kind: dict[str, SourceProfileName] = {}
    for entry in entries:
        kind_key, separator, name = entry.partition("=")
        if not separator or not kind_key.strip():
            raise ConfigurationError(
                f"{key}={value!r} mixes a bare profile with per-kind overrides at {entry!r}; "
                f"write every entry as kind_key=profile once one of them is"
            )
        per_kind[kind_key.strip()] = _profile_name(f"{key} [{kind_key.strip()}]", name.strip())
    return OptionSourceProfile(per_kind=per_kind)


def _profile_name(key: str, value: str) -> SourceProfileName:
    for candidate in _SOURCE_PROFILES:
        if value == candidate:
            return candidate
    raise ConfigurationError(
        f"{key}={value!r} is not one of {', '.join(repr(item) for item in _SOURCE_PROFILES)}"
    )


def _plain_seconds(seconds: float) -> str:
    """Render a seconds value without a trailing ``.0`` nobody wrote."""
    return f"{seconds:g}"


def _log_level(environ: Mapping[str, str], key: str, default: str) -> str:
    value = _raw(environ, key)
    if value is None:
        return default
    name = value.upper()
    if name not in logging.getLevelNamesMapping():
        raise ConfigurationError(f"{key}={value!r} is not a known log level name")
    return name


def _path(environ: Mapping[str, str], key: str) -> Path | None:
    value = _raw(environ, key)
    if value is None:
        return None
    return Path(value).expanduser()


def _directory(environ: Mapping[str, str], key: str, default: Path) -> Path:
    path = _path(environ, key)
    if path is None:
        return default
    if path.exists() and not path.is_dir():
        raise ConfigurationError(f"{key}={path} exists but is not a directory")
    return path


def _file(environ: Mapping[str, str], key: str, default: Path | None) -> Path | None:
    path = _path(environ, key)
    if path is None:
        return default
    if path.exists() and not path.is_file():
        raise ConfigurationError(f"{key}={path} exists but is not a file")
    return path


def _required_file(environ: Mapping[str, str], key: str, default: Path) -> Path:
    """A file-valued key that always resolves — absence is a runtime concern, not a setting.

    A catalog that is missing is reported by ``doctor`` and refused by the command that needs
    it. Refusing it here would make every ``tourganize --version`` in a fresh checkout an
    exit 3.
    """
    resolved = _file(environ, key, default)
    return default if resolved is None else resolved
