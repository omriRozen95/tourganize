"""Ports: abstract protocols only, typed with the standard library.

A port never imports an adapter. Each port is introduced by one feature and ships with at
least one fake in ``tourganize.adapters`` in that same feature.
"""

from __future__ import annotations
