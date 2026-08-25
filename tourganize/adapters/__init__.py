"""Adapters: every implementation of a port.

Two rules hold across this package: adapter sub-packages never import each other, and
``tourganize.application.composition`` is the only module anywhere that may import from
here. Everything else, the CLI included, receives its adapters from the Container.
"""

from __future__ import annotations
