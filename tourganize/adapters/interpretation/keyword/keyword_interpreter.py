"""``KeywordTurnInterpreter`` — the deterministic ``TurnInterpreter`` the dialogue starts with.

No model, no network, no ambiguity: a phrase table says what an utterance means, a regex says
what a date and a place look like, and the same input always produces the same
``TurnInterpretation``. That is the whole point of it. It exists so that F05's state machine can
be built, driven and pinned by Golden Conversations (F11) before F08 exists, and it exists
behind a port so that F08 is a config change rather than a rewrite.

It is **explicitly a stand-in**, and the two things it deliberately does not do are worth naming
so nobody mistakes them for gaps to fill in here:

* **No natural-language understanding.** Intent comes from phrases; anything unphrased is
  ``UNKNOWN``, which the Director turns into ``clarify``. Widening this file is the wrong fix —
  F08's Extraction Call against a schema is the right one.
* **No relative dates.** "next month" is not resolved here, so it produces no value at all
  rather than a guessed one. Resolving it needs a ``Clock`` *and* a locale calendar, and the
  boundary that owns both is F08's interpreter.

Everything locale-specific — the phrases, the per-kind keywords, the place markers, the month
names, and the Requirement Schema field names each value shape is filed under — is in
``keywords.<locale>.yaml``. Nothing in this module names a travel topic, and nothing in it names
an English word.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Final, final

from tourganize.adapters.interpretation.keyword.phrase_tables import (
    SHAPE_DATE_RANGE,
    SHAPE_PLACE,
    PhraseTable,
    load_phrase_tables,
)
from tourganize.dialogue import (
    DEFAULT_LOCALE,
    DialogueContext,
    DialogueState,
    TurnIntent,
    TurnInterpretation,
    UserTurn,
)
from tourganize.domain.requirements import RequirementSource, RequirementUpdate

__all__ = ["HEBREW_LOCALE", "KeywordTurnInterpreter"]

#: The Locale Tag a turn written in the Hebrew block is read as. F10's Language Detector
#: replaces this one-line rule with a real one; until then, script *is* the signal.
HEBREW_LOCALE: Final = "he"

_HEBREW_BLOCK: Final = re.compile("[\u0590-\u05ff]")
_ISO_DATE: Final = r"\d{4}-\d{2}-\d{2}"
_QUOTED: Final = re.compile(r"[\"'](?P<ref>[^\"']+)[\"']")
_ORDINAL: Final = re.compile(r"(?<!\w)(?P<ordinal>\d{1,2})(?:st|nd|rd|th)?(?!\w)")
_WORD: Final = re.compile(r"[^\s,.;:!?]+")
#: The confidence a keyword match is reported with. A single number rather than a scale,
#: because a phrase table has no notion of degree — it either matched or it did not — and a
#: fabricated gradient would be read as meaning something by whatever consumes it next.
_MATCHED_CONFIDENCE: Final = 1.0


@final
class KeywordTurnInterpreter:
    """Reads turns with a phrase table. Deterministic, and replaced by F08."""

    def __init__(self, config_dir: Path, *, default_locale: str = DEFAULT_LOCALE) -> None:
        self._config_dir = config_dir
        self._default_locale = default_locale
        self._tables: Mapping[str, PhraseTable] | None = None

    @property
    def config_dir(self) -> Path:
        """Where the phrase tables are read from, for ``doctor`` and error messages."""
        return self._config_dir

    def tables(self) -> Mapping[str, PhraseTable]:
        """The phrase tables, read once and cached.

        Loaded on first use rather than in the constructor, for the reason the YAML catalog is:
        a missing configuration directory has to be a failing ``doctor`` check, not an
        exception thrown while the Container is being wired.
        """
        if self._tables is None:
            self._tables = load_phrase_tables(self._config_dir)
        return self._tables

    def interpret(self, turn: UserTurn, context: DialogueContext) -> TurnInterpretation:
        """Read one turn in the light of ``context``."""
        locale = _locale_of(turn)
        table = self._table_for(locale if locale is not None else context.locale)
        text = turn.text
        lowered = text.lower()

        mentioned = _mentioned_kinds(table, lowered, context.known_kind_keys)
        updates = _requirement_updates(table, text, context)
        matched = frozenset(intent for intent in TurnIntent if _matches(table, intent, lowered))
        intent = _intent_of(matched, context, updates=updates, mentioned=mentioned, text=text)
        return TurnInterpretation(
            intent=intent,
            mentioned_kinds=mentioned,
            requirement_updates=updates,
            chosen_option_ref=(
                _choice_candidate(text, context) if intent is TurnIntent.CHOOSE_OPTION else None
            ),
            detected_locale=locale,
            confidence=_MATCHED_CONFIDENCE if intent is not TurnIntent.UNKNOWN else 0.0,
            notes=f"keyword table {table.locale}",
        )

    def _table_for(self, locale: str) -> PhraseTable:
        """The table for ``locale``, falling back to the default one, then to any at all."""
        tables = self.tables()
        found = tables.get(locale) or tables.get(self._default_locale)
        if found is not None:
            return found
        # Deterministic rather than arbitrary: the locales are sorted, so a two-table install
        # with neither the turn's locale nor the default one behaves the same on every machine.
        return tables[sorted(tables)[0]]


def _locale_of(turn: UserTurn) -> str | None:
    """The Locale Tag this turn is in, or ``None`` when there is nothing to go on.

    Hebrew script is decisive: a turn containing it is Hebrew whatever the surface believed.
    Otherwise the surface's hint stands, and with no hint the session keeps the locale it had —
    an English-looking turn is not evidence that a Hebrew conversation has switched language.
    """
    if _HEBREW_BLOCK.search(turn.text):
        return HEBREW_LOCALE
    return turn.locale_hint


def _matches(table: PhraseTable, intent: TurnIntent, lowered: str) -> bool:
    """Whether any phrase this locale declares for ``intent`` appears in the turn."""
    return any(_contains(lowered, phrase) for phrase in table.intents.get(intent, ()))


def _contains(lowered: str, phrase: str) -> bool:
    """Whether ``phrase`` appears in ``lowered`` on word boundaries.

    ``(?<!\\w)``/``(?!\\w)`` rather than ``\\b`` so that a phrase which begins or ends with
    punctuation still matches, and so that Hebrew — whose letters are word characters — is
    bounded the same way Latin is.
    """
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", lowered) is not None


def _mentioned_kinds(table: PhraseTable, lowered: str, known: tuple[str, ...]) -> tuple[str, ...]:
    """The Component Kinds this turn raised, in the catalog's own declaration order.

    Restricted to the Kinds the catalog declares: a phrase table naming a Kind that was removed
    or disabled is a stale file, not a licence to plan something nobody declared.
    """
    return tuple(
        kind_key
        for kind_key in known
        if any(_contains(lowered, keyword) for keyword in table.kinds.get(kind_key, ()))
    )


def _intent_of(
    matched: frozenset[TurnIntent],
    context: DialogueContext,
    *,
    updates: tuple[RequirementUpdate, ...],
    mentioned: tuple[str, ...],
    text: str,
) -> TurnIntent:
    """Place the turn. Order matters, and this is the whole of the ordering.

    Leaving is honoured from anywhere, because a traveller saying goodbye means it. Then the
    state decides: while a slate is on the table a turn is a refinement if it carries new
    values and a choice if it carries a reference; while an offer is on the table it is a yes or
    a no. Only then do the unprompted readings apply.
    """
    if TurnIntent.END_SESSION in matched:
        return TurnIntent.END_SESSION
    if TurnIntent.STATE_REQUEST in matched:
        return TurnIntent.STATE_REQUEST
    if context.state is DialogueState.AWAITING_CHOICE:
        if updates or TurnIntent.REFINE in matched:
            return TurnIntent.REFINE
        if _choice_candidate(text, context) is not None:
            return TurnIntent.CHOOSE_OPTION
    if context.state is DialogueState.OFFERING_UNMENTIONED:
        if TurnIntent.ACCEPT_OFFER in matched:
            return TurnIntent.ACCEPT_OFFER
        if TurnIntent.DECLINE_OFFER in matched:
            return TurnIntent.DECLINE_OFFER
    if updates or mentioned:
        return TurnIntent.ANSWER_QUESTION
    if TurnIntent.SMALL_TALK in matched:
        return TurnIntent.SMALL_TALK
    return TurnIntent.UNKNOWN


def _choice_candidate(text: str, context: DialogueContext) -> str | None:
    """Read an *attempt* at naming one Plan Option: a quoted id, a known id, or an ordinal.

    Deliberately not resolved here. A turn that names ``9`` when three options are on the table
    is a traveller trying to choose, not a turn nobody could place, and the difference matters:
    the Director answers the first with ``clarify(unresolved_choice)``, which says "not one of
    these", and the second with ``clarify(not_understood)``, which says "say that again".
    Resolving a reference against a slate is the Director's job — it holds the slate.
    """
    quoted = _QUOTED.search(text)
    if quoted is not None:
        return quoted.group("ref")
    lowered = text.lower()
    for option_id in context.slate_option_refs:
        if _contains(lowered, option_id.lower()):
            return option_id
    ordinal = _ORDINAL.search(text)
    return None if ordinal is None else ordinal.group("ordinal")


def _requirement_updates(
    table: PhraseTable, text: str, context: DialogueContext
) -> tuple[RequirementUpdate, ...]:
    """Offer a value for every shape this turn spells out and this locale files somewhere.

    A shape is offered only when the table maps it to a field name *and* — once a component is
    in focus — that component's Requirement Schema declares that field. Before anything is in
    focus there is no schema to check against, so the table's own name is offered and the
    Director reports a name the schema turns out not to declare rather than merging it.
    """
    found: list[RequirementUpdate] = []
    for shape, reading in (
        (SHAPE_DATE_RANGE, _date_range(table, text)),
        (SHAPE_PLACE, _place(table, text)),
    ):
        if reading is None:
            continue
        field_name = table.field_for(shape)
        if field_name is None or not _declared(field_name, context):
            continue
        value, raw = reading
        found.append(
            RequirementUpdate(
                field_name=field_name,
                value=value,
                source=RequirementSource.USER,
                confidence=_MATCHED_CONFIDENCE,
                raw_text=raw,
            )
        )
    return tuple(found)


def _declared(field_name: str, context: DialogueContext) -> bool:
    return not context.focus_field_names or field_name in context.focus_field_names


def _date_range(table: PhraseTable, text: str) -> tuple[str, str] | None:
    """Read a resolved, ordered date range, in the canonical ``start/end`` spelling.

    Two spellings, both explicit about the year: two ISO-8601 dates with a separator between
    them, and two day numbers sharing one month name — which is how a traveller writes it
    ("23-28 October 2026") and the reason the month names are locale configuration.
    """
    separator = _separator_pattern(table)
    both_iso = re.search(rf"(?P<start>{_ISO_DATE})\s*{separator}\s*(?P<end>{_ISO_DATE})", text)
    if both_iso is not None:
        return f"{both_iso.group('start')}/{both_iso.group('end')}", both_iso.group(0)
    if not table.months:
        return None
    months = "|".join(re.escape(name) for name in sorted(table.months, key=len, reverse=True))
    spelled = re.search(
        rf"(?<!\w)(?P<first>\d{{1,2}})\s*{separator}\s*(?P<second>\d{{1,2}})\s+"
        rf"(?P<month>{months})\s+(?P<year>\d{{4}})(?!\d)",
        text,
        re.IGNORECASE,
    )
    if spelled is None:
        return None
    month = table.months[spelled.group("month").lower()]
    year = int(spelled.group("year"))
    try:
        start = date(year, month, int(spelled.group("first")))
        end = date(year, month, int(spelled.group("second")))
    except ValueError:
        # A day number the month does not have. Offering nothing is right: the Director then
        # asks again, which is a better answer than a date nobody meant.
        return None
    return f"{start.isoformat()}/{end.isoformat()}", spelled.group(0)


def _separator_pattern(table: PhraseTable) -> str:
    """The alternation of this locale's range separators, longest first so ``--`` beats ``-``."""
    declared = table.range_separators or ("/",)
    alternation = "|".join(re.escape(item) for item in sorted(declared, key=len, reverse=True))
    return f"(?:{alternation})"


def _place(table: PhraseTable, text: str) -> tuple[str, str] | None:
    """Read a place name: the name-shaped words following one of this locale's place markers.

    Case is preserved and nothing is resolved — turning a name into an airport code or a
    coordinate is F16/F17's work behind a port. At most two words, because "in Tel Aviv" is a
    place and "in Paris between the 23rd" is a place followed by a sentence.
    """
    if not table.place_markers:
        return None
    markers = "|".join(re.escape(marker) for marker in table.place_markers)
    marked = re.search(rf"(?<!\w)(?:{markers})(?=\s)\s+(?P<tail>.+)", text, re.IGNORECASE)
    if marked is None:
        return None
    words: list[str] = []
    for word in _WORD.findall(marked.group("tail")):
        if not _is_name_word(word, table):
            break
        words.append(word)
        if len(words) == 2:
            break
    if not words:
        return None
    value = " ".join(words)
    return value, value


def _is_name_word(word: str, table: PhraseTable) -> bool:
    """Whether ``word`` could be part of a place name in this locale.

    A capitalised Latin word or a Hebrew-script one, and never a month name: "in October" names
    a time, not a place, and a month slipping through here would be filed as somewhere to stay.
    """
    if word.lower() in table.months:
        return False
    if _HEBREW_BLOCK.search(word):
        return True
    return word[:1].isupper() and word[:1].isalpha()
