"""The file-backed ``ComponentCatalog`` and the Requirement Schemas it resolves to."""

from __future__ import annotations

from tourganize.adapters.catalog.yaml.yaml_catalog import (
    CATALOG_VERSION,
    YamlComponentCatalog,
)
from tourganize.adapters.catalog.yaml.yaml_schemas import (
    SCHEMA_FILE_SUFFIX,
    load_schema,
    schema_path,
)

__all__ = [
    "CATALOG_VERSION",
    "SCHEMA_FILE_SUFFIX",
    "YamlComponentCatalog",
    "load_schema",
    "schema_path",
]
