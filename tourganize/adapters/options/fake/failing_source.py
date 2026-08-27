"""``FailingOptionSource`` — the Option Source that never answers.

Failure containment is a *rule* rather than an accident: one source of two failing must still
produce a slate from the survivor, and *every* source failing must produce
:class:`~tourganize.platform.errors.OptionSourcingError` rather than whatever exception the
provider happened to raise. Both are testable only if failing on demand is something a source
can be asked to do, which is what this fake is for — no broken provider, no unplugged network,
no patched module.

It raises for every query, unconditionally. A registry that needs one working source and one
broken source for the same Component Kind registers this beside a real one; a source that
worked for some Kinds and not others would be a second behaviour to reason about and has no
test that wants it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final, final

from tourganize.domain.options.query import OptionQuery, OptionSourceResult
from tourganize.platform.errors import PortUnavailableError

__all__ = ["FAILING_SOURCE_ID", "FailingOptionSource"]

#: What this fake calls itself, unless a test needs two of them told apart.
FAILING_SOURCE_ID: Final = "fake:failing"


@final
class FailingOptionSource:
    """Raises instead of answering, and remembers what it was asked."""

    def __init__(
        self,
        *,
        source_id: str = FAILING_SOURCE_ID,
        kind_keys: Iterable[str] = (),
    ) -> None:
        self._source_id = source_id
        self._kind_keys = frozenset(kind_keys)
        #: Every query this source was handed, in order — so a test can prove it was *asked*
        #: before it failed, rather than skipped.
        self.queries: list[OptionQuery] = []

    @property
    def source_id(self) -> str:
        return self._source_id

    @property
    def kind_keys(self) -> frozenset[str]:
        return self._kind_keys

    def search(self, query: OptionQuery) -> OptionSourceResult:
        """Record the query and raise."""
        self.queries.append(query)
        raise PortUnavailableError(f"{self._source_id} cannot answer for {query.kind_key!r}")
