"""Fixtures shared by every test. Conventions are documented in ``tests/README.md``."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.domain.options import Money, PlanOption, Provenance
from tourganize.platform.settings import Settings

SettingsFactory = Callable[..., Settings]
OptionFactory = Callable[..., PlanOption]

#: A valid Component Catalog with neutral keys. The shipped catalog names travel topics; a
#: test about the *machinery* should not have to name one, and neutral keys keep the rule that
#: no topic string appears in the package easy to see. ``gamma`` is disabled on purpose.
SAMPLE_CATALOG: Final = """\
version: 1
kinds:
  - kind_key: alpha
    message_key: component.alpha
    priority_weight: 300
    schema_key: alpha.v1
    requires_outcome_of: []
    enabled: true
  - kind_key: beta
    message_key: component.beta
    priority_weight: 200
    schema_key: beta.v1
    requires_outcome_of: [alpha]
    enabled: true
  - kind_key: gamma
    message_key: component.gamma
    priority_weight: 100
    schema_key: gamma.v1
    enabled: false
"""


def write_catalog(config_dir: Path, text: str = SAMPLE_CATALOG) -> Path:
    """Write a Component Catalog where ``Settings`` expects to find one, and return its path."""
    path = config_dir / "catalog" / "components.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


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
def catalog_file(tmp_path: Path) -> Path:
    """A valid Component Catalog inside the config directory ``settings_factory`` points at.

    Requesting this fixture alongside ``settings_factory`` is how a test says "a healthy
    installation": from F02 on, an installation without a catalog cannot plan anything.
    """
    return write_catalog(tmp_path / "config")


@pytest.fixture
def frozen_clock() -> FrozenClock:
    """A clock pinned to :data:`DEFAULT_MOMENT` that only moves when a test moves it."""
    return FrozenClock(DEFAULT_MOMENT)


@pytest.fixture
def option_factory(frozen_clock: FrozenClock) -> OptionFactory:
    """Build a ``PlanOption`` with plausible Provenance, naming only what a test cares about.

    ``option_factory("a1", price=Money(74000, "EUR"), nights=5)`` is a priced option of kind
    ``alpha``; every keyword that is not ``kind_key`` or ``price`` becomes a declared fact.
    """
    moment = frozen_clock.now()

    def factory(
        option_id: str,
        kind_key: str = "alpha",
        *,
        price: Money | None = None,
        **facts: object,
    ) -> PlanOption:
        return PlanOption(
            option_id=option_id,
            kind_key=kind_key,
            facts=facts,
            price=price,
            provenance=Provenance(source_id=f"fixture:{kind_key}", retrieved_at=moment),
        )

    return factory
