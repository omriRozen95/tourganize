"""The Option Sourcing fakes: manufactured slates, replayed results, and a source that fails.

``FixedSlatePlanner`` is F05's ``OptionSlatePlanner`` fake and stays what the state-machine
tests drive; ``RecordedOptionSource`` and ``FailingOptionSource`` are F06's ``OptionSource``
fakes. All three exist so that no test needs a network, a key or a fixture tree — and, per D9,
they remain the default long after live providers exist.
"""

from __future__ import annotations

from tourganize.adapters.options.fake.failing_source import (
    FAILING_SOURCE_ID,
    FailingOptionSource,
)
from tourganize.adapters.options.fake.fixed_slate_planner import (
    FIXED_PLANNER_SOURCE_ID,
    FixedSlatePlanner,
)
from tourganize.adapters.options.fake.recorded_source import (
    RECORDED_SOURCE_ID,
    RecordedOptionSource,
)

__all__ = [
    "FAILING_SOURCE_ID",
    "FIXED_PLANNER_SOURCE_ID",
    "RECORDED_SOURCE_ID",
    "FailingOptionSource",
    "FixedSlatePlanner",
    "RecordedOptionSource",
]
