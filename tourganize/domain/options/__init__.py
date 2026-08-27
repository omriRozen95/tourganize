"""The option vocabulary: what a candidate is, what it costs, and where it came from.

Four names are re-exported here — ``Money``, ``PlanOption``, ``OptionSlate``, ``Provenance``.
Two more live in this package and are deliberately *not*:
:class:`~tourganize.domain.options.query.OptionQuery` and
:class:`~tourganize.domain.options.query.OptionSourceResult` name a ``Selection``, and
``tourganize.domain.trip`` already imports this package for the Option Slate a Trip Plan
records. Re-exporting them would make the two packages import each other at import time; both
modules say so in their own docstrings, and both are imported by module path instead — as
``requirements/values.py`` already imports ``options.money``.
:mod:`tourganize.domain.options.filters` is by module path for the same reason.
"""

from __future__ import annotations

from tourganize.domain.options.money import Money
from tourganize.domain.options.option import OptionSlate, PlanOption
from tourganize.domain.options.provenance import Provenance

__all__ = ["Money", "OptionSlate", "PlanOption", "Provenance"]
