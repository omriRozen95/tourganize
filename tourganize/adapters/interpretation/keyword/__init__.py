"""The keyword-based ``TurnInterpreter``, and the phrase tables it reads."""

from __future__ import annotations

from tourganize.adapters.interpretation.keyword.keyword_interpreter import (
    HEBREW_LOCALE,
    KeywordTurnInterpreter,
)
from tourganize.adapters.interpretation.keyword.phrase_tables import (
    KEYWORD_FILE_PATTERN,
    SHAPE_DATE_RANGE,
    SHAPE_PLACE,
    SHAPES,
    PhraseTable,
    load_phrase_tables,
    read_phrase_table,
)

__all__ = [
    "HEBREW_LOCALE",
    "KEYWORD_FILE_PATTERN",
    "SHAPES",
    "SHAPE_DATE_RANGE",
    "SHAPE_PLACE",
    "KeywordTurnInterpreter",
    "PhraseTable",
    "load_phrase_tables",
    "read_phrase_table",
]
