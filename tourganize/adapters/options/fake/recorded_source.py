"""``RecordedOptionSource`` — the Option Source that answers with what it was handed.

Two jobs, and they are the same object because they are the same need: to make an Option Source
*predictable* and to make what the Planning Service asked it *visible*.

A test that is about merging, ranking, de-duplication or filtering needs options with exactly
the prices and facts the case is about, and building a fixture tree for each of those would put
the thing under test behind a file format. A test that is about the **query** — that the slate
size arrived, that the Selections an Outcome Dependency entitles a Kind to read were passed
through, that a refinement carried a different digest — needs to look at what the source
received. :attr:`queries` is that, in order.

It is a fake in the strict sense D9 means: its shape is the port's, so it appears in the
``OptionSource`` contract suite beside the Fixture Provider and has to pass it unmodified.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Final, final

from tourganize.domain.options import PlanOption
from tourganize.domain.options.query import OptionQuery, OptionSourceResult

__all__ = ["RECORDED_SOURCE_ID", "RecordedOptionSource"]

#: What this fake calls itself, unless a test needs two of them told apart.
RECORDED_SOURCE_ID: Final = "fake:recorded"


@final
class RecordedOptionSource:
    """Answers every query for a Component Kind with the options recorded for it."""

    def __init__(
        self,
        options: Mapping[str, Sequence[PlanOption]],
        retrieved_at: datetime,
        *,
        source_id: str = RECORDED_SOURCE_ID,
        diagnostics: Iterable[str] = (),
        partial: bool = False,
    ) -> None:
        self._options = {kind_key: tuple(items) for kind_key, items in options.items()}
        self._retrieved_at = retrieved_at
        self._source_id = source_id
        self._diagnostics = tuple(diagnostics)
        self._partial = partial
        #: Every query this source was handed, in order.
        self.queries: list[OptionQuery] = []

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def kind_keys(self) -> frozenset[str]:
        return frozenset(self._options)

    def search(self, query: OptionQuery) -> OptionSourceResult:
        """Return the recorded options for ``query.kind_key``, truncated to the slate size.

        Truncating here rather than leaving it to the caller is the port's rule — "at most
        ``query.slate_size``" — and a fake that ignored it would be a fake whose shape differs
        from the contract, which is the one thing D9 forbids.
        """
        self.queries.append(query)
        held = self._options.get(query.kind_key, ())
        chosen = held[: query.slate_size]
        return OptionSourceResult(
            options=chosen,
            source_id=self._source_id,
            retrieved_at=self._retrieved_at,
            partial=self._partial or len(chosen) < len(held),
            diagnostics=self._diagnostics,
        )
