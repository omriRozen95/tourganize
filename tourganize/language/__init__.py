"""Language Services: Prompt Library, locale detection, Message Catalogue, bidi shaping.

F07 fills in the first half of that list: the **Message Catalogue** and the **Act Renderer**
that reads it, which is the only place an Assistant Act becomes text in Phase 1. F08 adds the
prompts, and F10 adds locale detection, the real bilingual handling and bidi shaping — both
plug in behind the seam this module already exposes.

Nothing here is an adapter. The Act Renderer reads two configuration files and holds a
``ComponentCatalog``; a surface consumes it and the Composition Root builds it, which is why
it lives in ``tourganize.language`` rather than under ``tourganize.adapters``.
"""

from __future__ import annotations

from tourganize.language.act_renderer import (
    DEFAULT_MINOR_DIGITS,
    MISSING_MARKER_CLOSE,
    MISSING_MARKER_OPEN,
    ActRenderer,
    Direction,
    DisplayColumn,
    DisplayProfile,
    DisplayProfiles,
    MessageCatalogue,
    OptionRow,
    RenderedAct,
    load_display_profiles,
    load_message_catalogue,
    missing_marker,
)

__all__ = [
    "DEFAULT_MINOR_DIGITS",
    "MISSING_MARKER_CLOSE",
    "MISSING_MARKER_OPEN",
    "ActRenderer",
    "Direction",
    "DisplayColumn",
    "DisplayProfile",
    "DisplayProfiles",
    "MessageCatalogue",
    "OptionRow",
    "RenderedAct",
    "load_display_profiles",
    "load_message_catalogue",
    "missing_marker",
]
