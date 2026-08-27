"""Fixtures shared by every test. Conventions are documented in ``tests/README.md``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
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


#: The Requirement Schemas of the two *enabled* kinds of :data:`SAMPLE_CATALOG`, keyed by
#: ``schema_key`` exactly as the file names are. ``alpha.v1`` is the interesting one: it carries
#: a blocking rule with two candidate groups, which is the shape the client's own "a range, or
#: a start and an end" rule needs. ``gamma`` is disabled and deliberately has no schema — a
#: kind nobody can plan does not need one, and `catalog validate` must not ask for it.
SAMPLE_SCHEMAS: Final[Mapping[str, str]] = {
    "alpha.v1": """\
schema_key: alpha.v1
component_kind: alpha
fields:
  - name: place
    field_kind: place
    obligation: blocking
    prompt_message_key: ask.alpha.place
    example_message_key: example.alpha.place
  - name: date_range
    field_kind: date_range
    obligation: blocking
    prompt_message_key: ask.alpha.date_range
  - name: starts_on
    field_kind: date
    obligation: optional
    prompt_message_key: ask.alpha.starts_on
  - name: ends_on
    field_kind: date
    obligation: optional
    prompt_message_key: ask.alpha.ends_on
  - name: party_size
    field_kind: integer
    obligation: optional
    prompt_message_key: ask.alpha.party_size
    constraints: {min: 1, max: 12}
  - name: budget_ceiling
    field_kind: money
    obligation: optional
    prompt_message_key: ask.alpha.budget_ceiling
  - name: min_rating
    field_kind: score
    obligation: optional
    prompt_message_key: ask.alpha.min_rating
    constraints: {min: 0, max: 10}
blocking_rules:
  - name: where
    any_of: [[place]]
  - name: when
    any_of: [[date_range], [starts_on, ends_on]]
""",
    "beta.v1": """\
schema_key: beta.v1
component_kind: beta
fields:
  - name: place
    field_kind: place
    obligation: blocking
    prompt_message_key: ask.beta.place
  - name: comfort
    field_kind: enum
    obligation: optional
    prompt_message_key: ask.beta.comfort
    enum_values: [basic, standard, premium]
""",
}


def write_catalog(config_dir: Path, text: str = SAMPLE_CATALOG) -> Path:
    """Write a Component Catalog where ``Settings`` expects to find one, and return its path."""
    path = config_dir / "catalog" / "components.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_schemas(config_dir: Path, schemas: Mapping[str, str] = SAMPLE_SCHEMAS) -> Path:
    """Write Requirement Schemas where ``Settings`` expects them, and return the directory."""
    directory = config_dir / "catalog" / "schemas"
    directory.mkdir(parents=True, exist_ok=True)
    for schema_key, text in schemas.items():
        (directory / f"{schema_key}.yaml").write_text(text, encoding="utf-8")
    return directory


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
def schema_files(tmp_path: Path) -> Path:
    """The Requirement Schemas of ``catalog_file``'s enabled kinds, in the same config tree.

    From F03 on, "a healthy installation" means both: a catalog whose kinds name schemas, and
    the schemas they name. `catalog validate` and `catalog gaps` need this fixture as well as
    ``catalog_file``; `catalog show` and `doctor` still need only the catalog.
    """
    return write_schemas(tmp_path / "config")


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
