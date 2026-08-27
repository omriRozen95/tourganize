"""The ``OptionSlatePlanner`` fake: manufactured slates, no I/O, no fixture files."""

from __future__ import annotations

from tourganize.adapters.options.fake.fixed_slate_planner import (
    FIXED_PLANNER_SOURCE_ID,
    FixedSlatePlanner,
)

__all__ = ["FIXED_PLANNER_SOURCE_ID", "FixedSlatePlanner"]
