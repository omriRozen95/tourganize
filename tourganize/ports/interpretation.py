"""The two ports the dialogue introduces: ``TurnInterpreter`` and ``OptionSlatePlanner``.

This module is the documented import path for both, and the place a reader looks for them —
``tourganize.ports`` is where the application's ports are listed. Neither is *defined* here,
for the reason [D17](../../docs/architecture/decisions.md) records — the same one D15 had
already recorded once, for ``PriorityPolicy``: a port's contract has to name the types it
carries, and these two carry Dialogue value objects — a ``UserTurn``, a ``DialogueState``, a
``PendingQuestion``. The
Director that consumes them lives in the dialogue as well, so defining them beside the value
objects is what keeps the dependency pointing one way instead of two packages reaching into
each other. See :mod:`tourganize.dialogue.ports`.

Adapters: ``KeywordTurnInterpreter`` in ``tourganize.adapters.interpretation.keyword`` (the
deterministic stand-in F08 replaces with a model-backed one) and ``FixedSlatePlanner`` in
``tourganize.adapters.options.fake`` (the fake F06 replaces with the real planning service over
the ``OptionSource`` port).
"""

from __future__ import annotations

from tourganize.dialogue.ports import DialogueContext, OptionSlatePlanner, TurnInterpreter

__all__ = ["DialogueContext", "OptionSlatePlanner", "TurnInterpreter"]
