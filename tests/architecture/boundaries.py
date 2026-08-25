"""An AST reading of the import rules, so they hold even without import-linter installed.

The contracts here are the same four in ``[tool.importlinter]`` in ``pyproject.toml``. Two
checks of one rule is deliberate duplication: import-linter is the gate that fails CI, and
this module is what still fails when someone removes the tool from the dev extras.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import tourganize

PACKAGE_ROOT: Final = Path(tourganize.__file__).resolve().parent
REPO_ROOT: Final = PACKAGE_ROOT.parent
ROOT_PACKAGE: Final = "tourganize"

PURE_PACKAGES: Final = ("tourganize.domain", "tourganize.dialogue")
ADAPTERS_PACKAGE: Final = "tourganize.adapters"
ADAPTER_IMPORTERS: Final = ("tourganize.application.composition",)
PORTS_PACKAGE: Final = "tourganize.ports"

PROBE_MODULE: Final = "_boundary_probe"
PROBE_SOURCE: Final = '''"""A deliberate violation, planted by a test and removed again."""

from __future__ import annotations

from tourganize.adapters.telemetry.null import NullTelemetrySink

__all__ = ["NullTelemetrySink"]
'''


@dataclass(frozen=True, slots=True)
class ImportEdge:
    """One import, as ``importer -> imported``."""

    importer: str
    imported: str
    line: int

    def __str__(self) -> str:
        return f"{self.importer}:{self.line} imports {self.imported}"


def module_name(path: Path) -> str:
    """Return the dotted name of the module at ``path``."""
    relative = path.resolve().relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def iter_modules() -> Iterator[tuple[str, Path]]:
    """Yield every module in the ``tourganize`` package, name first."""
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        yield module_name(path), path


def imports_of(name: str, path: Path) -> tuple[ImportEdge, ...]:
    """Return every module ``path`` imports, with relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]

    edges: list[ImportEdge] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges += [ImportEdge(name, alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            target = _resolve(package, node.module, node.level)
            edges.append(ImportEdge(name, target, node.lineno))
    return tuple(edges)


def _resolve(package: str, module: str | None, level: int) -> str:
    if level == 0:
        return module or ""
    parts = package.split(".")
    base = ".".join(parts[: len(parts) - (level - 1)]) if level > 1 else package
    return f"{base}.{module}" if module else base


def _is_stdlib(imported: str) -> bool:
    return imported.split(".")[0] in sys.stdlib_module_names


def _adapter_area(module: str) -> str | None:
    """Return the adapter sub-package a module belongs to, e.g. ``telemetry``."""
    prefix = f"{ADAPTERS_PACKAGE}."
    if not module.startswith(prefix):
        return None
    return module[len(prefix) :].split(".")[0]


def find_violations() -> tuple[str, ...]:
    """Return a human-readable line per import that breaks one of the four contracts."""
    violations: list[str] = []
    for name, path in iter_modules():
        for edge in imports_of(name, path):
            violations += judge(edge)
    return tuple(violations)


def judge(edge: ImportEdge) -> list[str]:
    """Return every contract ``edge`` breaks, as human-readable lines."""
    found: list[str] = []
    imported = edge.imported

    if (
        edge.importer.startswith(PURE_PACKAGES)
        and not _is_stdlib(imported)
        and not imported.startswith(PURE_PACKAGES)
    ):
        found.append(f"the domain must import only the standard library: {edge}")

    if edge.importer.startswith(PORTS_PACKAGE) and imported.startswith(ADAPTERS_PACKAGE):
        found.append(f"a port must not import an adapter: {edge}")

    importer_area = _adapter_area(edge.importer)
    imported_area = _adapter_area(imported)
    if importer_area is not None and imported_area is not None and importer_area != imported_area:
        found.append(f"adapter sub-packages must stay independent: {edge}")

    if imported.startswith(ADAPTERS_PACKAGE) and not edge.importer.startswith(
        (*ADAPTER_IMPORTERS, ADAPTERS_PACKAGE)
    ):
        found.append(f"only the Composition Root may import adapters: {edge}")

    return found


@contextmanager
def planted_violation() -> Iterator[Path]:
    """Create a domain module that imports an adapter, then remove it again.

    The probe is planted in the real tree rather than in a copy, so what it proves is that
    the contracts *in this repository* catch a violation — not that a parallel fixture
    package does.
    """
    path = PACKAGE_ROOT / "domain" / f"{PROBE_MODULE}.py"
    path.write_text(PROBE_SOURCE, encoding="utf-8")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
