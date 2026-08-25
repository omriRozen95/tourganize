"""Adapters: every implementation of a port.

Two rules hold across this package: adapter sub-packages never import each other, and
nothing outside ``tourganize.application.composition`` and ``tourganize.cli`` may import
anything from here.
"""

from __future__ import annotations
