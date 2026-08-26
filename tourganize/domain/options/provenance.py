"""``Provenance`` — where a Plan Option or a Knowledge Passage came from.

Every fact the assistant presents has to be traceable back to whatever produced it: a fixture
file, a live provider, an MCP tool, a document in the corpus. That is what makes a slate
auditable and what F19's citations hang off. The timestamp is timezone-aware and comes from
the ``Clock`` port, so a replayed conversation carries the moment it was recorded with.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from tourganize.domain.invariants import require_aware, require_text

__all__ = ["Provenance"]


@dataclass(frozen=True, slots=True)
class Provenance:
    """The origin of one piece of retrieved data."""

    source_id: str
    retrieved_at: datetime
    external_ref: str | None = None
    citations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_text(self.source_id, "Provenance.source_id")
        require_aware(self.retrieved_at, "Provenance.retrieved_at")
