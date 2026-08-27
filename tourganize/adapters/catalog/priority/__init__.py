"""Adapters of the ``PriorityPolicy`` port: the shipped weighted policy, and the fake.

Both read only what a Component Kind declares, which is why they live beside the catalog
adapters rather than in an area of their own: replacing the *policy* and replacing the *source*
of the declarations are the same kind of change.
"""

from __future__ import annotations

from tourganize.adapters.catalog.priority.fixed_policy import FIXED_POLICY_ID, FixedOrderPolicy
from tourganize.adapters.catalog.priority.weighted_policy import (
    WEIGHTED_POLICY_ID,
    WeightedCatalogPolicy,
)

__all__ = [
    "FIXED_POLICY_ID",
    "WEIGHTED_POLICY_ID",
    "FixedOrderPolicy",
    "WeightedCatalogPolicy",
]
