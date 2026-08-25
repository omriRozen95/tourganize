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
| ``TOURGANIZE_DATA_DIR`` | Writable state (sessions, exports, indexes) | ``var`` |
| ``TOURGANIZE_SECRETS_FILE`` | Optional ``KEY=value`` file merged *under* the environment | unset |
| ``TOURGANIZE_TELEMETRY_SINK`` | ``null`` or ``jsonl`` | ``jsonl`` |
| ``TOURGANIZE_TELEMETRY_PATH`` | Where the JSONL sink writes | ``$DATA_DIR/telemetry.jsonl`` |

Two keys are worth a word on. ``TOURGANIZE_TELEMETRY_PATH`` defaults to its documented value
whichever sink is selected — the ``null`` sink simply never writes there — so nothing
downstream has to re-derive it. And a secrets file may only set ``TOURGANIZE_*`` keys: a
stray key is refused rather than ignored, because a secret believed to be loaded is worse
than one that is missing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal, TypeVar

from tourganize.platform.errors import ConfigurationError
from tourganize.platform.secrets import REDACTED, SecretValue

__all__ = [
    "KNOWN_KEYS",
    "SECRET_KEY_SUFFIXES",
    "Env",
    "LogFormat",
    "Settings",
    "TelemetrySinkName",
    "default_telemetry_path",
    "unrecognised_keys",
]

Env = Literal["dev", "test", "prod"]
LogFormat = Literal["json", "human"]
TelemetrySinkName = Literal["null", "jsonl"]

PREFIX: Final = "TOURGANIZE_"

_ENV_VALUES: Final[tuple[Env, ...]] = ("dev", "test", "prod")
_LOG_FORMATS: Final[tuple[LogFormat, ...]] = ("json", "human")
_TELEMETRY_SINKS: Final[tuple[TelemetrySinkName, ...]] = ("null", "jsonl")

DEFAULT_CONFIG_DIR: Final = Path("config")
DEFAULT_DATA_DIR: Final = Path("var")
DEFAULT_LOG_LEVEL: Final = "INFO"
TELEMETRY_FILENAME: Final = "telemetry.jsonl"

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

#: Every non-secret key this version understands. ``doctor`` reports ``TOURGANIZE_*`` keys
#: that are in neither this set nor the secret convention, which catches typos.
KNOWN_KEYS: Final = frozenset(
    {
        "TOURGANIZE_ENV",
        "TOURGANIZE_LOG_LEVEL",
        "TOURGANIZE_LOG_FORMAT",
        "TOURGANIZE_CONFIG_DIR",
        "TOURGANIZE_DATA_DIR",
        "TOURGANIZE_SECRETS_FILE",
        "TOURGANIZE_TELEMETRY_SINK",
        "TOURGANIZE_TELEMETRY_PATH",
    }
)


@dataclass(frozen=True, slots=True)
class Settings:
    """The resolved configuration of one process."""

    env: Env
    log_level: str
    log_format: LogFormat
    config_dir: Path
    data_dir: Path
    telemetry_sink: TelemetrySinkName
    telemetry_path: Path | None
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

        return cls(
            env=env,
            log_level=_log_level(merged, "TOURGANIZE_LOG_LEVEL", DEFAULT_LOG_LEVEL),
            log_format=_choice(merged, "TOURGANIZE_LOG_FORMAT", _LOG_FORMATS, log_format_default),
            config_dir=_directory(merged, "TOURGANIZE_CONFIG_DIR", DEFAULT_CONFIG_DIR),
            data_dir=data_dir,
            telemetry_sink=sink,
            telemetry_path=_file(
                merged, "TOURGANIZE_TELEMETRY_PATH", default_telemetry_path(data_dir)
            ),
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
            "data_dir": str(self.data_dir),
            "telemetry_sink": self.telemetry_sink,
            "telemetry_path": "unset" if self.telemetry_path is None else str(self.telemetry_path),
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
