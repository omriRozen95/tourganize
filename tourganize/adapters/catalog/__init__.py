"""Adapters of the ``ComponentCatalog`` and ``PriorityPolicy`` ports.

``yaml`` loads the shipped ``config/catalog/components.yaml``; ``memory`` is the fake every
test builds a catalog with, without touching a file; ``priority`` holds the two Priority
Policies, which order the Kinds those catalogs declare.
"""

from __future__ import annotations
