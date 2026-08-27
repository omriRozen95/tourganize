"""The Act Renderer: the one place an Assistant Act becomes text in Phase 1.

An Assistant Act carries message keys, field names, ``kind_key``s, opaque codes and
structured Plan Option data, and never a sentence — that is what lets one Director serve a
traveller reading English and a traveller reading Hebrew. This module is the other half of
that bargain: it takes an Act and a Locale Tag and produces a :class:`RenderedAct`, drawing
every word from the **Message Catalogue** (``config/messages/<locale>.yaml``) and every
option column from a **Display Profile** (``config/messages/display.<locale>.yaml``).

Two rules keep it honest.

**No conditional logic per Component Kind.** Which facts an option shows, in what order and
with what unit, is read from the Display Profile; the fallback when a Kind has no profile is
"every fact the source declared, in declaration order". A fourth Component Kind is therefore
still configuration, exactly as F02 and F06 promise, and this module never learns a topic's
name. Message keys follow the same rule: a key is looked up most-specific-first —
``<act>.<reason_code>``, then ``<act>.<kind_key>``, then ``<act>`` — so a Kind that wants its
own phrasing writes one line of YAML and a Kind that does not writes nothing.

**A missing key is visible, never fatal.** An unknown message key renders
``⟪missing:the.key⟫`` and logs at WARNING; a placeholder the payload cannot fill renders
``⟪missing:name⟫`` the same way. A demonstration that loses a sentence must still finish the
conversation, and a blank line where a question should be is the one failure mode that would
not be noticed.

``direction`` is plumbed from the catalogue's declared ``direction`` (``rtl`` for Hebrew) so
that a surface can do the right thing from day one. Making it *look* right — bidi shaping,
mixed-script runs, locale-aware dates and numbers — is F10's, and everything here is
provisional by design.

Three consequences of those rules are worth stating outright, because each is a place where
"be forgiving" and "be honest" pull in opposite directions.

*A missing message **file** is not a missing message **key**.* A key nobody wrote is one
sentence a translator has not reached yet, and the marker is how it gets noticed. A locale
whose catalogue does not exist is a broken installation — every sentence would be a marker —
so :func:`load_message_catalogue` raises ``ConfigurationError`` instead of rendering a
screenful of them. What protects a *running* conversation from that is locale resolution:
:class:`ActRenderer` is told which Locale Tags this installation supports, and a tag outside
that list (an over-eager Language Detector, a ``--locale`` typo) renders in the default one.
A missing *Display Profile* file, by contrast, really is nothing: it means "no Kind has asked
for a nicer table yet", which is the same state an unconfigured fourth Kind is in.

*Labels fall back to their own names, messages do not.* A column heading with no
``fact.<name>`` message renders as the raw fact name and logs at DEBUG, because ``nights`` is
a perfectly legible heading and demanding a message for one would mean a fourth Component
Kind needs a config edit to render at all — which is precisely the property F02 and F06 buy.
A *sentence* has no such fallback: there is nothing legible to fall back to, so it gets the
marker and a WARNING.

*Substitution is written out here rather than handed to* ``str.format``. Message files are
edited by hand, and ``format`` answers a stray ``{`` with a ``ValueError`` and an unknown
placeholder with a ``KeyError`` — two ways for a typo in a translation to end a session. The
walk in :func:`_substitute` answers both with a marker and carries on.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from tourganize.dialogue import (
    ASK_BLOCKING,
    ASK_OPTIONAL,
    CLARIFY,
    CONFIRM_SELECTION,
    DELIVER_SUMMARY,
    OFFER_UNMENTIONED,
    PRESENT_SLATE,
    REPORT_INVALID_VALUE,
    AssistantAct,
)
from tourganize.domain.errors import UnknownComponentKindError
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.yaml_subset import read_config_file
from tourganize.ports.catalog import ComponentCatalog

__all__ = [
    "DEFAULT_MINOR_DIGITS",
    "MISSING_MARKER_CLOSE",
    "MISSING_MARKER_OPEN",
    "ActRenderer",
    "Direction",
    "DisplayColumn",
    "DisplayProfile",
    "DisplayProfiles",
    "MessageCatalogue",
    "OptionRow",
    "RenderedAct",
    "load_display_profiles",
    "load_message_catalogue",
    "missing_marker",
]

LOGGER: Final = logging.getLogger("tourganize.language.act_renderer")

Direction = Literal["ltr", "rtl"]

#: What a missing message key or unfillable placeholder renders as. Deliberately loud and
#: deliberately not ASCII: it cannot be mistaken for something a traveller was meant to read.
MISSING_MARKER_OPEN: Final = "⟪"
MISSING_MARKER_CLOSE: Final = "⟫"

#: Minor units per major unit, when a Display Profile does not say. Two is right for the
#: currencies this release's fixtures use; a currency with a different exponent is F10's
#: problem, and the Display Profile is where the answer will go.
DEFAULT_MINOR_DIGITS: Final = 2

#: The message keys the renderer itself needs. Every one of them is *punctuation* rather than
#: prose — a separator, a money layout, the two words a boolean fact reads as — which is why
#: the renderer may name them and may name nothing else.
_LIST_SEPARATOR: Final = "list.separator"
_MONEY_FORMAT: Final = "money.format"
_VALUE_TRUE: Final = "value.true"
_VALUE_FALSE: Final = "value.false"
_VALUE_NONE: Final = "value.none"

#: The ``deliver_summary`` payload fields that hold ``kind_key`` tuples. Each gets a derived
#: ``<name>_kinds`` placeholder holding the phrased Component Kind names, because these are
#: the lists that end up inside a sentence.
_SUMMARY_KIND_LISTS: Final = ("selected", "declined", "open", "open_mentioned")

#: Message key prefixes for the two kinds of *label* a table needs: the name of a Requirement
#: Schema field (a Filter Note, an optional-field list) and the name of a Plan Option fact.
_FIELD_PREFIX: Final = "field"
_FACT_PREFIX: Final = "fact"

_DIRECTIONS: Final = ("ltr", "rtl")
_MESSAGE_FILE_KEYS: Final = frozenset({"direction", "locale", "messages"})
_DISPLAY_FILE_KEYS: Final = frozenset({"default", "kinds", "locale", "money"})
_PROFILE_KEYS: Final = frozenset({"columns", "show_price"})
_COLUMN_KEYS: Final = frozenset({"fact", "unit"})
_MONEY_KEYS: Final = frozenset({"minor_digits"})

#: Which payload entry ``{count}`` counts, most specific first. One list rather than a branch
#: per Act: "how many things is this Act about" has the same answer everywhere — the options
#: on a slate, the Kinds in an offer, the questions in an elicitation.
_COUNTABLE: Final = ("options", "kind_keys", "prompt_message_keys")


def missing_marker(key: str) -> str:
    """The visible marker a missing key or placeholder renders as."""
    return f"{MISSING_MARKER_OPEN}missing:{key}{MISSING_MARKER_CLOSE}"


@dataclass(frozen=True, slots=True)
class OptionRow:
    """One numbered row of an Option Slate, ready to be drawn.

    ``cells`` is ``(label, value)`` in Display Profile order, already phrased and united.
    ``filter_notes`` are the phrased names of the optional filters this option fails — the
    visible half of soft filtering (F06), which is invisible if a surface drops it.
    """

    number: int
    option_id: str
    price: str | None
    cells: tuple[tuple[str, str], ...] = ()
    filter_notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedAct:
    """One Assistant Act as a heading, body lines and an optional numbered option table."""

    act: str
    heading: str | None
    lines: tuple[str, ...] = ()
    option_rows: tuple[OptionRow, ...] = ()
    direction: Direction = "ltr"
    kind_key: str | None = None


@dataclass(frozen=True, slots=True)
class DisplayColumn:
    """One column of an option table: which fact to read, and what to put after it."""

    fact: str
    unit: str = ""


@dataclass(frozen=True, slots=True)
class DisplayProfile:
    """How one Component Kind's options are laid out. Structure only — never wording."""

    columns: tuple[DisplayColumn, ...] = ()
    show_price: bool = True
    minor_digits: int = DEFAULT_MINOR_DIGITS


@dataclass(frozen=True, slots=True)
class DisplayProfiles:
    """The Display Profiles of one locale, with the fallback for a Kind that declares none."""

    locale: str
    kinds: Mapping[str, DisplayProfile] = field(default_factory=dict)
    fallback: DisplayProfile = DisplayProfile()

    def for_kind(self, kind_key: str | None) -> DisplayProfile:
        """The profile in force for ``kind_key``, or the fallback."""
        if kind_key is None:
            return self.fallback
        return self.kinds.get(kind_key, self.fallback)


@dataclass(frozen=True, slots=True)
class MessageCatalogue:
    """One locale's phrasings, keyed by message key.

    ``direction`` is declared in the file rather than inferred from the tag: a Locale Tag is
    an identifier, and guessing writing direction from one is exactly the kind of hardcoded
    table that goes wrong on the first locale nobody thought of.
    """

    locale: str
    direction: Direction = "ltr"
    messages: Mapping[str, str] = field(default_factory=dict)

    def get(self, *candidates: str) -> str:
        """The first candidate key this catalogue declares, or a missing marker for the last.

        Most-specific-first lookup lives here so that every caller resolves keys the same
        way, and so that "this Kind wants its own wording" costs one line of YAML.
        """
        for key in candidates:
            declared = self.messages.get(key)
            if declared is not None:
                return declared
        wanted = candidates[-1] if candidates else ""
        LOGGER.warning(
            "no message for %s in the %r catalogue; rendering a marker",
            " -> ".join(candidates) or "an unnamed key",
            self.locale,
            extra={"kind": "language"},
        )
        return missing_marker(wanted)

    def has(self, key: str) -> bool:
        """Whether ``key`` is declared. For ``doctor`` and for tests, never for control flow."""
        return key in self.messages


def load_message_catalogue(message_dir: Path, locale: str) -> MessageCatalogue:
    """Read ``<message_dir>/<locale>.yaml``, or raise ``ConfigurationError`` naming the file.

    A *missing* file is an error, unlike a missing Display Profile file: a supported locale
    with no catalogue would render every sentence as a marker, which is a broken installation
    rather than a translation still in progress.
    """
    path = message_dir / f"{locale}.yaml"
    document = read_config_file(path)
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            f"invalid Message Catalogue {path}: the file must be a mapping with `locale`, "
            f"`direction` and `messages` keys"
        )
    _refuse_unknown(document, _MESSAGE_FILE_KEYS, "Message Catalogue", path)
    _require_declared_locale(document, locale, "Message Catalogue", path)
    declared = document.get("messages")
    if not isinstance(declared, Mapping):
        raise ConfigurationError(
            f"invalid Message Catalogue {path}: `messages` must be a mapping of message key "
            f"to phrasing, got {declared!r}"
        )
    messages: dict[str, str] = {}
    for key, phrasing in declared.items():
        if not isinstance(phrasing, str):
            raise ConfigurationError(
                f"invalid Message Catalogue {path}: message {str(key)!r} must be text, "
                f"got {phrasing!r}"
            )
        messages[str(key)] = phrasing
    return MessageCatalogue(
        locale=locale, direction=_direction_of(document, path), messages=messages
    )


def load_display_profiles(message_dir: Path, locale: str) -> DisplayProfiles:
    """Read ``<message_dir>/display.<locale>.yaml``.

    A locale with no Display Profile file is not an error: it falls back to "every declared
    fact, in declaration order", which is what an unconfigured fourth Component Kind gets too.
    """
    path = message_dir / f"display.{locale}.yaml"
    if not path.exists():
        LOGGER.debug(
            "no Display Profiles at %s; every option table falls back to its declared facts",
            path,
            extra={"kind": "language"},
        )
        return DisplayProfiles(locale=locale)
    document = read_config_file(path)
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: the file must be a mapping with `locale`, "
            f"`money`, `default` and `kinds` keys"
        )
    _refuse_unknown(document, _DISPLAY_FILE_KEYS, "Display Profiles", path)
    _require_declared_locale(document, locale, "Display Profiles", path)
    minor_digits = _minor_digits_of(document, path)
    fallback = _profile_of(document.get("default"), minor_digits, "default", path)
    declared = document.get("kinds")
    if declared is None:
        declared = {}
    if not isinstance(declared, Mapping):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: `kinds` must be a mapping of kind_key to "
            f"profile, got {declared!r}"
        )
    kinds = {
        str(kind_key): _profile_of(entry, minor_digits, str(kind_key), path)
        for kind_key, entry in declared.items()
    }
    return DisplayProfiles(locale=locale, kinds=kinds, fallback=fallback)


class ActRenderer:
    """Turns Assistant Acts into :class:`RenderedAct`s, one locale at a time.

    Catalogues and profiles are read lazily and cached per locale, for the same reason the
    Component Catalog is: constructing the renderer must not read a file, so a broken message
    file is a failing ``doctor`` check and a visible marker, not an exception thrown while the
    Composition Root is wiring the application.
    """

    def __init__(
        self,
        message_dir: Path,
        catalog: ComponentCatalog,
        *,
        supported_locales: Sequence[str] = (),
        default_locale: str = "en",
    ) -> None:
        self._message_dir = message_dir
        self._catalog = catalog
        self._supported = tuple(supported_locales)
        self._default_locale = default_locale
        self._catalogues: dict[str, MessageCatalogue] = {}
        self._profiles: dict[str, DisplayProfiles] = {}

    @property
    def message_dir(self) -> Path:
        """Where this renderer reads its Message Catalogue and Display Profiles."""
        return self._message_dir

    def catalogue(self, locale: str) -> MessageCatalogue:
        """The Message Catalogue of ``locale``, loaded once and cached."""
        tag = self.resolve_locale(locale)
        cached = self._catalogues.get(tag)
        if cached is None:
            cached = load_message_catalogue(self._message_dir, tag)
            self._catalogues[tag] = cached
        return cached

    def profiles(self, locale: str) -> DisplayProfiles:
        """The Display Profiles of ``locale``, loaded once and cached."""
        tag = self.resolve_locale(locale)
        cached = self._profiles.get(tag)
        if cached is None:
            cached = load_display_profiles(self._message_dir, tag)
            self._profiles[tag] = cached
        return cached

    def direction(self, locale: str) -> Direction:
        """The writing direction ``locale``'s catalogue declares."""
        return self.catalogue(locale).direction

    def resolve_locale(self, locale: str) -> str:
        """Which Locale Tag will actually be rendered in, given what this install supports.

        An installation that names no supported locales trusts whatever it is handed — that
        is the test fixture's world. One that does name them refuses to look for a file it
        knows is not there: a Language Detector guessing ``fr`` must degrade to the default
        locale, not raise mid-conversation.
        """
        if not self._supported or locale in self._supported:
            return locale
        LOGGER.warning(
            "locale %r is not one of %s; rendering in %r instead",
            locale,
            ", ".join(self._supported),
            self._default_locale,
            extra={"kind": "language"},
        )
        return self._default_locale

    def render(self, act: AssistantAct, locale: str | None = None) -> RenderedAct:
        """Render one Assistant Act. Never raises for an Act in the closed vocabulary.

        ``locale`` overrides the Act's own — a surface started with ``--locale he`` renders in
        Hebrew even before the interpreter has detected anything.
        """
        tag = self.resolve_locale(act.locale if locale is None else locale)
        catalogue = self.catalogue(tag)
        values = self._values(act, catalogue)
        return RenderedAct(
            act=act.act,
            heading=self._line(catalogue, _heading_keys(act), values),
            lines=self._body_lines(act, catalogue, values),
            option_rows=self._option_rows(act, catalogue, self.profiles(tag)),
            direction=catalogue.direction,
            kind_key=act.kind_key,
        )

    # -- the body of an Act ------------------------------------------------------------------

    def _body_lines(
        self, act: AssistantAct, catalogue: MessageCatalogue, values: Mapping[str, str]
    ) -> tuple[str, ...]:
        """The Act's body lines: one branch per **Act**, and never one per Component Kind.

        The Act vocabulary is closed (F05), so branching on it is exhaustive and stays that
        way; branching on a ``kind_key`` would be the thing that quietly ends "a fourth Kind
        is configuration". An Act with nothing to say below its heading answers with no lines
        rather than with a blank one.
        """
        payload = act.payload
        if act.act in (ASK_BLOCKING, ASK_OPTIONAL):
            return self._lines_for(catalogue, payload.get("prompt_message_keys"), values)
        if act.act == REPORT_INVALID_VALUE:
            return self._lines_for(catalogue, payload.get("reason_message_key"), values)
        if act.act == CONFIRM_SELECTION:
            if _texts(payload.get("noted_kinds")):
                return (self._line(catalogue, (f"{CONFIRM_SELECTION}.noted",), values),)
            return ()
        if act.act == OFFER_UNMENTIONED:
            remaining = payload.get("remaining")
            if isinstance(remaining, int) and not isinstance(remaining, bool) and remaining > 0:
                return (self._line(catalogue, (f"{OFFER_UNMENTIONED}.remaining",), values),)
            return ()
        if act.act == DELIVER_SUMMARY:
            return self._summary_lines(act, catalogue, values)
        if act.act == CLARIFY:
            # The examples are the escalation the Director offers once a Blocking Rule has
            # been asked about too many times; a field that declares none falls back to being
            # asked again, which is better than an escalation with nothing in it.
            examples = payload.get("example_message_keys")
            keys = examples if _texts(examples) else payload.get("prompt_message_keys")
            return self._lines_for(catalogue, keys, values)
        return ()

    def _summary_lines(
        self, act: AssistantAct, catalogue: MessageCatalogue, values: Mapping[str, str]
    ) -> tuple[str, ...]:
        """One line per Selection, then what was turned down and what is still open.

        Each Selection line is rendered against its *own* Component Kind, not the Act's: the
        summary names no single Kind, so ``{component}`` has to be rebound per line or every
        row would read as the same one.
        """
        payload = act.payload
        lines: list[str] = []
        for entry in _mappings(payload.get("selections")):
            local = dict(values)
            local.update(self._payload_values(catalogue, entry))
            local["component"] = self._component_label(catalogue, _text(entry.get("kind_key")))
            lines.append(self._line(catalogue, (f"{DELIVER_SUMMARY}.selection",), local))
        if _texts(payload.get("declined")):
            lines.append(self._line(catalogue, (f"{DELIVER_SUMMARY}.declined",), values))
        if _texts(payload.get("open")):
            lines.append(self._line(catalogue, (f"{DELIVER_SUMMARY}.open",), values))
        if not lines:
            lines.append(self._line(catalogue, (f"{DELIVER_SUMMARY}.empty",), values))
        return tuple(lines)

    def _lines_for(
        self, catalogue: MessageCatalogue, keys: object, values: Mapping[str, str]
    ) -> tuple[str, ...]:
        """Resolve a payload entry that names message keys — one key or a tuple of them."""
        return tuple(self._line(catalogue, (key,), values) for key in _texts(keys))

    def _line(
        self, catalogue: MessageCatalogue, candidates: Sequence[str], values: Mapping[str, str]
    ) -> str:
        """One resolved, substituted line: the whole of "a message key becomes a sentence"."""
        keys = tuple(candidates)
        return _substitute(catalogue.get(*keys), values, origin=keys[-1] if keys else "")

    # -- the placeholders a template may fill --------------------------------------------------

    def _values(self, act: AssistantAct, catalogue: MessageCatalogue) -> dict[str, str]:
        """Every ``{placeholder}`` an Act's templates can fill, phrased for this locale.

        Two layers. Everything the payload carries by its own name, so a message may say
        ``{round_index}`` without anyone here having heard of rounds; then the derived names —
        ``component``, ``kinds``, ``fields``, ``noted``, ``choice``, ``count`` and the
        ``<list>_kinds`` phrasings of the summary's own lists — which are the
        payload's opaque keys turned into words, and the reason a message file never has to
        contain a ``kind_key``. A derived name is defined only when the payload carries what it
        is derived from, so a template that asks for one in the wrong Act gets a marker rather
        than a plausible-looking blank.
        """
        payload = act.payload
        values = self._payload_values(catalogue, payload)
        values["component"] = self._component_label(catalogue, act.kind_key)
        count = _count_of(payload)
        if count is not None:
            values["count"] = str(count)
        kind_keys = payload.get("kind_keys")
        if kind_keys is not None:
            values["kinds"] = self._joined_components(catalogue, _texts(kind_keys))
        noted_kinds = payload.get("noted_kinds")
        if noted_kinds is not None:
            values["noted"] = self._joined_components(catalogue, _texts(noted_kinds))
        # The closing summary's lists, phrased. The raw tuples stay available under their own
        # payload names — a template is free to ask for either — but a sentence a traveller
        # reads must not contain a `kind_key`: a lower_snake_case identifier dropped into a
        # Hebrew sentence is exactly the failure the whole message-key mechanism exists to
        # prevent, and it is invisible to anyone reviewing the English.
        for name in _SUMMARY_KIND_LISTS:
            listed = payload.get(name)
            if listed is not None:
                values[f"{name}_kinds"] = self._joined_components(catalogue, _texts(listed))
        named = payload.get("field_names")
        if named is None:
            named = payload.get("preferred_fields")
        if named is not None:
            values["fields"] = _join(
                catalogue,
                tuple(
                    self._label(catalogue, _FIELD_PREFIX, act.kind_key, name)
                    for name in _texts(named)
                ),
            )
        chosen = payload.get("option_id")
        if isinstance(chosen, str):
            values["choice"] = chosen
        return values

    def _payload_values(
        self, catalogue: MessageCatalogue, payload: Mapping[str, object]
    ) -> dict[str, str]:
        """Every scalar and every tuple-of-scalars in a payload, by its own name."""
        values: dict[str, str] = {}
        for name, value in payload.items():
            phrase = _phrase_of(catalogue, value)
            if phrase is not None:
                values[str(name)] = phrase
        return values

    def _component_label(self, catalogue: MessageCatalogue, kind_key: str | None) -> str:
        """The phrased name of a Component Kind, or "" when there is no Kind to name.

        The Component Catalog owns the ``message_key``; this file owns the words. A Kind the
        catalog does not declare is not a marker: an Act about nothing in particular is
        ordinary (``greet`` is one), and a summary of a resumed plan may name a Kind a since-
        edited catalog has dropped. Neither is worth shouting a sentence's worth of noise for.
        """
        if not kind_key:
            return ""
        try:
            kind = self._catalog.kind(kind_key)
        except UnknownComponentKindError:
            LOGGER.debug(
                "the catalog declares no Component Kind %r; rendering it as nothing",
                kind_key,
                extra={"kind": "language"},
            )
            return ""
        return catalogue.get(kind.message_key)

    def _joined_components(self, catalogue: MessageCatalogue, kind_keys: Sequence[str]) -> str:
        """A list of Component Kinds as one phrase. A Kind nobody declares drops out."""
        labels = tuple(
            label for label in (self._component_label(catalogue, key) for key in kind_keys) if label
        )
        return _join(catalogue, labels)

    def _label(
        self, catalogue: MessageCatalogue, prefix: str, kind_key: str | None, name: str
    ) -> str:
        """A field or fact label: ``<prefix>.<kind_key>.<name>``, ``<prefix>.<name>``, or raw.

        The raw name is the fallback on purpose, and it is why a fourth Component Kind renders
        a table with no edit to any file: ``nights`` is a legible heading. It logs at DEBUG,
        not WARNING — an unphrased label is a nicety nobody has written yet, where an unphrased
        *sentence* is a hole in the conversation.
        """
        candidates = [] if kind_key is None else [f"{prefix}.{kind_key}.{name}"]
        candidates.append(f"{prefix}.{name}")
        for key in candidates:
            if catalogue.has(key):
                return catalogue.get(key)
        LOGGER.debug(
            "no %s label for %r in the %r catalogue; using the name itself",
            prefix,
            name,
            catalogue.locale,
            extra={"kind": "language"},
        )
        return name

    # -- the option table ----------------------------------------------------------------------

    def _option_rows(
        self, act: AssistantAct, catalogue: MessageCatalogue, profiles: DisplayProfiles
    ) -> tuple[OptionRow, ...]:
        """The numbered Plan Options of a slate, laid out by this Kind's Display Profile.

        Numbering is the payload's own order and counts every entry, because "2" means "the
        second thing you showed me" to a traveller and ``slate.options[1]`` to the Director,
        and those two must not be able to disagree.
        """
        if act.act != PRESENT_SLATE:
            return ()
        profile = profiles.for_kind(act.kind_key)
        rows: list[OptionRow] = []
        for number, entry in enumerate(_mappings(act.payload.get("options")), start=1):
            facts = entry.get("facts")
            declared = facts if isinstance(facts, Mapping) else {}
            columns = profile.columns or tuple(DisplayColumn(fact=str(name)) for name in declared)
            rows.append(
                OptionRow(
                    number=number,
                    option_id=_text(entry.get("option_id")) or "",
                    price=_price_text(catalogue, entry.get("price"), profile),
                    cells=tuple(
                        (
                            self._label(catalogue, _FACT_PREFIX, act.kind_key, column.fact),
                            _scalar_text(catalogue, declared[column.fact]) + column.unit,
                        )
                        for column in columns
                        if column.fact in declared
                    ),
                    filter_notes=tuple(
                        self._label(catalogue, _FIELD_PREFIX, act.kind_key, note)
                        for note in _texts(entry.get("filter_notes"))
                    ),
                )
            )
        return tuple(rows)


# -- reading the two files -------------------------------------------------------------------


def _refuse_unknown(
    document: Mapping[object, object], known: frozenset[str], what: str, path: Path
) -> None:
    """Refuse a top-level key nobody reads. A silently ignored key is a silently lost edit."""
    unknown = sorted(str(key) for key in document if str(key) not in known)
    if unknown:
        raise ConfigurationError(
            f"invalid {what} {path}: unknown top-level key(s) {', '.join(unknown)}; "
            f"expected {', '.join(sorted(known))}"
        )


def _require_declared_locale(
    document: Mapping[object, object], locale: str, what: str, path: Path
) -> None:
    """Refuse a file whose declared ``locale`` disagrees with its own name.

    The two say the same thing twice on purpose: the file name is what the loader resolves
    and the declaration is what a reader sees, and a copied-then-half-edited file is the way
    they come apart.
    """
    declared = document.get("locale")
    if declared is not None and declared != locale:
        raise ConfigurationError(
            f"invalid {what} {path}: it declares locale {declared!r}, but its name says {locale!r}"
        )


def _direction_of(document: Mapping[object, object], path: Path) -> Direction:
    """The declared writing direction, defaulting to ``ltr``."""
    declared = document.get("direction", "ltr")
    if declared == "ltr":
        return "ltr"
    if declared == "rtl":
        return "rtl"
    raise ConfigurationError(
        f"invalid Message Catalogue {path}: `direction` must be one of "
        f"{', '.join(_DIRECTIONS)}, got {declared!r}"
    )


def _minor_digits_of(document: Mapping[object, object], path: Path) -> int:
    """``money.minor_digits``, or the default when the file does not say."""
    money = document.get("money")
    if money is None:
        return DEFAULT_MINOR_DIGITS
    if not isinstance(money, Mapping):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: `money` must be a mapping with "
            f"`minor_digits`, got {money!r}"
        )
    _refuse_unknown(money, _MONEY_KEYS, "Display Profiles `money` in", path)
    digits = money.get("minor_digits", DEFAULT_MINOR_DIGITS)
    if not isinstance(digits, int) or isinstance(digits, bool) or digits < 0:
        raise ConfigurationError(
            f"invalid Display Profiles {path}: `money.minor_digits` must be a whole number "
            f"of digits, got {digits!r}"
        )
    return digits


def _profile_of(entry: object, minor_digits: int, name: str, path: Path) -> DisplayProfile:
    """One Display Profile — the ``default`` block or one Component Kind's."""
    if entry is None:
        return DisplayProfile(minor_digits=minor_digits)
    if not isinstance(entry, Mapping):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: profile {name!r} must be a mapping with "
            f"`columns` and `show_price`, got {entry!r}"
        )
    _refuse_unknown(entry, _PROFILE_KEYS, f"Display Profile {name!r} in", path)
    show_price = entry.get("show_price", True)
    if not isinstance(show_price, bool):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: profile {name!r} declares show_price "
            f"{show_price!r}, which is not a yes or a no"
        )
    declared = entry.get("columns")
    if declared is None:
        declared = []
    if not isinstance(declared, Sequence) or isinstance(declared, str):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: profile {name!r} declares columns "
            f"{declared!r}, which is not a list"
        )
    return DisplayProfile(
        columns=tuple(
            _column_of(column, position, name, path)
            for position, column in enumerate(declared, start=1)
        ),
        show_price=show_price,
        minor_digits=minor_digits,
    )


def _column_of(column: object, position: int, name: str, path: Path) -> DisplayColumn:
    """One table column. A column that names no fact reads nothing, so it is refused."""
    if not isinstance(column, Mapping):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: column {position} of profile {name!r} is not "
            f"a mapping ({column!r})"
        )
    _refuse_unknown(column, _COLUMN_KEYS, f"column {position} of Display Profile {name!r} in", path)
    fact = column.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        raise ConfigurationError(
            f"invalid Display Profiles {path}: column {position} of profile {name!r} must "
            f"name a `fact`, got {fact!r}"
        )
    unit = column.get("unit", "")
    if not isinstance(unit, str):
        raise ConfigurationError(
            f"invalid Display Profiles {path}: column {position} of profile {name!r} declares "
            f"unit {unit!r}, which is not text"
        )
    return DisplayColumn(fact=fact, unit=unit)


# -- turning payload values into text ----------------------------------------------------------


def _substitute(template: str, values: Mapping[str, str], *, origin: str) -> str:
    """Fill ``{name}`` from ``values``; ``{{`` and ``}}`` are literal braces.

    Hand-written by design rather than delegated to ``str.format``. A Message Catalogue is
    edited by translators, and ``format`` answers an unknown placeholder with ``KeyError`` and
    an unbalanced brace with ``ValueError`` — two ways for one typo to end a conversation.
    Here a placeholder nothing fills becomes a marker and a lone brace stays a lone brace.
    """
    out: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char in "{}" and template[index + 1 : index + 2] == char:
            out.append(char)
            index += 2
            continue
        if char != "{":
            out.append(char)
            index += 1
            continue
        closing = template.find("}", index + 1)
        name = "" if closing == -1 else template[index + 1 : closing]
        if not name or "{" in name:
            out.append(char)
            index += 1
            continue
        filled = values.get(name)
        if filled is None:
            LOGGER.warning(
                "message %r has no value for placeholder %r; rendering a marker",
                origin,
                name,
                extra={"kind": "language"},
            )
            filled = missing_marker(name)
        out.append(filled)
        index = closing + 1
    return "".join(out)


def _heading_keys(act: AssistantAct) -> tuple[str, ...]:
    """The heading's message keys, most specific first: reason code, then Kind, then Act.

    One rule for every Act. A Kind that wants its own phrasing writes ``<act>.<kind_key>``; a
    reason code that deserves better than the generic line writes ``<act>.<reason_code>``; and
    an installation that writes neither still gets a sentence.
    """
    reason = act.payload.get("reason_code")
    keys: list[str] = []
    if isinstance(reason, str) and reason:
        keys.append(f"{act.act}.{reason}")
    if act.kind_key:
        keys.append(f"{act.act}.{act.kind_key}")
    keys.append(act.act)
    return tuple(keys)


def _price_text(catalogue: MessageCatalogue, price: object, profile: DisplayProfile) -> str | None:
    """A Plan Option's price as the locale writes it, or ``None`` when there is none to write.

    ``None`` covers three different things on purpose — the option carries no price, the
    profile does not show one, and the payload's price is not readable — because a surface
    does the same thing with all three: leave the column out.
    """
    if not profile.show_price or not isinstance(price, Mapping):
        return None
    amount = price.get("amount_minor")
    currency = price.get("currency")
    if not isinstance(amount, int) or isinstance(amount, bool) or not isinstance(currency, str):
        return None
    return _substitute(
        catalogue.get(_MONEY_FORMAT),
        {"amount": _amount_text(amount, profile.minor_digits), "currency": currency},
        origin=_MONEY_FORMAT,
    )


def _amount_text(amount_minor: int, minor_digits: int) -> str:
    """74000 minor units at two digits is ``740.00``. Integer arithmetic, so it never drifts."""
    if minor_digits <= 0:
        return str(amount_minor)
    whole, remainder = divmod(abs(amount_minor), 10**minor_digits)
    sign = "-" if amount_minor < 0 else ""
    return f"{sign}{whole}.{remainder:0{minor_digits}d}"


def _scalar_text(catalogue: MessageCatalogue, value: object) -> str:
    """One payload value or option fact as text. Booleans and ``None`` are words, not repr."""
    if value is None:
        return catalogue.get(_VALUE_NONE)
    if value is True:
        return catalogue.get(_VALUE_TRUE)
    if value is False:
        return catalogue.get(_VALUE_FALSE)
    return str(value)


def _join(catalogue: MessageCatalogue, parts: Sequence[str]) -> str:
    """Join phrases with the locale's own separator, which is punctuation and so is data."""
    return catalogue.get(_LIST_SEPARATOR).join(parts)


def _count_of(payload: Mapping[str, object]) -> int | None:
    """What ``{count}`` counts in this payload, or ``None`` when it counts nothing."""
    for name in _COUNTABLE:
        value = payload.get(name)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return len(value)
    return None


def _is_scalar(value: object) -> bool:
    """Whether a payload value is one thing a message can name. ``bool`` is an ``int``."""
    return value is None or isinstance(value, (str, int, float))


def _phrase_of(catalogue: MessageCatalogue, value: object) -> str | None:
    """One payload value as a placeholder's worth of text, or ``None`` when it is not one.

    A scalar is itself and a tuple of scalars is a list; everything else — the option records
    on a slate, the Selections in a summary, a Blocking Rule's groups of field names — is
    structure, which a ``{placeholder}`` has no way to say and this refuses to guess at.
    """
    if _is_scalar(value):
        return _scalar_text(catalogue, value)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return None
    items = tuple(value)
    if not all(_is_scalar(item) for item in items):
        return None
    return _join(catalogue, tuple(_scalar_text(catalogue, item) for item in items))


def _texts(value: object) -> tuple[str, ...]:
    """A payload entry read as message keys or names: one string, or a sequence of them."""
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(item for item in value if isinstance(item, str) and item)
    return ()


def _text(value: object) -> str | None:
    """A payload entry read as one string, or ``None`` when it is anything else."""
    return value if isinstance(value, str) else None


def _mappings(value: object) -> tuple[Mapping[str, object], ...]:
    """A payload entry read as a sequence of structured records — options, Selections."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))
