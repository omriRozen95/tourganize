"""``OptionSource`` and ``OptionSlatePlanner`` adapters.

``fixture`` is the Fixture Provider — the permanent test default (D9) and, until F17 and F24,
the only real source of option data. ``fake`` holds the three in-process stand-ins: F05's
``FixedSlatePlanner``, and F06's ``RecordedOptionSource`` and ``FailingOptionSource``.
``registry`` maps a Component Kind to the sources its Source Profile names, and ``ranking``
holds the shipped ``OptionRanking``. ``world`` (F17) and ``live`` (F24) arrive beside them,
behind the same port and passing the same contract suite.
"""

from __future__ import annotations

from tourganize.adapters.options.ranking import (
    CHEAPEST_FIRST_RANKING_ID,
    CheapestFirstRanking,
)
from tourganize.adapters.options.registry import SourceRegistry

__all__ = ["CHEAPEST_FIRST_RANKING_ID", "CheapestFirstRanking", "SourceRegistry"]
