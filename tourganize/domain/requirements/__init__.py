"""Requirement Schema, Field Spec, Requirement Set, Gap Report — what has to be known.

The Component Catalog says *which* topics can be planned; this package says what has to be
known before one of them can be. Both are data: a Requirement Schema is a file under
``${TOURGANIZE_SCHEMA_DIR}``, read by an adapter, and everything here is pure — no file
access, no YAML, no clock. Relative dates in particular are resolved *before* a value reaches
this package, at the interpretation boundary, which is why nothing here imports a ``Clock``.

Four modules, in dependency order: :mod:`~tourganize.domain.requirements.schema` declares what
a schema is, :mod:`~tourganize.domain.requirements.validation` says what a valid value of each
Field Kind is, :mod:`~tourganize.domain.requirements.values` holds the values collected so far,
and :mod:`~tourganize.domain.requirements.gaps` reports what is still missing.
"""

from __future__ import annotations

from tourganize.domain.requirements.gaps import (
    BlockingGap,
    CandidateGroup,
    GapReport,
    InvalidValue,
    analyse,
)
from tourganize.domain.requirements.schema import (
    CONSTRAINT_KEYS,
    FIELD_NAME_PATTERN,
    BlockingRule,
    FieldKind,
    FieldSpec,
    Obligation,
    RequirementSchema,
    schema_problems,
)
from tourganize.domain.requirements.validation import (
    REASON_MESSAGE_KEYS,
    VALIDATORS,
    DateRange,
    normalise,
)
from tourganize.domain.requirements.values import (
    PRECEDENCE,
    RequirementSet,
    RequirementSource,
    RequirementUpdate,
    RequirementValue,
    SupersededValue,
    Supersession,
)

__all__ = [
    "CONSTRAINT_KEYS",
    "FIELD_NAME_PATTERN",
    "PRECEDENCE",
    "REASON_MESSAGE_KEYS",
    "VALIDATORS",
    "BlockingGap",
    "BlockingRule",
    "CandidateGroup",
    "DateRange",
    "FieldKind",
    "FieldSpec",
    "GapReport",
    "InvalidValue",
    "Obligation",
    "RequirementSchema",
    "RequirementSet",
    "RequirementSource",
    "RequirementUpdate",
    "RequirementValue",
    "SupersededValue",
    "Supersession",
    "analyse",
    "normalise",
    "schema_problems",
]
