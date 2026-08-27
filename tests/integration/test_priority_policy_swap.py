"""Swapping the Priority Policy is one environment variable and nothing else.

The point D3 makes, checked end to end: the same catalog, the same plan, the same
``build_agenda`` call, one value of ``TOURGANIZE_PRIORITY_POLICY`` — and a different planning
order. Everything goes through ``Settings.from_env`` and ``build_container``, because "no other
code change" is only true if the wiring is the thing doing the choosing.

The catalog written here declares its Kinds in the *opposite* order to their weights, which is
what makes the two policies distinguishable at all: the shipped catalog happens to declare its
Kinds heaviest first, so both policies agree about it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from conftest import write_catalog

from tourganize.application.composition import Container, build_container
from tourganize.domain.catalog import build_agenda
from tourganize.domain.trip import TripPlan
from tourganize.platform.settings import Settings

#: Declaration order and weight order disagree on purpose: ``alpha`` is declared first and is
#: the lightest, ``gamma`` is declared last and is the heaviest.
AGAINST_THE_WEIGHTS: Final = """\
version: 1
kinds:
  - kind_key: alpha
    message_key: component.alpha
    priority_weight: 100
    schema_key: alpha.v1
    enabled: true
  - kind_key: beta
    message_key: component.beta
    priority_weight: 200
    schema_key: beta.v1
    enabled: true
  - kind_key: gamma
    message_key: component.gamma
    priority_weight: 300
    schema_key: gamma.v1
    enabled: true
"""


def planning_order(container: Container) -> tuple[str, ...]:
    """The Agenda of an empty plan, built exactly as F05 will build it every turn."""
    plan = TripPlan(plan_id="plan-1", created_at=container.clock.now())
    agenda = build_agenda(
        plan,
        container.component_catalog.kinds(),
        container.priority_policy,
        failure_skip=container.settings.agenda_failure_skip,
    )
    return tuple(entry.kind_key for entry in agenda.entries)


def containers(tmp_path: Path) -> tuple[Container, Container]:
    """Two wired applications, identical but for ``TOURGANIZE_PRIORITY_POLICY``."""
    write_catalog(tmp_path / "config", AGAINST_THE_WEIGHTS)
    environ = {
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
    }
    return (
        build_container(Settings.from_env({**environ, "TOURGANIZE_PRIORITY_POLICY": "weighted"})),
        build_container(Settings.from_env({**environ, "TOURGANIZE_PRIORITY_POLICY": "fixed"})),
    )


def test_one_setting_changes_the_planning_order_and_nothing_else_does(tmp_path: Path) -> None:
    weighted, fixed = containers(tmp_path)

    assert planning_order(weighted) == ("gamma", "beta", "alpha")
    assert planning_order(fixed) == ("alpha", "beta", "gamma")


def test_the_two_containers_differ_in_exactly_one_slot(tmp_path: Path) -> None:
    """A config switch, not a fork: every other adapter is the same class in both."""
    weighted, fixed = containers(tmp_path)

    differing = {
        port for port, adapter in weighted.adapters().items() if adapter != fixed.adapters()[port]
    }

    assert differing == {"PriorityPolicy"}


def test_the_default_container_uses_the_weighted_policy(tmp_path: Path) -> None:
    """Nobody has to set the key for the documented default to be in force."""
    write_catalog(tmp_path / "config", AGAINST_THE_WEIGHTS)
    settings = Settings.from_env(
        {
            "TOURGANIZE_ENV": "test",
            "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
            "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
        }
    )

    container = build_container(settings)

    assert container.priority_policy.policy_id == "weighted"
    assert planning_order(container) == ("gamma", "beta", "alpha")
