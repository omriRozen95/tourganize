"""Fixtures shared by every test. Conventions are documented in ``tests/README.md``."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.domain.options import Money, PlanOption, Provenance
from tourganize.platform.settings import (
    Settings,
    default_keyword_config_dir,
    default_schema_dir,
)

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
#: a start and an end" rule needs, and two optional fields that declare how they *filter* a Plan
#: Option (F06) — ``budget_ceiling`` against an option's price, ``min_rating`` against its
#: ``review_score`` fact. ``gamma`` is disabled and deliberately has no schema — a kind nobody
#: can plan does not need one, and `catalog validate` must not ask for it.
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
    constraints: {filters: price, comparison: at_most}
  - name: min_rating
    field_kind: score
    obligation: optional
    prompt_message_key: ask.alpha.min_rating
    constraints: {min: 0, max: 10, filters: review_score, comparison: at_least}
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


#: The Component Kinds :data:`SAMPLE_KEYWORDS` raises phrases for — :data:`SAMPLE_CATALOG`'s
#: three. A suite that declares a different set asks :func:`keyword_table` for one.
SAMPLE_KEYWORD_KINDS: Final = ("alpha", "beta", "gamma")

_KEYWORDS_TEMPLATE: Final = """\
locale: en
intents:
  end_session: [goodbye, "that is all"]
  state_request: ["where are we"]
  accept_offer: ["yes please", yes]
  decline_offer: ["no thanks", no]
  refine: [cheaper, "something else"]
  small_talk: [hello, thanks]
kinds:
{kinds}
fields:
  place: place
  date_range: date_range
place_markers: [in, at, near]
range_separators: ["/", "-", "–", to]
months:
  october: 10
  november: 11
"""


def keyword_table(*kind_keys: str) -> str:
    """A phrase table for the keyword Turn Interpreter, raising ``kind_keys`` by name.

    One definition, because the table differed between two suites by the single line naming the
    third Component Kind and was otherwise copied word for word. Small on purpose — the tables
    are scaffolding F08 replaces — and English, because every test utterance is. ``fields``
    names the two fields ``alpha.v1`` declares for the shapes this interpreter can read.
    """
    declared = kind_keys or SAMPLE_KEYWORD_KINDS
    return _KEYWORDS_TEMPLATE.format(kinds="\n".join(f"  {key}: [{key}]" for key in declared))


#: A phrase table with neutral Component Kinds, matching :data:`SAMPLE_CATALOG`.
SAMPLE_KEYWORDS: Final = keyword_table()


def write_catalog(config_dir: Path, text: str = SAMPLE_CATALOG) -> Path:
    """Write a Component Catalog where ``Settings`` expects to find one, and return its path."""
    path = config_dir / "catalog" / "components.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def schemas_dir(config_dir: Path) -> Path:
    """Where ``Settings`` resolves ``TOURGANIZE_SCHEMA_DIR`` to inside ``config_dir``.

    The documented default, asked for rather than spelled out: an adapter is handed this
    directory explicitly — it has no default of its own to fall back to — so every test that
    builds one by hand has to say where the schemas are, and should say it the same way.
    """
    return default_schema_dir(config_dir)


def write_schemas(config_dir: Path, schemas: Mapping[str, str] = SAMPLE_SCHEMAS) -> Path:
    """Write Requirement Schemas where ``Settings`` expects them, and return the directory."""
    directory = schemas_dir(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for schema_key, text in schemas.items():
        (directory / f"{schema_key}.yaml").write_text(text, encoding="utf-8")
    return directory


def keywords_dir(config_dir: Path) -> Path:
    """Where ``Settings`` resolves ``TOURGANIZE_KEYWORD_CONFIG_DIR`` to inside ``config_dir``.

    The documented default, asked for rather than spelled out, for the reason
    :func:`schemas_dir` is: the keyword interpreter is handed its directory and has no fallback.
    """
    return default_keyword_config_dir(config_dir)


#: Option fixtures for :data:`SAMPLE_CATALOG`'s two enabled kinds, keyed by ``kind_key`` and
#: then by file name. Neutral keys and neutral content, for the reason the sample catalog has
#: them: a test about *sourcing* should not have to name a travel topic. ``alpha`` carries eight
#: options across two places and two currencies, with review scores spread wide enough that a
#: filter, a ranking and a refinement all visibly do something.
SAMPLE_OPTION_FIXTURES: Final[Mapping[str, Mapping[str, str]]] = {
    "alpha": {
        "paris": """\
{
  "kind_key": "alpha",
  "matchable": ["place", "date_range"],
  "match": {"place": ["Paris"], "date_range": ["2026-01-01/2027-12-31"]},
  "options": [
    {"external_ref": "a-1", "facts": {"review_score": 8.7, "nights": 5},
     "price": {"amount_minor": 74000, "currency": "EUR"}},
    {"external_ref": "a-2", "facts": {"review_score": 9.1, "nights": 5},
     "price": {"amount_minor": 96500, "currency": "EUR"}},
    {"external_ref": "a-3", "facts": {"review_score": 7.4, "nights": 5},
     "price": {"amount_minor": 51000, "currency": "EUR"}},
    {"external_ref": "a-4", "facts": {"review_score": 9.4, "nights": 5},
     "price": {"amount_minor": 148000, "currency": "EUR"}},
    {"external_ref": "a-5", "facts": {"review_score": 6.9, "nights": 5},
     "price": {"amount_minor": 39000, "currency": "EUR"}}
  ]
}
""",
        "lisbon": """\
{
  "kind_key": "alpha",
  "matchable": ["place"],
  "match": {"place": ["Lisbon"]},
  "options": [
    {"external_ref": "b-1", "facts": {"review_score": 8.2, "nights": 3},
     "price": {"amount_minor": 62000, "currency": "ILS"}},
    {"external_ref": "b-2", "facts": {"review_score": 7.0, "nights": 3},
     "price": {"amount_minor": 41000, "currency": "ILS"}},
    {"external_ref": "b-3", "facts": {"review_score": 9.0, "nights": 3},
     "price": {"amount_minor": 118000, "currency": "ILS"}}
  ]
}
""",
    },
    "beta": {
        "everywhere": """\
{
  "kind_key": "beta",
  "matchable": ["place"],
  "options": [
    {"external_ref": "c-1", "facts": {"comfort": "basic"},
     "price": {"amount_minor": 12000, "currency": "EUR"}},
    {"external_ref": "c-2", "facts": {"comfort": "premium"},
     "price": {"amount_minor": 45000, "currency": "EUR"}}
  ]
}
""",
    },
}


def write_option_fixtures(
    root: Path, fixtures: Mapping[str, Mapping[str, str]] = SAMPLE_OPTION_FIXTURES
) -> Path:
    """Write a Fixture Provider tree — ``<root>/<kind_key>/<name>.json`` — and return ``root``."""
    for kind_key, files in fixtures.items():
        directory = root / kind_key
        directory.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (directory / f"{name}.json").write_text(text, encoding="utf-8")
    return root


def write_keywords(config_dir: Path, tables: Mapping[str, str] | None = None) -> Path:
    """Write keyword phrase tables where ``Settings`` expects them, and return the directory."""
    declared = {"en": SAMPLE_KEYWORDS} if tables is None else tables
    directory = keywords_dir(config_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for locale, text in declared.items():
        (directory / f"keywords.{locale}.yaml").write_text(text, encoding="utf-8")
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
def keyword_files(tmp_path: Path) -> Path:
    """The keyword interpreter's phrase tables, in the config tree ``settings_factory`` uses.

    From F05 on, "a healthy installation" means these too: the Turn Interpreter is a wired port,
    so ``doctor`` probes it, and an interpreter with no phrases is a misconfigured install rather
    than a quiet degradation.
    """
    return write_keywords(tmp_path / "config")


@pytest.fixture
def option_fixture_dir(tmp_path: Path) -> Path:
    """A Fixture Provider tree for ``catalog_file``'s enabled kinds, inside this test's tmp_path.

    From F06 on, "a healthy installation" means this too — though a *missing* tree is not a
    broken one: the Fixture Provider answers a query it has no recording for with a synthetic
    set, so that a demonstration never dead-ends. What this fixture buys is recorded data to
    assert on.
    """
    return write_option_fixtures(tmp_path / "fixtures" / "options")


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
