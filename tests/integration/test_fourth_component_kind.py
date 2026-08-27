"""Adding a fourth Component Kind costs no Python — option sourcing included.

F02 proved it for the catalog, F03 for the Requirement Schema, F04 for the Agenda. This is F06's
half: a Kind that did not exist when the Planning Service was written is declared in
``components.yaml``, described by a schema file, given a directory of recorded options — and
sourced, filtered, ranked and presented with **no change to any module under** ``tourganize/``.

The catalog and the schema are written into ``tmp_path`` rather than added to the shipped files,
because whether Tourganize *ships* a fourth topic is a product decision and this is a test about
the architecture. The fixture data is the shipped tree: ``fixtures/options/dining/`` exists in
the repository precisely so that this proof runs against real recorded data.

Nothing in this file imports a module F06 did not already have. That is the assertion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from conftest import keyword_table, write_keywords, write_messages

from tourganize.application.composition import Container, build_container
from tourganize.application.diagnostics import run_diagnostics
from tourganize.domain.requirements import RequirementSet, RequirementUpdate
from tourganize.domain.trip import TripPlan
from tourganize.platform.settings import Settings

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SHIPPED_FIXTURES: Final = REPO_ROOT / "fixtures" / "options"

#: The fourth topic, declared nowhere in ``tourganize/`` and everywhere in configuration.
FOURTH: Final = "dining"

CATALOG: Final = f"""\
version: 1
kinds:
  - kind_key: {FOURTH}
    message_key: component.{FOURTH}
    priority_weight: 50
    schema_key: {FOURTH}.v1
    requires_outcome_of: []
    enabled: true
"""

SCHEMA: Final = f"""\
schema_key: {FOURTH}.v1
component_kind: {FOURTH}
fields:
  - name: place
    field_kind: place
    obligation: blocking
    prompt_message_key: ask.{FOURTH}.place
  - name: date_range
    field_kind: date_range
    obligation: blocking
    prompt_message_key: ask.{FOURTH}.date_range
  - name: party_size
    field_kind: integer
    obligation: optional
    prompt_message_key: ask.{FOURTH}.party_size
    constraints: {{min: 1, max: 12, filters: party_size, comparison: at_least}}
  - name: budget_ceiling
    field_kind: money
    obligation: optional
    prompt_message_key: ask.{FOURTH}.budget_ceiling
    constraints: {{filters: price, comparison: at_most}}
blocking_rules:
  - name: where
    any_of: [[place]]
  - name: when
    any_of: [[date_range]]
"""


def settings_for(tmp_path: Path, **overrides: str) -> Settings:
    """A configuration whose *only* Component Kind is one nobody wrote Python for."""
    config = tmp_path / "config"
    (config / "catalog" / "schemas").mkdir(parents=True, exist_ok=True)
    (config / "catalog" / "components.yaml").write_text(CATALOG, encoding="utf-8")
    (config / "catalog" / "schemas" / f"{FOURTH}.v1.yaml").write_text(SCHEMA, encoding="utf-8")
    # Phrase tables too, so that `doctor` is asked about a *whole* installation rather than one
    # with a port missing: the interpreter check would otherwise fail for reasons of its own.
    write_keywords(config, {"en": keyword_table(FOURTH)})
    # And a Message Catalogue, for the same reason — and one that says nothing whatever about
    # this Kind, which is the point: a fourth topic renders from the fallbacks, so `doctor`
    # passes and a slate draws without a single line of configuration naming it.
    write_messages(config)
    environ = {
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(config),
        "TOURGANIZE_FIXTURE_DIR": str(SHIPPED_FIXTURES),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
        "TOURGANIZE_TELEMETRY_SINK": "null",
    }
    environ.update(overrides)
    return Settings.from_env(environ)


def requirements(container: Container, **values: object) -> RequirementSet:
    """What the traveller has said, merged against the schema the *catalog port* resolves.

    Through the Container rather than by reading the file: the schema this test wrote is only
    interesting if the application finds it the way it finds every other one.
    """
    schema = container.component_catalog.schema_for(FOURTH)
    updates = [RequirementUpdate(field_name=name, value=value) for name, value in values.items()]
    return RequirementSet.empty(FOURTH).with_updates(updates, schema=schema)


def test_the_fourth_kind_is_catalogued_and_plannable(tmp_path: Path) -> None:
    container = build_container(settings_for(tmp_path))

    assert [kind.kind_key for kind in container.component_catalog.enabled_kinds()] == [FOURTH]
    assert container.component_catalog.schema_for(FOURTH).schema_key == f"{FOURTH}.v1"


def test_the_fourth_kind_is_sourced_from_its_own_fixture_directory(tmp_path: Path) -> None:
    """The whole feature, for a topic no module names: a directory of JSON became a slate."""
    container = build_container(settings_for(tmp_path))
    held = requirements(container, place="Paris", date_range="2026-10-23/2026-10-28")

    slate = container.option_slate_planner.plan(FOURTH, held, _a_plan(container), 0)

    assert slate.kind_key == FOURTH
    assert slate.options
    assert slate.diagnostics == ()
    assert all(option.kind_key == FOURTH for option in slate.options)
    assert all(option.price is not None for option in slate.options)
    assert all(option.provenance.external_ref for option in slate.options)


def test_the_fourth_kind_s_declared_filters_work_the_way_every_other_kind_s_do(
    tmp_path: Path,
) -> None:
    """The filter is declared in the new schema file. No Python knew it existed."""
    container = build_container(settings_for(tmp_path))
    held = requirements(
        container, place="Paris", date_range="2026-10-23/2026-10-28", budget_ceiling="1000 EUR"
    )

    slate = container.option_slate_planner.plan(FOURTH, held, _a_plan(container), 0)

    assert slate.options
    assert all(option.filter_notes == ("budget_ceiling",) for option in slate.options)


def test_strict_filtering_reaches_the_fourth_kind_too(tmp_path: Path) -> None:
    container = build_container(settings_for(tmp_path, TOURGANIZE_OPTION_FILTER_STRICT="true"))
    held = requirements(
        container, place="Paris", date_range="2026-10-23/2026-10-28", budget_ceiling="1000 EUR"
    )

    slate = container.option_slate_planner.plan(FOURTH, held, _a_plan(container), 0)

    assert slate.options == ()
    assert "filtered_out" in slate.diagnostics


def test_doctor_reports_the_fourth_kind_s_sources(tmp_path: Path) -> None:
    """A new topic is visible in the health report without anybody teaching it the topic."""
    report = run_diagnostics(build_container(settings_for(tmp_path)), version="9.9.9")

    check = next(item for item in report.checks if item.name == "option_sources")
    assert check.ok
    assert f"{FOURTH} -> fixture: fixture" in check.detail
    assert "1 of 1 with recorded data" in check.detail
    assert report.ok


def test_the_shipped_tree_holds_recorded_data_for_the_fourth_kind() -> None:
    """The negative tests above would also pass against a synthetic fallback."""
    assert (SHIPPED_FIXTURES / FOURTH).is_dir()
    assert list((SHIPPED_FIXTURES / FOURTH).glob("*.json"))


def _a_plan(container: Container) -> TripPlan:
    return TripPlan(plan_id="fourth", created_at=container.clock.now())
