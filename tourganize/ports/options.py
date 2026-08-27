"""The Option Sourcing ports: ``OptionSource``, ``OptionSourceRegistry`` and ``OptionRanking``.

This module is the documented import path for the three, and for the two value objects they
carry — :class:`~tourganize.domain.options.query.OptionQuery` and
:class:`~tourganize.domain.options.query.OptionSourceResult`, which are *defined* in the domain
and re-exported here for the reason their own module records.

``OptionSource`` is the port every present and future provider implements: the Fixture
Providers this feature ships, the MCP-backed source of F17, the commercial adapters of F24.
D9's promise — "a fixture's shape may never differ from the port contract" — is enforceable
only because the contract suite in ``tests/contracts/test_option_source_contract.py`` runs over
*every* adapter of this port, so an adapter is finished when that suite passes unmodified.

The other two are small on purpose.

``OptionSourceRegistry`` answers one question — which sources serve this Component Kind, in
what order — and exists as a port because the Planning Service consumes it and
``tourganize.application`` may not import an adapter. It is where
``TOURGANIZE_OPTION_SOURCE_PROFILE`` ends up: ``fixture`` today, ``world`` (F17) and ``live``
(F24) later, per Component Kind if the profile says so.

``OptionRanking`` is the replaceable order a slate is presented in. It is a port for the reason
``PriorityPolicy`` is one: the shipped answer is a defensible default rather than a fact about
travel, and a client who wants "cheapest first, ignore everything else" should be able to have
it by swapping one small object. A ranking never *removes* an option — truncation to the slate
size is the Planning Service's, and discarding a filtered-out option is configuration.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from tourganize.domain.options import PlanOption
from tourganize.domain.options.query import OptionQuery, OptionSourceResult

__all__ = [
    "OptionQuery",
    "OptionRanking",
    "OptionSource",
    "OptionSourceRegistry",
    "OptionSourceResult",
]


@runtime_checkable
class OptionSource(Protocol):
    """Answers an Option Query with candidate Plan Options for one Component Kind.

    Three obligations, all of them checked by the contract suite: the options come back with
    :class:`~tourganize.domain.options.provenance.Provenance` on every one of them, they belong
    to the Component Kind that was asked about, and their ``option_id``s are unique within the
    result and stable across identical queries. A priced option carries a currency — there is
    no bare number anywhere — and no option carries prose, because wording is composed per
    locale at presentation time and a provider does not know the traveller's language.

    Raising is allowed and is how a source says it could not answer. The Planning Service
    records it as a diagnostic and asks the next source; only when *every* source for a kind
    fails does the caller see :class:`~tourganize.platform.errors.OptionSourcingError`.
    """

    @property
    def source_id(self) -> str:
        """This source's stable identity, as it appears in Provenance and telemetry."""
        ...

    @property
    def kind_keys(self) -> frozenset[str]:
        """The Component Kinds this source holds data for.

        Advisory rather than a gate: a source that is asked about a Kind it does not list may
        answer with an empty result, or with whatever it can derive. What it may **not** do is
        answer with options of a different Kind.
        """
        ...

    def search(self, query: OptionQuery) -> OptionSourceResult:
        """Return this source's candidates for ``query``, at most ``query.slate_size`` of them."""
        ...


@runtime_checkable
class OptionSourceRegistry(Protocol):
    """Which Option Sources serve a Component Kind, and in which order they are called."""

    def sources_for(self, kind_key: str) -> tuple[OptionSource, ...]:
        """The sources to call for ``kind_key``, in call order.

        Raises :class:`~tourganize.domain.errors.UnknownComponentKindError` when the configured
        profile leaves a Kind with no source at all — a configuration bug rather than a
        traveller's, and one ``doctor`` reports before a conversation starts. That is the
        second use of the error the glossary records, and the only one this port has: it is
        raised **about a Kind**, and a registry problem that names no Kind — a Source Profile
        listing the same ``source_id`` twice — is a
        :class:`~tourganize.platform.errors.ConfigurationError` instead.
        """
        ...

    def profile_for(self, kind_key: str) -> str:
        """The Source Profile name in force for ``kind_key`` — what ``doctor`` prints."""
        ...


@runtime_checkable
class OptionRanking(Protocol):
    """Orders the options of one slate. Never adds, removes or edits one."""

    @property
    def ranking_id(self) -> str:
        """This ranking's name, recorded in telemetry so a slate can be explained later."""
        ...

    def order(self, options: Sequence[PlanOption], query: OptionQuery) -> Sequence[PlanOption]:
        """Return exactly ``options``, reordered.

        Checked at the seam rather than trusted, like a ``PriorityPolicy``: a ranking that
        invents, drops or repeats an option is refused with
        :class:`~tourganize.platform.errors.ContractViolationError`.
        """
        ...
