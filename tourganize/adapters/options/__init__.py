"""``OptionSource`` and ``OptionSlatePlanner`` adapters.

``fake`` is the ``OptionSlatePlanner`` F05 drives its state machine with — manufactured slates,
no I/O. The rest arrive with the features that need them: ``fixture`` (F06), ``world`` (F17) and
``live`` (F24).
"""

from __future__ import annotations
