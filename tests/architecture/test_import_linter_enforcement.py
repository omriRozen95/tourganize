"""Proof that ``lint-imports`` — the CI gate — actually rejects a boundary violation.

Skipped when import-linter is not installed, because the AST checks in
``test_import_boundaries.py`` cover the same rules without it.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest
from boundaries import REPO_ROOT, planted_violation

#: These tests plant a file in the real ``tourganize/`` tree and read it back, so they may
#: never run beside each other on two workers. One group name keeps the whole directory on a
#: single xdist worker; without ``-n`` the marker does nothing at all.
pytestmark = pytest.mark.xdist_group("repo_tree")

EXECUTABLE = shutil.which("lint-imports")
requires_import_linter = pytest.mark.skipif(
    EXECUTABLE is None, reason="import-linter is not installed"
)


def _lint_imports() -> subprocess.CompletedProcess[str]:
    assert EXECUTABLE is not None
    return subprocess.run(
        [EXECUTABLE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@requires_import_linter
def test_the_real_tree_passes() -> None:
    result = _lint_imports()

    assert result.returncode == 0, result.stdout + result.stderr


@requires_import_linter
def test_a_domain_module_importing_an_adapter_fails_the_gate() -> None:
    with planted_violation():
        result = _lint_imports()

    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert "_boundary_probe" in output
    assert "BROKEN" in output.upper()
