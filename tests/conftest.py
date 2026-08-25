"""Fixtures shared by every test. Conventions are documented in ``tests/README.md``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.platform.settings import Settings

SettingsFactory = Callable[..., Settings]


@pytest.fixture
def settings_factory(tmp_path: Path) -> SettingsFactory:
    """Build ``Settings`` whose directories live inside this test's own ``tmp_path``.

    Keyword arguments are environment keys, so a test overrides exactly what it is about::

        settings = settings_factory(TOURGANIZE_TELEMETRY_SINK="null")
    """

    def factory(**overrides: str) -> Settings:
        environ = {
            "TOURGANIZE_ENV": "test",
            "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
            "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
        }
        environ.update(overrides)
        return Settings.from_env(environ)

    return factory


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A clock pinned to :data:`DEFAULT_MOMENT` that only moves when a test moves it."""
    return FrozenClock(DEFAULT_MOMENT)
