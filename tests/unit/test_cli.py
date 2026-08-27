"""The CLI surface: the commands that work, the stubs that name their feature, four exit codes."""

from __future__ import annotations

import io
import itertools
from collections.abc import Mapping
from pathlib import Path

import pytest
from conftest import (
    write_catalog,
    write_keywords,
    write_messages,
    write_option_fixtures,
    write_schemas,
)

from tourganize import __version__
from tourganize.cli import (
    CATALOG_ACTIONS,
    EXIT_CONFIGURATION_ERROR,
    EXIT_DOCTOR_FAILED,
    EXIT_NOT_IMPLEMENTED,
    EXIT_OK,
    EXIT_USAGE_ERROR,
    OPTIONS_ACTIONS,
    PLANNED_COMMANDS,
    main,
)


def _run(argv: list[str], environ: Mapping[str, str] | None = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, environ=environ or {}, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def _environ(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A healthy installation: catalog, schemas, phrase tables, messages and fixtures."""
    write_catalog(tmp_path / "config")
    write_schemas(tmp_path / "config")
    write_keywords(tmp_path / "config")
    write_messages(tmp_path / "config")
    write_option_fixtures(tmp_path / "fixtures" / "options")
    environ = {
        "TOURGANIZE_ENV": "test",
        "TOURGANIZE_CONFIG_DIR": str(tmp_path / "config"),
        "TOURGANIZE_DATA_DIR": str(tmp_path / "var"),
        "TOURGANIZE_FIXTURE_DIR": str(tmp_path / "fixtures" / "options"),
    }
    environ.update(extra)
    return environ


def test_version_prints_the_package_version() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--version"], environ={})
    assert raised.value.code == EXIT_OK


def test_no_command_prints_help() -> None:
    code, out, _ = _run([])
    assert code == EXIT_OK
    assert "doctor" in out
    assert "chat" in out


@pytest.mark.parametrize("command", sorted(PLANNED_COMMANDS))
def test_every_planned_command_exits_2_naming_its_feature(command: str) -> None:
    feature, _summary = PLANNED_COMMANDS[command]

    code, out, err = _run([command])

    assert code == EXIT_NOT_IMPLEMENTED
    assert feature in err
    assert command in err
    assert out == ""


def test_chat_without_a_terminal_refuses_rather_than_waiting_for_a_keystroke() -> None:
    """The default surface is the terminal one, and a pipe is not a terminal.

    Refusing is the whole point: a Textual application started against a stand-in stream does
    not fail, it waits — and a suite that hangs says less than any error message. Both ways
    out are named in the refusal because both are one flag away.
    """
    code, out, err = _run(["chat"])

    assert code == EXIT_CONFIGURATION_ERROR
    assert "--script" in err
    assert "TOURGANIZE_SURFACE=scripted" in err
    assert out == ""


@pytest.mark.parametrize("action", CATALOG_ACTIONS)
def test_every_catalog_action_runs_rather_than_naming_a_feature(
    action: str, tmp_path: Path
) -> None:
    """F04 implemented `agenda`, the last `catalog` action that was still a stub. A stub exits 2
    and names the feature that will implement it; none of these do."""
    code, _out, err = _run(
        ["catalog", action, *(["--kind", "alpha"] if action == "gaps" else [])], _environ(tmp_path)
    )

    assert code == EXIT_OK
    assert "not implemented" not in err


def test_catalog_show_lists_the_declared_kinds(tmp_path: Path) -> None:
    code, out, _ = _run(["catalog", "show"], _environ(tmp_path))

    assert code == EXIT_OK
    assert "kind_key" in out
    assert "alpha" in out and "beta" in out and "gamma" in out
    assert "300" in out
    # beta awaits alpha's outcome, and gamma is declared but disabled.
    beta = next(line for line in out.splitlines() if line.startswith("beta"))
    gamma = next(line for line in out.splitlines() if line.startswith("gamma"))
    assert "alpha" in beta
    assert gamma.endswith("no")


def test_catalog_validate_accepts_a_sound_catalog(tmp_path: Path) -> None:
    code, out, err = _run(["catalog", "validate"], _environ(tmp_path))

    assert code == EXIT_OK
    assert "no problems found" in out
    assert "2 Requirement Schemas" in out
    assert err == ""


#: A broken catalog per validation rule, with the phrase the CLI must print for it. Keyed by
#: a short name so the parametrised test ids read as the rules they exercise.
BROKEN_CATALOGS = {
    "duplicate_key": (
        "duplicate kind_key 'alpha'",
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1}\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 2,"
        " schema_key: alpha.v1}\n",
    ),
    "dangling_dependency": (
        "which no kind declares",
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1, requires_outcome_of: [nowhere]}\n",
    ),
    "dependency_cycle": (
        "dependency cycle",
        "version: 1\n"
        "kinds:\n"
        "  - {kind_key: alpha, message_key: component.alpha, priority_weight: 1,"
        " schema_key: alpha.v1, requires_outcome_of: [beta]}\n"
        "  - {kind_key: beta, message_key: component.beta, priority_weight: 2,"
        " schema_key: beta.v1, requires_outcome_of: [alpha]}\n",
    ),
}


@pytest.mark.parametrize("rule", sorted(BROKEN_CATALOGS))
def test_catalog_validate_exits_3_and_names_the_problem(rule: str, tmp_path: Path) -> None:
    expected, body = BROKEN_CATALOGS[rule]
    environ = _environ(tmp_path)
    write_catalog(tmp_path / "config", body)

    code, out, err = _run(["catalog", "validate"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert expected in err
    assert out == ""


def test_catalog_show_exits_3_when_there_is_no_catalog(tmp_path: Path) -> None:
    environ = _environ(tmp_path, TOURGANIZE_CATALOG_PATH=str(tmp_path / "absent.yaml"))

    code, out, err = _run(["catalog", "show"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "does not exist" in err
    assert out == ""


def test_catalog_without_an_action_says_what_it_offers(tmp_path: Path) -> None:
    """And says it before reading the file, so a missing action is not reported as a bad catalog."""
    environ = _environ(tmp_path, TOURGANIZE_CATALOG_PATH=str(tmp_path / "absent.yaml"))

    code, out, err = _run(["catalog"], environ)

    assert code == EXIT_NOT_IMPLEMENTED
    assert all(action in err for action in CATALOG_ACTIONS)
    assert "does not exist" not in err
    assert out == ""


#: A broken Requirement Schema per validation rule, with the phrase the CLI must print for it.
#: These are the four the Definition of Done names.
BROKEN_SCHEMAS = {
    "rule_names_an_undeclared_field": (
        "which the schema does not declare",
        "schema_key: alpha.v1\ncomponent_kind: alpha\nfields:\n"
        "  - {name: place, field_kind: place, obligation: blocking,"
        " prompt_message_key: ask.alpha.place}\n"
        "blocking_rules:\n  - {name: where, any_of: [[nowhere]]}\n",
    ),
    "field_without_a_prompt_message_key": (
        "missing required key(s) prompt_message_key",
        "schema_key: alpha.v1\ncomponent_kind: alpha\nfields:\n"
        "  - {name: place, field_kind: place, obligation: blocking}\n",
    ),
    "enum_field_without_values": (
        "must declare its enum_values",
        "schema_key: alpha.v1\ncomponent_kind: alpha\nfields:\n"
        "  - {name: comfort, field_kind: enum, obligation: blocking,"
        " prompt_message_key: ask.alpha.comfort}\n",
    ),
    "component_kind_disagrees_with_the_catalog": (
        "but that schema describes 'elsewhere'",
        "schema_key: alpha.v1\ncomponent_kind: elsewhere\nfields:\n"
        "  - {name: place, field_kind: place, obligation: blocking,"
        " prompt_message_key: ask.alpha.place}\n",
    ),
}


@pytest.mark.parametrize("rule", sorted(BROKEN_SCHEMAS))
def test_catalog_validate_exits_3_for_a_broken_schema(rule: str, tmp_path: Path) -> None:
    expected, body = BROKEN_SCHEMAS[rule]
    environ = _environ(tmp_path)
    write_schemas(tmp_path / "config", {"alpha.v1": body})

    code, out, err = _run(["catalog", "validate"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert expected in err
    assert out == ""


def test_catalog_validate_exits_3_when_a_schema_file_is_missing(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    (tmp_path / "config" / "catalog" / "schemas" / "alpha.v1.yaml").unlink()

    code, _out, err = _run(["catalog", "validate"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "alpha.v1.yaml" in err


def test_catalog_validate_does_not_demand_a_schema_for_a_disabled_kind(tmp_path: Path) -> None:
    """`gamma` is disabled and has no schema file; a kind nobody can plan needs none."""
    code, out, _err = _run(["catalog", "validate"], _environ(tmp_path))

    assert code == EXIT_OK
    assert "2 Requirement Schemas" in out


def test_catalog_gaps_on_an_empty_set_reports_every_rule_and_every_filter(
    tmp_path: Path,
) -> None:
    code, out, err = _run(["catalog", "gaps", "--kind", "alpha"], _environ(tmp_path))

    assert code == EXIT_OK
    assert err == ""
    assert "is_plannable: false" in out
    assert "blocking (2):" in out
    assert "where" in out and "when" in out
    assert "optional (5):" in out
    assert "ask.alpha.place" in out
    assert "date_range  |  starts_on + ends_on" in out


def test_catalog_gaps_with_the_blocking_values_supplied_is_plannable(tmp_path: Path) -> None:
    code, out, _err = _run(
        [
            "catalog",
            "gaps",
            "--kind",
            "alpha",
            "--set",
            '{"place": "Paris", "date_range": "2026-10-23/2026-10-28"}',
        ],
        _environ(tmp_path),
    )

    assert code == EXIT_OK
    assert "is_plannable: true" in out
    assert "blocking (0):" in out
    assert "optional (5):" in out


def test_catalog_gaps_reports_a_present_but_invalid_value_as_invalid(tmp_path: Path) -> None:
    code, out, _err = _run(
        [
            "catalog",
            "gaps",
            "--kind",
            "alpha",
            "--set",
            '{"place": "Paris", "date_range": "2026-10-28/2026-10-23"}',
        ],
        _environ(tmp_path),
    )

    assert code == EXIT_OK
    assert "is_plannable: false" in out
    assert "blocking (0):" in out
    assert "invalid (1):" in out
    assert "requirement.invalid.date_range_reversed" in out


def test_catalog_gaps_refuses_an_unknown_kind(tmp_path: Path) -> None:
    code, out, err = _run(["catalog", "gaps", "--kind", "nowhere"], _environ(tmp_path))

    assert code == EXIT_USAGE_ERROR
    assert "nowhere" in err
    assert out == ""


def test_catalog_gaps_refuses_an_unknown_field_rather_than_ignoring_it(tmp_path: Path) -> None:
    """It usually means an extraction prompt and a schema have drifted apart."""
    code, out, err = _run(
        ["catalog", "gaps", "--kind", "alpha", "--set", '{"nowhere": 1}'], _environ(tmp_path)
    )

    assert code == EXIT_USAGE_ERROR
    assert "nowhere" in err
    assert out == ""


@pytest.mark.parametrize("supplied", ["not json", "[1, 2]", '"just a string"'])
def test_catalog_gaps_refuses_a_set_that_is_not_a_json_object(
    supplied: str, tmp_path: Path
) -> None:
    code, out, err = _run(
        ["catalog", "gaps", "--kind", "alpha", "--set", supplied], _environ(tmp_path)
    )

    assert code == EXIT_USAGE_ERROR
    assert "--set" in err
    assert out == ""


def test_catalog_gaps_needs_a_kind(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["catalog", "gaps"], environ=_environ(tmp_path))

    assert raised.value.code == EXIT_USAGE_ERROR


def test_catalog_gaps_exits_3_when_the_schema_is_missing(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    (tmp_path / "config" / "catalog" / "schemas" / "alpha.v1.yaml").unlink()

    code, out, err = _run(["catalog", "gaps", "--kind", "alpha"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "alpha.v1.yaml" in err
    assert out == ""


def test_the_schema_directory_can_be_moved_on_its_own(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    write_schemas(elsewhere)
    environ = _environ(tmp_path, TOURGANIZE_SCHEMA_DIR=str(elsewhere / "catalog" / "schemas"))
    (tmp_path / "config" / "catalog" / "schemas" / "alpha.v1.yaml").unlink()

    code, out, _err = _run(["catalog", "gaps", "--kind", "alpha"], environ)

    assert code == EXIT_OK
    assert "is_plannable: false" in out


def _agenda_rows(out: str) -> list[list[str]]:
    """The agenda table's rows, split into cells: the lines between the rule and the blank."""
    lines = out.splitlines()
    rule = next(index for index, line in enumerate(lines) if line and set(line) <= {"-", " "})
    return [line.split() for line in itertools.takewhile(bool, lines[rule + 1 :])]


def test_catalog_agenda_puts_a_mentioned_kind_first_whatever_its_weight(tmp_path: Path) -> None:
    """`beta` is the lighter of the two enabled Kinds in the fixture catalog."""
    code, out, err = _run(["catalog", "agenda", "--mentioned", "beta"], _environ(tmp_path))

    assert code == EXIT_OK
    assert err == ""
    assert _agenda_rows(out)[:2] == [
        ["beta", "MENTIONED", "0", "-", "not_plannable"],
        ["alpha", "UNMENTIONED", "0", "-", "not_plannable"],
    ]
    assert "next_actionable: beta" in out
    assert "mentioned_band_empty: false" in out


def test_catalog_agenda_orders_an_unmentioned_band_by_weight(tmp_path: Path) -> None:
    code, out, _err = _run(["catalog", "agenda"], _environ(tmp_path))

    assert code == EXIT_OK
    assert [row[0] for row in _agenda_rows(out)] == ["alpha", "beta"]
    assert "mentioned_band_empty: true" in out


def test_catalog_agenda_reports_an_outcome_dependency_inside_a_band(tmp_path: Path) -> None:
    """`beta` awaits `alpha` in the fixture catalog, and both are mentioned here."""
    code, out, _err = _run(["catalog", "agenda", "--mentioned", "beta,alpha"], _environ(tmp_path))

    assert code == EXIT_OK
    assert _agenda_rows(out) == [
        ["alpha", "MENTIONED", "0", "-", "not_plannable"],
        ["beta", "MENTIONED", "1", "alpha", "awaits_outcome"],
    ]
    assert "next_actionable: alpha" in out


def test_catalog_agenda_drops_what_is_selected_and_what_was_declined(tmp_path: Path) -> None:
    code, out, _err = _run(
        ["catalog", "agenda", "--mentioned", "alpha", "--selected", "alpha", "--declined", "beta"],
        _environ(tmp_path),
    )

    assert code == EXIT_OK
    assert _agenda_rows(out) == []  # nothing left to plan: an empty agenda is a valid answer
    assert "next_actionable: none" in out
    assert "mentioned_band_empty: true" in out


def test_catalog_agenda_never_plans_a_disabled_kind(tmp_path: Path) -> None:
    """`gamma` is declared and disabled, so it is not on the agenda and cannot be named."""
    code, out, _err = _run(["catalog", "agenda"], _environ(tmp_path))
    refused, _out, err = _run(["catalog", "agenda", "--mentioned", "gamma"], _environ(tmp_path))

    assert code == EXIT_OK
    assert "gamma" not in out
    assert refused == EXIT_USAGE_ERROR
    assert "disabled" in err


def test_catalog_agenda_names_the_policy_that_produced_it(tmp_path: Path) -> None:
    weighted = _run(["catalog", "agenda"], _environ(tmp_path))
    fixed = _run(["catalog", "agenda"], _environ(tmp_path, TOURGANIZE_PRIORITY_POLICY="fixed"))

    assert "(policy weighted)" in weighted[1]
    assert "(policy fixed)" in fixed[1]


def test_catalog_agenda_refuses_an_unknown_kind(tmp_path: Path) -> None:
    code, out, err = _run(["catalog", "agenda", "--mentioned", "nowhere"], _environ(tmp_path))

    assert code == EXIT_USAGE_ERROR
    assert "nowhere" in err
    assert "alpha" in err
    assert out == ""


def test_catalog_agenda_refuses_arguments_that_contradict_each_other(tmp_path: Path) -> None:
    """Selected and declined are two different endings; a Kind cannot have both."""
    code, out, err = _run(
        ["catalog", "agenda", "--selected", "alpha", "--declined", "alpha"], _environ(tmp_path)
    )

    assert code == EXIT_USAGE_ERROR
    assert "SELECTED -> DECLINED" in err
    assert out == ""


def test_catalog_agenda_exits_3_when_a_schema_is_missing(tmp_path: Path) -> None:
    """It reports plannability, so it needs the same schemas `validate` does."""
    environ = _environ(tmp_path)
    (tmp_path / "config" / "catalog" / "schemas" / "alpha.v1.yaml").unlink()

    code, out, err = _run(["catalog", "agenda"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "alpha.v1.yaml" in err
    assert out == ""


def test_catalog_agenda_exits_3_when_the_catalog_is_broken(tmp_path: Path) -> None:
    environ = _environ(tmp_path)
    write_catalog(tmp_path / "config", BROKEN_CATALOGS["dependency_cycle"][1])

    code, out, err = _run(["catalog", "agenda"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "dependency cycle" in err
    assert out == ""


def test_doctor_reports_settings_adapters_and_ports(tmp_path: Path) -> None:
    code, out, _ = _run(["doctor"], _environ(tmp_path))

    assert code == EXIT_OK
    assert f"tourganize {__version__}" in out
    assert "telemetry_sink: jsonl" in out
    assert "schema_dir: " in out
    assert "TelemetrySink: JsonlTelemetrySink" in out
    assert "ComponentCatalog: YamlComponentCatalog" in out
    assert "[ok  ] clock" in out
    assert "[ok  ] component_catalog" in out
    assert "[ok  ] priority_policy" in out
    assert "priority_policy: weighted" in out
    assert "doctor: ok" in out


def test_doctor_never_prints_a_secret(tmp_path: Path) -> None:
    leak = "cli-must-not-print-this"
    environ = _environ(tmp_path, TOURGANIZE_PROVIDER_API_KEY=leak)

    code, out, err = _run(["doctor"], environ)

    assert code == EXIT_OK
    assert leak not in out
    assert leak not in err
    assert "TOURGANIZE_PROVIDER_API_KEY=***" in out


def test_doctor_fails_when_a_port_is_unhealthy(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")
    environ = _environ(tmp_path, TOURGANIZE_DATA_DIR=str(blocker / "state"))

    code, out, _ = _run(["doctor"], environ)

    assert code == EXIT_DOCTOR_FAILED
    assert "doctor: FAILED" in out


def test_an_invalid_setting_exits_3_before_anything_is_built(tmp_path: Path) -> None:
    environ = _environ(tmp_path, TOURGANIZE_LOG_FORMAT="xml")

    code, out, err = _run(["doctor"], environ)

    assert code == EXIT_CONFIGURATION_ERROR
    assert "configuration error" in err
    assert "TOURGANIZE_LOG_FORMAT" in err
    assert out == ""


def test_a_stub_command_reports_a_broken_configuration_rather_than_its_own_stubbing() -> None:
    """Settings are resolved before dispatch: "fail fast, never half-configured"."""
    code, out, err = _run([next(iter(sorted(PLANNED_COMMANDS)))], {"TOURGANIZE_LOG_FORMAT": "xml"})

    assert code == EXIT_CONFIGURATION_ERROR
    assert "TOURGANIZE_LOG_FORMAT" in err
    assert out == ""


def test_an_unknown_command_is_rejected_by_the_parser() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["teleport"], environ={})
    assert raised.value.code == 2


# -- `options search` --------------------------------------------------------------------------


def _slate_rows(out: str) -> list[list[str]]:
    """The printed slate's rows as columns: option_id, price, facts, source, fails."""
    lines = out.splitlines()
    rule = next(index for index, line in enumerate(lines) if line.strip().startswith("---"))
    return [line.split() for line in itertools.takewhile(bool, lines[rule + 1 :])]


def test_options_search_prints_a_slate_with_prices_and_provenance(tmp_path: Path) -> None:
    """F06's headline: the first command that shows real option data."""
    code, out, err = _run(
        ["options", "search", "--kind", "alpha", "--set", '{"place": "Paris"}'],
        _environ(tmp_path),
    )

    assert code == EXIT_OK, out + err
    assert "alpha (schema alpha.v1, round 0)" in out
    assert "requirements_digest: " in out
    rows = _slate_rows(out)
    assert len(rows) == 3
    for row in rows:
        assert row[0].startswith("fixture:")
        assert row[2] == "EUR"  # the currency, beside the amount in minor units
        assert "review_score=" in " ".join(row)


def test_options_search_honours_the_configured_slate_size(tmp_path: Path) -> None:
    code, out, _ = _run(
        ["options", "search", "--kind", "alpha", "--set", '{"place": "Paris"}'],
        _environ(tmp_path, TOURGANIZE_SLATE_SIZE="2"),
    )

    assert code == EXIT_OK
    assert len(_slate_rows(out)) == 2


def test_options_search_marks_an_option_that_fails_an_optional_filter(tmp_path: Path) -> None:
    code, out, _ = _run(
        [
            "options",
            "search",
            "--kind",
            "alpha",
            "--set",
            '{"place": "Paris", "budget_ceiling": "1000 EUR"}',
        ],
        _environ(tmp_path),
    )

    assert code == EXIT_OK
    assert out.count("budget_ceiling") == len(_slate_rows(out))


def test_options_search_says_when_strict_filtering_left_nothing(tmp_path: Path) -> None:
    code, out, _ = _run(
        [
            "options",
            "search",
            "--kind",
            "alpha",
            "--set",
            '{"place": "Paris", "budget_ceiling": "1000 EUR"}',
        ],
        _environ(tmp_path, TOURGANIZE_OPTION_FILTER_STRICT="true"),
    )

    assert code == EXIT_OK
    assert "options (0):" in out
    assert "diagnostics: filtered_out" in out


def test_options_search_says_when_a_slate_had_to_be_synthesised(tmp_path: Path) -> None:
    """A demonstration never dead-ends, and the output never pretends the data was recorded."""
    code, out, _ = _run(
        ["options", "search", "--kind", "alpha", "--set", '{"place": "Reykjavik"}'],
        _environ(tmp_path),
    )

    assert code == EXIT_OK
    assert "synthesised" in out
    assert len(_slate_rows(out)) == 3


def test_options_search_refuses_a_kind_the_catalog_does_not_declare(tmp_path: Path) -> None:
    code, _, err = _run(["options", "search", "--kind", "nowhere"], _environ(tmp_path))

    assert code == EXIT_USAGE_ERROR
    assert "nowhere" in err


def test_options_search_refuses_a_field_the_schema_does_not_declare(tmp_path: Path) -> None:
    code, _, err = _run(
        ["options", "search", "--kind", "alpha", "--set", '{"nowhere": 1}'], _environ(tmp_path)
    )

    assert code == EXIT_USAGE_ERROR
    assert "nowhere" in err


def test_options_search_refuses_a_set_that_is_not_json(tmp_path: Path) -> None:
    code, _, err = _run(
        ["options", "search", "--kind", "alpha", "--set", "place=Paris"], _environ(tmp_path)
    )

    assert code == EXIT_USAGE_ERROR
    assert "not valid JSON" in err


def test_options_needs_an_action(tmp_path: Path) -> None:
    code, _, err = _run(["options"], _environ(tmp_path))

    assert code == EXIT_NOT_IMPLEMENTED
    assert ", ".join(OPTIONS_ACTIONS) in err


def test_options_search_is_deterministic_to_the_byte(tmp_path: Path) -> None:
    """The same query twice prints the same slate — what F11's replay rests on."""
    environ = _environ(tmp_path)
    arguments = ["options", "search", "--kind", "alpha", "--set", '{"place": "Paris"}']

    first = _run(arguments, environ)
    second = _run(arguments, environ)

    assert first == second


def test_options_search_exits_3_for_a_profile_this_release_cannot_build(tmp_path: Path) -> None:
    """`live` resolves in Settings and is refused by the Composition Root, naming F24.

    Sourcing failing *at run time* cannot be reached from here — the fixture profile always
    answers, synthesising when it has nothing recorded — so what the command line can be shown
    is the configuration that would have failed, refused before a slate is even attempted.
    ``tests/unit/test_planning_service.py`` is where every-source-failing is exercised.
    """
    code, out, err = _run(
        ["options", "search", "--kind", "alpha"],
        _environ(tmp_path, TOURGANIZE_OPTION_SOURCE_PROFILE="alpha=live"),
    )

    assert code == EXIT_CONFIGURATION_ERROR
    assert "F24" in err
    assert out == ""


def test_doctor_reports_the_option_sources_and_the_profile(tmp_path: Path) -> None:
    code, out, _ = _run(
        ["doctor"],
        _environ(tmp_path, TOURGANIZE_OPTION_SOURCE_PROFILE="alpha=fixture,beta=fixture"),
    )

    assert code == EXIT_OK
    assert "option_source_profile: alpha=fixture,beta=fixture (default fixture)" in out
    assert "OptionSlatePlanner: PlanningService" in out
    assert "[ok  ] option_sources: alpha -> fixture: fixture" in out
