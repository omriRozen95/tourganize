"""The Trip Plan aggregate: its components, their lifecycle, its completeness."""

from __future__ import annotations

from tourganize.domain.trip.completeness import PlanCompleteness
from tourganize.domain.trip.component import (
    LEGAL_TRANSITIONS,
    SETTLED_STATUSES,
    ComponentStatus,
    PlanComponent,
)
from tourganize.domain.trip.plan import TripPlan
from tourganize.domain.trip.selection import Selection

__all__ = [
    "LEGAL_TRANSITIONS",
    "SETTLED_STATUSES",
    "ComponentStatus",
    "PlanCompleteness",
    "PlanComponent",
    "Selection",
    "TripPlan",
]
