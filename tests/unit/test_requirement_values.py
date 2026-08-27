"""Merge precedence, immutability and the digest: everything ``RequirementSet`` promises."""

from __future__ import annotations

from datetime import date

import pytest

from tourganize.domain.errors import InvariantViolationError, UnknownFieldError
from tourganize.domain.options import Money
from tourganize.domain.requirements import (
    PRECEDENCE,
    BlockingRule,
    DateRange,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    RequirementSet,
    RequirementSource,
    RequirementUpdate,
    RequirementValue,
    SupersededValue,
    Supersession,
)

SCHEMA = RequirementSchema(
    schema_key="alpha.v1",
    component_kind="alpha",
    fields=(
        FieldSpec("place", FieldKind.PLACE, Obligation.BLOCKING, "ask.alpha.place"),
        FieldSpec("date_range", FieldKind.DATE_RANGE, Obligation.OPTIONAL, "ask.alpha.range"),
        FieldSpec(
            "party_size",
            FieldKind.INTEGER,
            Obligation.OPTIONAL,
            "ask.alpha.party_size",
            constraints={"min": 1, "max": 12},
        ),
        FieldSpec("budget_ceiling", FieldKind.MONEY, Obligation.OPTIONAL, "ask.alpha.budget"),
    ),
    blocking_rules=(BlockingRule("where", (("place",),)),),
)

EMPTY = RequirementSet.empty("alpha")


def update(
    name: str,
    value: object,
    source: RequirementSource = RequirementSource.USER,
    turn_index: int = 0,
) -> RequirementUpdate:
    return RequirementUpdate(name, value, source=source, turn_index=turn_index)


def test_an_empty_set_knows_nothing() -> None:
    assert len(EMPTY) == 0
    assert "place" not in EMPTY
    assert EMPTY.value_of("place") is None
    assert EMPTY.provenance_of("place") is None
    assert EMPTY.superseded == ()


def test_an_update_is_stored_normalised_with_its_provenance() -> None:
    merged = EMPTY.with_updates(
        [update("place", "  Paris ", RequirementSource.INFERRED, 3)], schema=SCHEMA
    )

    held = merged.provenance_of("place")
    assert held is not None
    assert held.field_name == "place"
    assert held.value == "Paris"
    assert held.source is RequirementSource.INFERRED
    assert held.turn_index == 3


def test_with_updates_never_touches_the_receiver() -> None:
    """Identity *and* content: a turn that is abandoned must leave no trace behind it."""
    first = EMPTY.with_updates([update("place", "Paris")], schema=SCHEMA)

    second = first.with_updates([update("place", "Lisbon", turn_index=1)], schema=SCHEMA)

    assert second is not first
    assert first.value_of("place") == "Paris"
    assert first.superseded == ()
    assert second.value_of("place") == "Lisbon"
    assert dict(first.values) != dict(second.values)


def test_a_set_is_frozen_and_its_values_are_read_only() -> None:
    held = EMPTY.with_updates([update("place", "Paris")], schema=SCHEMA)

    with pytest.raises(AttributeError):
        held.component_kind = "beta"  # type: ignore[misc]
    with pytest.raises(TypeError):
        held.values["place"] = None  # type: ignore[index]


#: Every ordered pair of sources, and whether the second replaces the first on a later turn.
#: USER beats everything, INFERRED beats only DEFAULT and CARRIED_OVER, CARRIED_OVER beats a
#: DEFAULT, and a DEFAULT beats nothing that was not also a DEFAULT.
REPLACEMENTS = [
    (RequirementSource.USER, RequirementSource.USER, True),
    (RequirementSource.USER, RequirementSource.INFERRED, False),
    (RequirementSource.USER, RequirementSource.DEFAULT, False),
    (RequirementSource.USER, RequirementSource.CARRIED_OVER, False),
    (RequirementSource.INFERRED, RequirementSource.USER, True),
    (RequirementSource.INFERRED, RequirementSource.INFERRED, True),
    (RequirementSource.INFERRED, RequirementSource.DEFAULT, False),
    (RequirementSource.INFERRED, RequirementSource.CARRIED_OVER, False),
    (RequirementSource.CARRIED_OVER, RequirementSource.USER, True),
    (RequirementSource.CARRIED_OVER, RequirementSource.INFERRED, True),
    (RequirementSource.CARRIED_OVER, RequirementSource.CARRIED_OVER, True),
    (RequirementSource.CARRIED_OVER, RequirementSource.DEFAULT, False),
    (RequirementSource.DEFAULT, RequirementSource.USER, True),
    (RequirementSource.DEFAULT, RequirementSource.INFERRED, True),
    (RequirementSource.DEFAULT, RequirementSource.CARRIED_OVER, True),
    (RequirementSource.DEFAULT, RequirementSource.DEFAULT, True),
]


@pytest.mark.parametrize(
    ("standing", "incoming", "replaces"),
    REPLACEMENTS,
    ids=[f"{a.value}->{b.value}" for a, b, _ in REPLACEMENTS],
)
def test_merge_precedence_across_every_pair_of_sources(
    standing: RequirementSource, incoming: RequirementSource, replaces: bool
) -> None:
    first = EMPTY.with_updates([update("place", "Paris", standing, 0)], schema=SCHEMA)

    merged = first.with_updates([update("place", "Lisbon", incoming, 1)], schema=SCHEMA)

    assert merged.value_of("place") == ("Lisbon" if replaces else "Paris")


def test_a_user_value_overwrites_an_inferred_one_and_the_inferred_one_is_kept() -> None:
    inferred = EMPTY.with_updates(
        [update("place", "Paris", RequirementSource.INFERRED, 0)], schema=SCHEMA
    )

    merged = inferred.with_updates(
        [update("place", "Lisbon", RequirementSource.USER, 1)], schema=SCHEMA
    )

    assert merged.value_of("place") == "Lisbon"
    assert [entry.held.value for entry in merged.superseded] == ["Paris"]
    assert merged.superseded[0].held.source is RequirementSource.INFERRED
    assert merged.superseded[0].outcome is Supersession.REPLACED


def test_an_inferred_value_does_not_overwrite_a_user_one_and_is_still_recorded() -> None:
    """Nothing is dropped: the contradiction is evidence a refinement may have to explain."""
    stated = EMPTY.with_updates(
        [update("place", "Paris", RequirementSource.USER, 0)], schema=SCHEMA
    )

    merged = stated.with_updates(
        [update("place", "Lisbon", RequirementSource.INFERRED, 5)], schema=SCHEMA
    )

    assert merged.value_of("place") == "Paris"
    assert [entry.held.value for entry in merged.superseded] == ["Lisbon"]
    assert merged.superseded_for("place")[0].held.source is RequirementSource.INFERRED
    assert merged.superseded_for("place")[0].outcome is Supersession.OVERRULED
    assert merged.superseded_for("date_range") == ()


def test_a_replaced_value_and_an_overruled_one_are_told_apart() -> None:
    """F05 explains a refinement from this: a value never held is not a value once held."""
    stated = EMPTY.with_updates(
        [update("place", "Paris", RequirementSource.USER, 0)], schema=SCHEMA
    )

    merged = stated.with_updates(
        [
            update("place", "Lisbon", RequirementSource.INFERRED, 1),
            update("place", "Rome", RequirementSource.USER, 2),
        ],
        schema=SCHEMA,
    )

    assert merged.value_of("place") == "Rome"
    assert [(entry.held.value, entry.outcome) for entry in merged.superseded] == [
        ("Lisbon", Supersession.OVERRULED),
        ("Paris", Supersession.REPLACED),
    ]
    replaced = [
        entry.held.value for entry in merged.superseded if entry.outcome is Supersession.REPLACED
    ]
    assert replaced == ["Paris"]


def test_a_superseded_entry_is_filed_under_the_field_its_value_names() -> None:
    entry = SupersededValue(
        RequirementValue("place", "Paris", RequirementSource.USER, 0), Supersession.REPLACED
    )

    assert entry.field_name == "place"


@pytest.mark.parametrize(
    ("held", "outcome", "reason"),
    [
        ("Paris", Supersession.REPLACED, "must be a RequirementValue"),
        (
            RequirementValue("place", "Paris", RequirementSource.USER, 0),
            "replaced",
            "must be a Supersession",
        ),
    ],
)
def test_a_malformed_superseded_entry_is_refused(
    held: object, outcome: object, reason: str
) -> None:
    with pytest.raises(InvariantViolationError) as raised:
        SupersededValue(held, outcome)  # type: ignore[arg-type]

    assert reason in str(raised.value)


def test_a_later_turn_of_the_same_source_wins() -> None:
    first = EMPTY.with_updates([update("place", "Paris", turn_index=1)], schema=SCHEMA)

    later = first.with_updates([update("place", "Lisbon", turn_index=4)], schema=SCHEMA)
    earlier = first.with_updates([update("place", "Rome", turn_index=0)], schema=SCHEMA)

    assert later.value_of("place") == "Lisbon"
    assert earlier.value_of("place") == "Paris"


def test_within_one_turn_the_last_update_wins() -> None:
    """A correction in mid-sentence — "Paris, no, Lisbon" — is one turn, and the last one meant."""
    first = EMPTY.with_updates([update("place", "Paris", turn_index=2)], schema=SCHEMA)

    same_turn = first.with_updates([update("place", "Lisbon", turn_index=2)], schema=SCHEMA)

    assert same_turn.value_of("place") == "Lisbon"
    assert same_turn.superseded[0].outcome is Supersession.REPLACED


def test_the_precedence_table_is_the_whole_rule() -> None:
    assert PRECEDENCE[RequirementSource.USER] > PRECEDENCE[RequirementSource.INFERRED]
    assert PRECEDENCE[RequirementSource.INFERRED] > PRECEDENCE[RequirementSource.CARRIED_OVER]
    assert PRECEDENCE[RequirementSource.CARRIED_OVER] > PRECEDENCE[RequirementSource.DEFAULT]
    assert set(PRECEDENCE) == set(RequirementSource)


def test_an_unknown_field_raises_rather_than_being_ignored() -> None:
    """Usually it means an extraction prompt and a schema have drifted apart."""
    with pytest.raises(UnknownFieldError) as raised:
        EMPTY.with_updates([update("nowhere", "x")], schema=SCHEMA)

    assert "nowhere" in str(raised.value)
    assert "place" in str(raised.value)


def test_an_invalid_value_is_stored_so_the_gap_report_can_ask_about_it() -> None:
    merged = EMPTY.with_updates([update("party_size", "a few")], schema=SCHEMA)

    assert merged.value_of("party_size") == "a few"


def test_a_schema_for_another_kind_is_refused() -> None:
    other = RequirementSchema("beta.v1", "beta", (SCHEMA.fields[0],))

    with pytest.raises(InvariantViolationError) as raised:
        EMPTY.with_updates([update("place", "Paris")], schema=other)

    assert "beta" in str(raised.value)


def test_several_updates_merge_in_the_order_they_arrive() -> None:
    merged = EMPTY.with_updates(
        [
            update("place", "Paris"),
            update("party_size", 2),
            update("place", "Lisbon", turn_index=1),
        ],
        schema=SCHEMA,
    )

    assert merged.value_of("place") == "Lisbon"
    assert merged.value_of("party_size") == 2
    assert len(merged.superseded) == 1


def test_the_digest_depends_on_the_values_and_nothing_else() -> None:
    """F06 seeds a deterministic search with it: same requirement, same slate."""
    stated = EMPTY.with_updates(
        [update("place", "Paris", RequirementSource.USER, 0)], schema=SCHEMA
    )
    inferred = EMPTY.with_updates(
        [update("place", "  Paris  ", RequirementSource.INFERRED, 7)], schema=SCHEMA
    )

    assert stated.digest() == inferred.digest()
    assert len(stated.digest()) == 16


def test_the_digest_changes_when_a_requirement_changes() -> None:
    paris = EMPTY.with_updates([update("place", "Paris")], schema=SCHEMA)
    lisbon = EMPTY.with_updates([update("place", "Lisbon")], schema=SCHEMA)

    assert paris.digest() != lisbon.digest()
    assert EMPTY.digest() != paris.digest()


def test_the_digest_does_not_depend_on_the_order_updates_arrived_in() -> None:
    one = EMPTY.with_updates([update("place", "Paris"), update("party_size", 2)], schema=SCHEMA)
    other = EMPTY.with_updates([update("party_size", 2), update("place", "Paris")], schema=SCHEMA)

    assert one.digest() == other.digest()


def test_the_digest_does_not_depend_on_a_python_spelling() -> None:
    """A stored session (F12) round-trips values through text; the digest must survive it."""
    typed = EMPTY.with_updates(
        [
            update("date_range", DateRange(date(2026, 10, 23), date(2026, 10, 28))),
            update("budget_ceiling", Money(74000, "EUR")),
        ],
        schema=SCHEMA,
    )
    spelled = EMPTY.with_updates(
        [update("date_range", "2026-10-23/2026-10-28"), update("budget_ceiling", "74000 EUR")],
        schema=SCHEMA,
    )

    assert typed.digest() == spelled.digest()


def test_the_digest_renders_every_normalised_value_type() -> None:
    """Booleans, durations, numbers and anything else all have to hash to *something* stable."""
    schema = RequirementSchema(
        "alpha.v1",
        "alpha",
        (
            FieldSpec("breakfast", FieldKind.BOOLEAN, Obligation.OPTIONAL, "ask.alpha.breakfast"),
            FieldSpec("starts_on", FieldKind.DATE, Obligation.OPTIONAL, "ask.alpha.starts_on"),
            FieldSpec("max_transfer", FieldKind.DURATION, Obligation.OPTIONAL, "ask.alpha.max"),
            FieldSpec("min_rating", FieldKind.SCORE, Obligation.OPTIONAL, "ask.alpha.rating"),
            FieldSpec("note", FieldKind.TEXT, Obligation.OPTIONAL, "ask.alpha.note"),
        ),
    )
    held = RequirementSet.empty("alpha").with_updates(
        [
            RequirementUpdate("breakfast", True),
            RequirementUpdate("starts_on", date(2026, 10, 23)),
            RequirementUpdate("max_transfer", "2h"),
            RequirementUpdate("min_rating", 8.7),
            # Kept verbatim because it fails its own validation — the digest still has to cope.
            RequirementUpdate("note", ["not", "text"]),
        ],
        schema=schema,
    )

    assert len(held.digest()) == 16
    assert held.digest() == held.digest()


def test_the_digest_does_not_collide_when_a_value_contains_a_separator() -> None:
    """`{"a": "b\\nc=d"}` and `{"a": "b", "c": "d"}` once hashed the same. F06 seeds a search
    with this, so two different requirements sharing a digest would answer the second with the
    first one's slate."""
    schema = RequirementSchema(
        "alpha.v1",
        "alpha",
        (
            FieldSpec("a", FieldKind.TEXT, Obligation.BLOCKING, "ask.alpha.a"),
            FieldSpec("c", FieldKind.TEXT, Obligation.OPTIONAL, "ask.alpha.c"),
        ),
        (BlockingRule("both", (("a",),)),),
    )
    empty = RequirementSet.empty("alpha")

    one = empty.with_updates([RequirementUpdate("a", "b\nc=d")], schema=schema)
    other = empty.with_updates(
        [RequirementUpdate("a", "b"), RequirementUpdate("c", "d")], schema=schema
    )

    assert one.digest() != other.digest()


def test_the_digest_distinguishes_component_kinds() -> None:
    beta_schema = RequirementSchema("beta.v1", "beta", SCHEMA.fields, SCHEMA.blocking_rules)
    alpha = EMPTY.with_updates([update("place", "Paris")], schema=SCHEMA)
    beta = RequirementSet.empty("beta").with_updates([update("place", "Paris")], schema=beta_schema)

    assert alpha.digest() != beta.digest()


@pytest.mark.parametrize(
    ("keyword", "reason"),
    [
        ({"field_name": ""}, "must be a non-empty string"),
        ({"source": "user"}, "source must be a RequirementSource"),
        ({"turn_index": "1"}, "turn_index must be an integer"),
        ({"turn_index": True}, "turn_index must be an integer"),
        ({"turn_index": -1}, "must not be negative"),
        ({"confidence": 1.5}, "confidence must be between 0 and 1"),
    ],
)
def test_a_malformed_requirement_value_is_refused(keyword: dict[str, object], reason: str) -> None:
    declared: dict[str, object] = {
        "field_name": "place",
        "value": "Paris",
        "source": RequirementSource.USER,
        "turn_index": 0,
    }
    declared.update(keyword)

    with pytest.raises(InvariantViolationError) as raised:
        RequirementValue(**declared)  # type: ignore[arg-type]

    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("values", "superseded", "reason"),
    [
        ("not a mapping", (), "values must be a mapping"),
        ({"place": "Paris"}, (), "expected a RequirementValue"),
        (
            {"elsewhere": RequirementValue("place", "Paris", RequirementSource.USER, 0)},
            (),
            "names field 'place'",
        ),
        ({}, ["not a tuple"], "superseded must be a tuple"),
        (
            {},
            (RequirementValue("place", "Paris", RequirementSource.USER, 0),),
            "must hold SupersededValue entries",
        ),
    ],
)
def test_a_malformed_requirement_set_is_refused(
    values: object, superseded: object, reason: str
) -> None:
    with pytest.raises(InvariantViolationError) as raised:
        RequirementSet("alpha", values, superseded)  # type: ignore[arg-type]

    assert reason in str(raised.value)


def test_a_requirement_update_needs_a_field_name() -> None:
    with pytest.raises(InvariantViolationError):
        RequirementUpdate("", "Paris")


def test_an_update_may_carry_the_travellers_own_words() -> None:
    """Recommended by the spec so a re-ask can quote what was actually said."""
    raw = RequirementUpdate("place", "Paris", raw_text="somewhere in Paris please")

    assert raw.raw_text == "somewhere in Paris please"
    assert raw.source is RequirementSource.USER
    assert raw.turn_index == 0


def test_a_requirement_value_reports_its_rank() -> None:
    held = RequirementValue("place", "Paris", RequirementSource.DEFAULT, 0)

    assert held.rank == PRECEDENCE[RequirementSource.DEFAULT]
