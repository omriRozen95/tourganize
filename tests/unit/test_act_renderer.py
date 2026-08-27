"""The Act Renderer: one Assistant Act and a Locale Tag in, one ``RenderedAct`` out.

Three properties carry most of this file, and each is a place where "be forgiving" and "be
honest" pull against each other.

A missing message key or an unfillable ``{placeholder}`` is a **visible marker**, never an
exception and never a blank line — a demonstration that quietly loses a question is worse
than one that shows where the question should have been. A *label* has no such marker: it
falls back to its own name and only whispers about it, because that is what lets a fourth
Component Kind render a table with no edit to any file. And a missing message *file* is
neither: it is a broken installation, so it raises.

The two halves of the suite read differently on purpose. The machinery is exercised against
small hand-written catalogues with neutral ``kind_key``s, so that a test about substitution
does not have to name a travel topic; the "it really renders" cases run the shipped
``config/messages`` against the shipped Component Catalog, and read the Kinds, fields and
columns out of that data rather than spelling any of them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

import pytest
from conftest import schemas_dir

from tourganize.adapters.catalog.memory import InMemoryComponentCatalog
from tourganize.adapters.catalog.yaml import YamlComponentCatalog
from tourganize.dialogue import (
    ACT_VOCABULARY,
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLARIFY,
    CLARIFY_STILL_MISSING,
    CLARIFY_UNRESOLVED_CHOICE,
    CLOSE,
    CONFIRM_SELECTION,
    DELIVER_SUMMARY,
    GREET,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_INVALID_VALUE,
    REPORT_SOURCING_FAILURE,
    SOURCING_FAILED,
    AssistantAct,
)
from tourganize.domain.catalog import ComponentKind
from tourganize.domain.requirements import Obligation
from tourganize.language import (
    MISSING_MARKER_OPEN,
    ActRenderer,
    RenderedAct,
    load_display_profiles,
    load_message_catalogue,
    missing_marker,
)
from tourganize.platform.errors import ConfigurationError
from tourganize.ports.catalog import ComponentCatalog

LOGGER_NAME: Final = "tourganize.language.act_renderer"

#: The Locale Tag the hand-written files in this module declare. Deliberately not one the
#: application ships, so that a test reading a fixture can never be reading `config/` instead.
LOCALE: Final = "xx"

#: The locales the application ships a Message Catalogue for.
SHIPPED_LOCALES: Final = ("en", "he")

#: One Plan Option's price, as ``_option_payload`` puts it on an Act. 74000 minor units at two
#: digits is ``740.00``, which is the arithmetic the money format has to get right.
PRICE: Final[Mapping[str, object]] = {"amount_minor": 74_000, "currency": "EUR"}

#: Enough of a catalogue for the machinery: the small words the renderer itself needs, plus one
#: phrased Component Kind. Neutral keys, for the reason ``SAMPLE_CATALOG`` has them.
BASE_MESSAGES: Final[Mapping[str, str]] = {
    "greet": "hello",
    "close": "goodbye",
    "present_slate": "here are {count}",
    "component.alpha": "the first thing",
    "field.budget_ceiling": "budget",
    "list.separator": ", ",
    "money.format": "{amount} {currency}",
    "value.true": "yes",
    "value.false": "no",
    "value.none": "-",
}


# -- building the world a test needs ---------------------------------------------------------


def catalog() -> InMemoryComponentCatalog:
    """Two neutral Component Kinds. ``beta`` is declared but deliberately never phrased."""
    return InMemoryComponentCatalog(
        (
            ComponentKind("alpha", "component.alpha", 300, "alpha.v1"),
            ComponentKind("beta", "component.beta", 200, "beta.v1"),
        )
    )


def message_file(
    messages: Mapping[str, str], *, locale: str = LOCALE, direction: str = "ltr"
) -> str:
    """A Message Catalogue as text. Values are quoted, so a ``{placeholder}`` survives YAML."""
    declared = "\n".join(f'  {key}: "{phrasing}"' for key, phrasing in messages.items())
    return f"locale: {locale}\ndirection: {direction}\n\nmessages:\n{declared}\n"


def write_files(
    directory: Path, *, messages: str | None = None, display: str | None = None
) -> Path:
    """Write a Message Catalogue and/or a Display Profile file for :data:`LOCALE`."""
    directory.mkdir(parents=True, exist_ok=True)
    if messages is not None:
        (directory / f"{LOCALE}.yaml").write_text(messages, encoding="utf-8")
    if display is not None:
        (directory / f"display.{LOCALE}.yaml").write_text(display, encoding="utf-8")
    return directory


def renderer_for(
    tmp_path: Path,
    messages: Mapping[str, str] | None = None,
    *,
    display: str | None = None,
) -> ActRenderer:
    """An ``ActRenderer`` over hand-written files, in :data:`LOCALE`."""
    directory = write_files(
        tmp_path / "messages",
        messages=message_file(BASE_MESSAGES if messages is None else messages),
        display=display,
    )
    return ActRenderer(directory, catalog())


def option_payload(
    option_id: str,
    *,
    price: Mapping[str, object] | None = None,
    facts: Mapping[str, object] | None = None,
    filter_notes: Sequence[str] = (),
) -> Mapping[str, object]:
    """One Plan Option exactly as the Dialogue Director puts it on a ``present_slate``."""
    return {
        "option_id": option_id,
        "price": price,
        "facts": {} if facts is None else dict(facts),
        "source_id": "fixture:test",
        "filter_notes": tuple(filter_notes),
    }


def slate(
    *options: Mapping[str, object], kind_key: str = "alpha", locale: str = LOCALE
) -> AssistantAct:
    """A ``present_slate`` Act carrying ``options``."""
    return AssistantAct(
        act=PRESENT_SLATE,
        payload={
            "round_index": 0,
            "option_ids": tuple(str(option["option_id"]) for option in options),
            "options": options,
            "requirements_digest": "d1",
        },
        locale=locale,
        kind_key=kind_key,
    )


def markers_in(rendered: RenderedAct) -> tuple[str, ...]:
    """Every rendered string that came out as a ⟪missing:…⟫ marker."""
    texts = [rendered.heading or "", *rendered.lines]
    for row in rendered.option_rows:
        texts += [row.option_id, row.price or "", *row.filter_notes]
        texts += [text for cell in row.cells for text in cell]
    return tuple(text for text in texts if MISSING_MARKER_OPEN in text)


# -- the shipped data ------------------------------------------------------------------------


def shipped_catalog() -> YamlComponentCatalog:
    """The Component Catalog the application ships with, as ``Settings`` resolves it."""
    config = Path("config")
    return YamlComponentCatalog(config / "catalog" / "components.yaml", schemas_dir(config))


def shipped_renderer(catalog_: ComponentCatalog) -> ActRenderer:
    """A renderer over the shipped ``config/messages``, told which locales exist."""
    return ActRenderer(
        Path("config") / "messages",
        catalog_,
        supported_locales=SHIPPED_LOCALES,
        default_locale=SHIPPED_LOCALES[0],
    )


def shipped_acts(
    renderer: ActRenderer, catalog_: ComponentCatalog, locale: str
) -> tuple[AssistantAct, ...]:
    """One realistic Act per entry of :data:`ACT_VOCABULARY`, in the shipped Kinds' terms.

    Every ``kind_key``, field name, prompt key and option fact is read out of the shipped
    catalog, schemas and Display Profiles rather than written here: naming a travel topic in a
    test would be the same mistake as naming one in the package, and this way the payloads stay
    true when the shipped data changes.
    """
    kinds = catalog_.enabled_kinds()
    primary = kinds[0].kind_key
    others = tuple(kind.kind_key for kind in kinds[1:])
    schema = catalog_.schema_for(primary)
    blocking = tuple(spec for spec in schema.fields if spec.obligation is Obligation.BLOCKING)
    optional = tuple(spec for spec in schema.fields if spec.obligation is Obligation.OPTIONAL)
    columns = renderer.profiles(locale).for_kind(primary).columns
    options = tuple(
        option_payload(
            f"opt-{number}",
            price=PRICE,
            facts={column.fact: f"{column.fact} {number}" for column in columns},
        )
        for number in (1, 2)
    )
    return (
        AssistantAct(act=GREET, payload={}, locale=locale),
        AssistantAct(
            act=ASK_BLOCKING,
            payload={
                "rule_name": schema.blocking_rules[0].name,
                "field_groups": schema.blocking_rules[0].any_of,
                "preferred_fields": tuple(spec.name for spec in blocking[:1]),
                "prompt_message_keys": tuple(spec.prompt_message_key for spec in blocking[:1]),
                "schema_key": schema.schema_key,
                "attempt": 1,
            },
            locale=locale,
            kind_key=primary,
        ),
        AssistantAct(
            act=ASK_OPTIONAL,
            payload={
                "field_names": tuple(spec.name for spec in optional[:2]),
                "prompt_message_keys": tuple(spec.prompt_message_key for spec in optional[:2]),
            },
            locale=locale,
            kind_key=primary,
        ),
        AssistantAct(
            act=REPORT_INVALID_VALUE,
            payload={
                "field_name": blocking[0].name,
                "reason_message_key": "requirement.invalid.not_a_date",
                "attempt": 2,
            },
            locale=locale,
            kind_key=primary,
        ),
        slate(*options, kind_key=primary, locale=locale),
        AssistantAct(
            act=CONFIRM_SELECTION,
            payload={"option_id": "opt-1", "round_index": 0, "noted_kinds": others[:1]},
            locale=locale,
            kind_key=primary,
        ),
        AssistantAct(
            act=OFFER_UNMENTIONED,
            payload={
                "kind_keys": others[:1],
                "message_keys": tuple(catalog_.kind(key).message_key for key in others[:1]),
                "remaining": len(others[1:]),
            },
            locale=locale,
        ),
        AssistantAct(
            act=DELIVER_SUMMARY,
            payload={
                "selected": (primary,),
                "declined": others[:1],
                "open": others[1:2],
                "open_mentioned": (),
                "is_closeable": True,
                "selections": ({"kind_key": primary, "option_id": "opt-1", "round_index": 0},),
            },
            locale=locale,
        ),
        AssistantAct(
            act=CLARIFY,
            payload={
                "reason_code": CLARIFY_UNRESOLVED_CHOICE,
                "given": "the cheap one",
                "option_ids": ("opt-1", "opt-2"),
            },
            locale=locale,
            kind_key=primary,
        ),
        AssistantAct(
            act=REPORT_SOURCING_FAILURE,
            payload={
                "reason_code": SOURCING_FAILED,
                "round_index": 0,
                "consecutive_failures": 1,
            },
            locale=locale,
            kind_key=primary,
        ),
        AssistantAct(act=CLOSE, payload={}, locale=locale),
    )


@pytest.mark.parametrize("locale", SHIPPED_LOCALES)
def test_every_act_renders_a_heading_from_the_shipped_catalogue(locale: str) -> None:
    """The whole Act vocabulary, in every shipped locale, with nothing missing.

    This is the F07 promise stated as one assertion: the Director's closed vocabulary and the
    shipped Message Catalogue agree, in Hebrew as well as in English, for realistic payloads —
    so a conversation reaches the traveller as sentences rather than as ⟪missing:…⟫ markers.
    """
    declared = shipped_catalog()
    renderer = shipped_renderer(declared)

    rendered = [renderer.render(act) for act in shipped_acts(renderer, declared, locale)]

    assert {one.act for one in rendered} == ACT_VOCABULARY
    for one in rendered:
        assert one.heading, f"{one.act} rendered no heading"
        assert markers_in(one) == (), f"{one.act} rendered a marker"


@pytest.mark.parametrize(("locale", "direction"), [("en", "ltr"), ("he", "rtl")])
def test_a_shipped_locale_renders_in_the_direction_it_declares(locale: str, direction: str) -> None:
    """``direction`` is declared in the file, never guessed from the tag."""
    declared = shipped_catalog()
    renderer = shipped_renderer(declared)

    assert renderer.direction(locale) == direction
    assert (
        renderer.render(AssistantAct(act=GREET, payload={}, locale=locale)).direction == direction
    )


def test_a_shipped_slate_is_numbered_and_priced() -> None:
    """The shipped Display Profiles really lay a table out, in their own declared order."""
    declared = shipped_catalog()
    renderer = shipped_renderer(declared)
    primary = declared.enabled_kinds()[0].kind_key
    columns = renderer.profiles("en").for_kind(primary).columns
    facts = {column.fact: f"{column.fact} value" for column in columns}

    rendered = renderer.render(
        slate(
            option_payload("a-1", price=PRICE, facts=facts),
            option_payload("a-2", price=PRICE, facts=facts),
            kind_key=primary,
            locale="en",
        )
    )

    assert [row.number for row in rendered.option_rows] == [1, 2]
    assert [row.option_id for row in rendered.option_rows] == ["a-1", "a-2"]
    assert all(row.price == "740.00 EUR" for row in rendered.option_rows)
    assert len(rendered.option_rows[0].cells) == len(columns)


# -- markers: what a hole in the catalogue looks like -----------------------------------------


def test_a_message_key_nobody_declares_renders_a_marker_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A key a translator has not reached yet is visible, and does not end the session."""
    renderer = renderer_for(
        tmp_path, {key: value for key, value in BASE_MESSAGES.items() if key != "close"}
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rendered = renderer.render(AssistantAct(act=CLOSE, payload={}, locale=LOCALE))

    assert rendered.heading == missing_marker("close")
    assert "close" in caplog.text
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_a_placeholder_the_payload_cannot_fill_renders_a_marker_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """``str.format`` would raise ``KeyError`` here; a typo must not end a conversation."""
    renderer = renderer_for(tmp_path, {**BASE_MESSAGES, "greet": "hello {nobody}"})

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rendered = renderer.render(AssistantAct(act=GREET, payload={}, locale=LOCALE))

    assert rendered.heading == f"hello {missing_marker('nobody')}"
    assert "nobody" in caplog.text
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        pytest.param("{{braced}}", "{braced}", id="doubled-braces-are-literal"),
        pytest.param("a stray { brace", "a stray { brace", id="a-lone-open-brace-survives"),
        pytest.param("a stray } brace", "a stray } brace", id="a-lone-close-brace-survives"),
        pytest.param("{ and then {count}", "{ and then 0", id="a-brace-before-a-placeholder"),
        pytest.param("{}", "{}", id="an-empty-placeholder-is-left-alone"),
        pytest.param("{count}{count}", "00", id="two-placeholders-in-a-row"),
    ],
)
def test_hand_written_braces_are_answered_rather_than_raised_at(
    tmp_path: Path, template: str, expected: str
) -> None:
    """Message files are edited by hand, so every brace a translator can type has an answer."""
    renderer = renderer_for(tmp_path, {**BASE_MESSAGES, "present_slate": template})

    rendered = renderer.render(slate())

    assert rendered.heading == expected


def test_a_label_falls_back_to_its_own_name_and_only_whispers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unphrased column heading is a nicety nobody wrote, not a hole in the conversation.

    That distinction is the whole reason a fourth Component Kind renders with no config edit,
    so the fallback is asserted together with the log level that says it is not an emergency.
    """
    renderer = renderer_for(tmp_path)

    with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
        rendered = renderer.render(slate(option_payload("a-1", facts={"wingspan": 3})))

    assert rendered.option_rows[0].cells == (("wingspan", "3"),)
    about_the_label = [one for one in caplog.records if "wingspan" in one.getMessage()]
    assert about_the_label
    assert {one.levelno for one in about_the_label} == {logging.DEBUG}


def test_a_component_kind_the_catalog_does_not_declare_renders_as_nothing(tmp_path: Path) -> None:
    """A resumed plan may name a Kind a since-edited catalog has dropped. That is not a marker."""
    renderer = renderer_for(tmp_path, {**BASE_MESSAGES, "close": "about {component}."})

    rendered = renderer.render(AssistantAct(act=CLOSE, payload={}, locale=LOCALE, kind_key="delta"))

    assert rendered.heading == "about ."


# -- key lookup: most specific first -----------------------------------------------------------


LOOKUP_MESSAGES: Final[Mapping[str, str]] = {
    **BASE_MESSAGES,
    "clarify": "the generic line",
    "clarify.alpha": "the line this Kind asked for",
    "clarify.not_understood": "the line this reason asked for",
}


@pytest.mark.parametrize(
    ("payload", "kind_key", "expected"),
    [
        pytest.param(
            {"reason_code": "not_understood"},
            "alpha",
            "the line this reason asked for",
            id="a-reason-code-beats-a-kind",
        ),
        pytest.param({}, "alpha", "the line this Kind asked for", id="a-kind-beats-the-act"),
        pytest.param({}, None, "the generic line", id="the-act-is-the-last-resort"),
        pytest.param(
            {"reason_code": "unheard_of"},
            None,
            "the generic line",
            id="an-unphrased-reason-code-falls-through",
        ),
    ],
)
def test_a_key_is_looked_up_reason_code_then_kind_then_act(
    tmp_path: Path, payload: Mapping[str, object], kind_key: str | None, expected: str
) -> None:
    """One rule for every Act: a Kind or a reason that wants its own wording writes one line."""
    renderer = renderer_for(tmp_path, LOOKUP_MESSAGES)

    rendered = renderer.render(
        AssistantAct(act=CLARIFY, payload=payload, locale=LOCALE, kind_key=kind_key)
    )

    assert rendered.heading == expected


# -- the option table --------------------------------------------------------------------------


PROFILED_DISPLAY: Final = """\
locale: xx

money: {minor_digits: 2}

default:
  columns: []
  show_price: true

kinds:
  alpha:
    columns:
      - {fact: second}
      - {fact: first, unit: "/10"}
      - {fact: never_declared}
    show_price: true
"""


def test_option_rows_are_numbered_from_one_in_payload_order(tmp_path: Path) -> None:
    """Numbering is the payload's own order, and it counts every entry.

    A traveller reads "2" as "the second thing you showed me" and the Director reads
    ``slate.options[1]``, and those two must not be able to disagree.
    """
    renderer = renderer_for(tmp_path, display=PROFILED_DISPLAY)

    rendered = renderer.render(slate(*(option_payload(f"a-{number}") for number in (3, 1, 2))))

    assert [(row.number, row.option_id) for row in rendered.option_rows] == [
        (1, "a-3"),
        (2, "a-1"),
        (3, "a-2"),
    ]


def test_columns_follow_the_profile_and_skip_a_fact_the_option_lacks(tmp_path: Path) -> None:
    """Profile order, not fact order — and a column naming a fact nobody supplied is dropped."""
    renderer = renderer_for(tmp_path, display=PROFILED_DISPLAY)

    rendered = renderer.render(
        slate(option_payload("a-1", facts={"first": 8.7, "second": "b", "third": "c"}))
    )

    assert rendered.option_rows[0].cells == (("second", "b"), ("first", "8.7/10"))


def test_a_kind_with_no_display_profile_shows_every_declared_fact_in_order(
    tmp_path: Path,
) -> None:
    """The property F02 and F06 bought, spent here: an unconfigured Kind still renders a table.

    ``beta`` is declared by the catalog and named by neither the Display Profiles nor a single
    ``fact.*`` message, which is exactly the state a fourth Component Kind is in on the day it
    is added. It gets every fact the source declared, in declaration order, headed by the raw
    fact names — no Python change and no config edit.
    """
    renderer = renderer_for(tmp_path, display=PROFILED_DISPLAY)
    facts: Mapping[str, object] = {"gamma": 1, "alpha": 2, "beta": 3}

    rendered = renderer.render(slate(option_payload("b-1", facts=facts), kind_key="beta"))

    assert rendered.option_rows[0].cells == (("gamma", "1"), ("alpha", "2"), ("beta", "3"))


def test_filter_notes_become_phrased_field_labels(tmp_path: Path) -> None:
    """F06's soft filtering made visible: the option that misses a filter says which one.

    A Filter Note is a field name, never a reason — so the renderer phrases the name and stops
    there, falling back to the name itself for a field nobody has written a label for.
    """
    renderer = renderer_for(tmp_path, display=PROFILED_DISPLAY)

    rendered = renderer.render(
        slate(option_payload("a-1", filter_notes=("budget_ceiling", "min_rating")))
    )

    assert rendered.option_rows[0].filter_notes == ("budget", "min_rating")


ZERO_DIGIT_DISPLAY: Final = """\
locale: xx

money: {minor_digits: 0}

default:
  columns: []
  show_price: true
"""

UNPRICED_DISPLAY: Final = """\
locale: xx

money: {minor_digits: 2}

default:
  columns: []
  show_price: false
"""


@pytest.mark.parametrize(
    ("display", "price", "expected"),
    [
        pytest.param(PROFILED_DISPLAY, PRICE, "740.00 EUR", id="74000-minor-units-at-two-digits"),
        pytest.param(
            PROFILED_DISPLAY,
            {"amount_minor": -74_000, "currency": "EUR"},
            "-740.00 EUR",
            id="a-negative-amount-keeps-its-sign",
        ),
        pytest.param(
            PROFILED_DISPLAY,
            {"amount_minor": 7, "currency": "ILS"},
            "0.07 ILS",
            id="less-than-one-major-unit",
        ),
        pytest.param(ZERO_DIGIT_DISPLAY, PRICE, "74000 EUR", id="a-currency-with-no-minor-units"),
        pytest.param(PROFILED_DISPLAY, None, None, id="an-option-with-no-price"),
        pytest.param(UNPRICED_DISPLAY, PRICE, None, id="a-profile-that-shows-no-price"),
        pytest.param(
            PROFILED_DISPLAY,
            {"amount_minor": "lots", "currency": "EUR"},
            None,
            id="a-price-that-cannot-be-read",
        ),
    ],
)
def test_a_price_is_the_locales_money_format_at_the_profiles_minor_digits(
    tmp_path: Path,
    display: str,
    price: Mapping[str, object] | None,
    expected: str | None,
) -> None:
    """Integer arithmetic, so the amount never drifts — and ``None`` for the three no-price cases.

    "the option has none", "the profile shows none" and "the payload's price is unreadable" are
    one answer on purpose: a surface leaves the column out for all three.
    """
    renderer = renderer_for(tmp_path, display=display)

    rendered = renderer.render(slate(option_payload("a-1", price=price)))

    assert rendered.option_rows[0].price == expected


def test_boolean_and_absent_facts_are_words_rather_than_repr(tmp_path: Path) -> None:
    """``True`` is not a word a traveller reads, so the catalogue owns what it says."""
    renderer = renderer_for(tmp_path)

    rendered = renderer.render(
        slate(option_payload("a-1", facts={"one": True, "two": False, "three": None}))
    )

    assert rendered.option_rows[0].cells == (("one", "yes"), ("two", "no"), ("three", "-"))


# -- the bodies of the Acts that have one --------------------------------------------------------


BODY_MESSAGES: Final[Mapping[str, str]] = {
    **BASE_MESSAGES,
    "component.beta": "the second thing",
    "ask_blocking": "one thing first:",
    "ask.alpha.place": "where?",
    "ask.alpha.date_range": "which dates?",
    "confirm_selection": "noted: {choice}.",
    "confirm_selection.noted": "also noted: {noted}.",
    "offer_unmentioned": "shall I plan {kinds}?",
    "offer_unmentioned.remaining": "{remaining} more after that.",
    "deliver_summary": "where the plan stands:",
    "deliver_summary.selection": "{component}: {option_id}",
    "deliver_summary.declined": "turned down: {declined}.",
    "deliver_summary.open": "still open: {open}.",
    "deliver_summary.empty": "nothing settled yet.",
    "clarify": "did not follow that.",
    "clarify.still_missing": "an example that helps:",
    "example.alpha.place": "for example: Somewhere.",
}


def test_a_blocking_question_asks_every_prompt_the_payload_names(tmp_path: Path) -> None:
    """One Act, one obligation — but a candidate group of two fields is two prompts."""
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=ASK_BLOCKING,
            payload={
                "rule_name": "when",
                "preferred_fields": ("place", "date_range"),
                "prompt_message_keys": ("ask.alpha.place", "ask.alpha.date_range"),
                "schema_key": "alpha.v1",
                "attempt": 1,
            },
            locale=LOCALE,
            kind_key="alpha",
        )
    )

    assert rendered.heading == "one thing first:"
    assert rendered.lines == ("where?", "which dates?")


def test_a_clarification_prefers_the_example_over_asking_again(tmp_path: Path) -> None:
    """The escalation the Director offers once a Blocking Rule has been asked about too often."""
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=CLARIFY,
            payload={
                "reason_code": CLARIFY_STILL_MISSING,
                "rule_name": "where",
                "field_names": ("place",),
                "example_message_keys": ("example.alpha.place",),
                "prompt_message_keys": ("ask.alpha.place",),
            },
            locale=LOCALE,
            kind_key="alpha",
        )
    )

    assert rendered.heading == "an example that helps:"
    assert rendered.lines == ("for example: Somewhere.",)


def test_a_clarification_falls_back_to_asking_again_when_no_example_exists(
    tmp_path: Path,
) -> None:
    """A field that declares no example is asked about again, which beats an empty escalation."""
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=CLARIFY,
            payload={
                "reason_code": CLARIFY_STILL_MISSING,
                "example_message_keys": (),
                "prompt_message_keys": ("ask.alpha.date_range",),
            },
            locale=LOCALE,
            kind_key="alpha",
        )
    )

    assert rendered.lines == ("which dates?",)


@pytest.mark.parametrize(
    ("noted_kinds", "expected"),
    [
        pytest.param(("beta",), ("also noted: the second thing.",), id="a-turn-that-raised-more"),
        pytest.param((), (), id="a-turn-that-raised-nothing-else"),
    ],
)
def test_a_confirmation_mentions_other_kinds_only_when_the_turn_raised_some(
    tmp_path: Path, noted_kinds: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    """An Act with nothing to say below its heading says nothing, rather than a blank line."""
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=CONFIRM_SELECTION,
            payload={"option_id": "a-1", "round_index": 0, "noted_kinds": noted_kinds},
            locale=LOCALE,
            kind_key="alpha",
        )
    )

    assert rendered.heading == "noted: a-1."
    assert rendered.lines == expected


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [
        pytest.param(2, ("2 more after that.",), id="more-are-waiting"),
        pytest.param(0, (), id="that-was-all-of-them"),
        pytest.param(True, (), id="a-boolean-is-not-a-count"),
    ],
)
def test_an_offer_mentions_the_remainder_only_when_there_is_one(
    tmp_path: Path, remaining: object, expected: tuple[str, ...]
) -> None:
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=OFFER_UNMENTIONED,
            payload={
                "kind_keys": ("alpha", "beta"),
                "message_keys": ("component.alpha", "component.beta"),
                "remaining": remaining,
            },
            locale=LOCALE,
        )
    )

    assert rendered.heading == "shall I plan the first thing, the second thing?"
    assert rendered.lines == expected


def test_a_summary_names_each_selections_own_component(tmp_path: Path) -> None:
    """``{component}`` is rebound per line: the summary names no single Kind, so the Act's own
    ``kind_key`` would make every row read as the same one."""
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=DELIVER_SUMMARY,
            payload={
                "selected": ("alpha", "beta"),
                "declined": (),
                "open": (),
                "open_mentioned": (),
                "is_closeable": True,
                "selections": (
                    {"kind_key": "alpha", "option_id": "a-1", "round_index": 0},
                    {"kind_key": "beta", "option_id": "b-2", "round_index": 1},
                ),
            },
            locale=LOCALE,
        )
    )

    assert rendered.lines == ("the first thing: a-1", "the second thing: b-2")


def test_a_summary_phrases_its_selections_but_lists_the_rest_by_key(tmp_path: Path) -> None:
    """What ``declined`` and ``open`` render as today: the raw ``kind_key``s, not phrased names.

    ``{noted}`` and ``{kinds}`` are *derived* placeholders and come out as Component Kind
    names; ``{declined}`` and ``{open}`` are plain payload tuples and come out as keys. The
    asymmetry is what the contract says, and this pins it so that a change to it is deliberate
    — but a traveller reading the shipped catalogue sees an untranslated key in that sentence,
    which is F10's to settle.
    """
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=DELIVER_SUMMARY,
            payload={
                "selected": (),
                "declined": ("alpha",),
                "open": ("beta",),
                "open_mentioned": ("beta",),
                "is_closeable": False,
                "selections": (),
            },
            locale=LOCALE,
        )
    )

    assert rendered.lines == ("turned down: alpha.", "still open: beta.")


def test_a_summary_with_nothing_settled_says_so(tmp_path: Path) -> None:
    """The one line that must exist: a summary is never a heading with an empty body."""
    renderer = renderer_for(tmp_path, BODY_MESSAGES)

    rendered = renderer.render(
        AssistantAct(
            act=DELIVER_SUMMARY,
            payload={
                "selected": (),
                "declined": (),
                "open": (),
                "open_mentioned": (),
                "is_closeable": False,
                "selections": (),
            },
            locale=LOCALE,
        )
    )

    assert rendered.lines == ("nothing settled yet.",)


# -- reading the two files ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("- one\n- two\n", "must be a mapping", id="a-list-rather-than-a-mapping"),
        pytest.param("", "must be a mapping", id="an-empty-file"),
        pytest.param(
            'locale: xx\nmessages:\n  greet: "hi"\nextra: 1\n',
            "unknown top-level key",
            id="a-key-nobody-reads",
        ),
        pytest.param(
            'locale: en\nmessages:\n  greet: "hi"\n',
            "declares locale",
            id="a-locale-that-disagrees-with-the-file-name",
        ),
        pytest.param(
            "locale: xx\ndirection: ltr\n",
            "`messages` must be a mapping",
            id="no-messages-block",
        ),
        pytest.param(
            "locale: xx\nmessages: nope\n",
            "`messages` must be a mapping",
            id="a-messages-block-that-is-not-a-mapping",
        ),
        pytest.param(
            "locale: xx\nmessages:\n  greet: 3\n",
            "must be text",
            id="a-phrasing-that-is-not-text",
        ),
        pytest.param(
            'locale: xx\ndirection: sideways\nmessages:\n  greet: "hi"\n',
            "`direction` must be one of",
            id="a-direction-nobody-writes-in",
        ),
    ],
)
def test_a_malformed_message_catalogue_is_refused_by_name(
    tmp_path: Path, text: str, reason: str
) -> None:
    """Every refusal names the file, because the reader's next move is to open it."""
    directory = write_files(tmp_path / "messages", messages=text)

    with pytest.raises(ConfigurationError) as raised:
        load_message_catalogue(directory, LOCALE)

    assert f"{LOCALE}.yaml" in str(raised.value)
    assert reason in str(raised.value)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        pytest.param("- one\n", "must be a mapping", id="a-list-rather-than-a-mapping"),
        pytest.param("", "must be a mapping", id="an-empty-file"),
        pytest.param("locale: xx\nextra: 1\n", "unknown top-level key", id="a-key-nobody-reads"),
        pytest.param(
            "locale: en\n", "declares locale", id="a-locale-that-disagrees-with-the-file-name"
        ),
        pytest.param("locale: xx\nmoney: 2\n", "`money` must be a mapping", id="money-is-a-number"),
        pytest.param(
            "locale: xx\nmoney: {minor_digits: 2, major: 1}\n",
            "unknown top-level key",
            id="a-money-key-nobody-reads",
        ),
        pytest.param(
            "locale: xx\nmoney: {minor_digits: two}\n",
            "whole number of digits",
            id="minor-digits-is-not-a-number",
        ),
        pytest.param(
            "locale: xx\nmoney: {minor_digits: -1}\n",
            "whole number of digits",
            id="minor-digits-is-negative",
        ),
        pytest.param(
            "locale: xx\nmoney: {minor_digits: true}\n",
            "whole number of digits",
            id="minor-digits-is-a-boolean",
        ),
        pytest.param(
            "locale: xx\ndefault: nope\n",
            "must be a mapping with `columns`",
            id="a-profile-that-is-not-a-mapping",
        ),
        pytest.param(
            "locale: xx\ndefault:\n  columns: []\n  extra: 1\n",
            "unknown top-level key",
            id="a-profile-key-nobody-reads",
        ),
        pytest.param(
            "locale: xx\ndefault:\n  show_price: maybe\n",
            "not a yes or a no",
            id="show-price-is-not-a-boolean",
        ),
        pytest.param(
            "locale: xx\ndefault:\n  columns: nope\n",
            "which is not a list",
            id="columns-is-not-a-list",
        ),
        pytest.param(
            "locale: xx\ndefault:\n  columns:\n    - nope\n",
            "is not a mapping",
            id="a-column-that-is-not-a-mapping",
        ),
        pytest.param(
            "locale: xx\ndefault:\n  columns:\n    - {fact: a, width: 3}\n",
            "unknown top-level key",
            id="a-column-key-nobody-reads",
        ),
        pytest.param(
            'locale: xx\ndefault:\n  columns:\n    - {unit: "/10"}\n',
            "must name a `fact`",
            id="a-column-that-names-no-fact",
        ),
        pytest.param(
            'locale: xx\ndefault:\n  columns:\n    - {fact: "  "}\n',
            "must name a `fact`",
            id="a-column-whose-fact-is-blank",
        ),
        pytest.param(
            "locale: xx\ndefault:\n  columns:\n    - {fact: a, unit: 3}\n",
            "which is not text",
            id="a-unit-that-is-not-text",
        ),
        pytest.param(
            "locale: xx\nkinds: nope\n",
            "`kinds` must be a mapping",
            id="a-kinds-block-that-is-not-a-mapping",
        ),
        pytest.param(
            "locale: xx\nkinds:\n  alpha: nope\n",
            "profile 'alpha' must be a mapping",
            id="one-kinds-profile-that-is-not-a-mapping",
        ),
    ],
)
def test_a_malformed_display_profile_file_is_refused_by_name(
    tmp_path: Path, text: str, reason: str
) -> None:
    """A Display Profile file that exists has to be readable; only a *missing* one is fine."""
    directory = write_files(tmp_path / "messages", display=text)

    with pytest.raises(ConfigurationError) as raised:
        load_display_profiles(directory, LOCALE)

    assert f"display.{LOCALE}.yaml" in str(raised.value)
    assert reason in str(raised.value)


def test_a_locale_with_no_display_profiles_is_not_an_error(tmp_path: Path) -> None:
    """A locale with no Display Profile file means "no Kind has asked for a nicer table yet",
    which is the same state an unconfigured fourth Component Kind is in."""
    renderer = renderer_for(tmp_path)

    profiles = renderer.profiles(LOCALE)
    rendered = renderer.render(slate(option_payload("a-1", price=PRICE, facts={"first": 1})))

    assert profiles.for_kind("alpha").columns == ()
    assert rendered.option_rows[0].cells == (("first", "1"),)
    assert rendered.option_rows[0].price == "740.00 EUR"


def test_a_missing_message_catalogue_is_an_error_naming_the_file(tmp_path: Path) -> None:
    """A supported locale with no catalogue is a broken install, not a translation in progress."""
    with pytest.raises(ConfigurationError) as raised:
        load_message_catalogue(tmp_path / "never-written", LOCALE)

    assert f"{LOCALE}.yaml" in str(raised.value)


# -- the renderer's own lifecycle -----------------------------------------------------------------


def test_the_renderer_reads_nothing_until_it_renders(tmp_path: Path) -> None:
    """Constructing must not read a file: a broken catalogue is a failing ``doctor`` check, not
    an exception thrown while the Composition Root is wiring the application."""
    renderer = ActRenderer(tmp_path / "never-written", catalog())

    assert renderer.message_dir == tmp_path / "never-written"
    with pytest.raises(ConfigurationError):
        renderer.render(AssistantAct(act=GREET, payload={}, locale=LOCALE))


def test_each_locale_is_read_once_and_cached(tmp_path: Path) -> None:
    """A conversation must not see its wording change underneath it mid-session."""
    directory = write_files(
        tmp_path / "messages", messages=message_file(BASE_MESSAGES), display=PROFILED_DISPLAY
    )
    renderer = ActRenderer(directory, catalog())
    catalogue = renderer.catalogue(LOCALE)
    profiles = renderer.profiles(LOCALE)

    (directory / f"{LOCALE}.yaml").unlink()
    (directory / f"display.{LOCALE}.yaml").unlink()

    assert renderer.catalogue(LOCALE) is catalogue
    assert renderer.profiles(LOCALE) is profiles
    assert renderer.render(AssistantAct(act=GREET, payload={}, locale=LOCALE)).heading == "hello"


def test_an_unsupported_locale_renders_in_the_default_one(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An over-eager Language Detector degrades to the default locale, mid-conversation."""
    directory = tmp_path / "messages"
    directory.mkdir(parents=True)
    (directory / "en.yaml").write_text(message_file(BASE_MESSAGES, locale="en"), encoding="utf-8")
    renderer = ActRenderer(directory, catalog(), supported_locales=("en",), default_locale="en")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        rendered = renderer.render(AssistantAct(act=GREET, payload={}, locale="fr"))

    assert renderer.resolve_locale("fr") == "en"
    assert rendered.heading == "hello"
    assert "fr" in caplog.text


def test_an_installation_that_names_no_locales_trusts_what_it_is_handed(tmp_path: Path) -> None:
    """The test fixture's world: no supported list means "whatever tag you ask for"."""
    renderer = renderer_for(tmp_path)

    assert renderer.resolve_locale(LOCALE) == LOCALE
    assert renderer.resolve_locale("anything") == "anything"


def test_the_locale_argument_overrides_the_acts_own(tmp_path: Path) -> None:
    """A surface started with ``--locale`` renders in it before anything has been detected."""
    directory = write_files(tmp_path / "messages", messages=message_file(BASE_MESSAGES))
    (directory / "fr.yaml").write_text(
        message_file({**BASE_MESSAGES, "greet": "bonjour"}, locale="fr", direction="rtl"),
        encoding="utf-8",
    )
    renderer = ActRenderer(directory, catalog())

    rendered = renderer.render(AssistantAct(act=GREET, payload={}, locale=LOCALE), locale="fr")

    assert rendered.heading == "bonjour"
    assert rendered.direction == "rtl"
