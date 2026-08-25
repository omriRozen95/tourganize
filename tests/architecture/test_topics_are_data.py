"""The rule that makes Component Kinds data: no travel topic may be named in the package.

If ``lodging`` appears anywhere in ``tourganize/``, something has started to know about a
specific topic — and the promise that adding ``dining`` costs no Python is already broken. The
check is mechanical rather than by eye, because this is exactly the kind of rule that erodes
one convenient ``if`` at a time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from boundaries import PACKAGE_ROOT, REPO_ROOT

#: The Component Kinds shipped in ``config/catalog/components.yaml``. They are allowed to
#: appear in configuration, fixtures, tests and documentation — and nowhere in the package.
SHIPPED_KIND_KEYS: Final = ("air_travel", "lodging", "ground_transport")
CATALOG_FILE: Final = REPO_ROOT / "config" / "catalog" / "components.yaml"


def hits_in(root: Path) -> list[str]:
    """Return ``path:line: key`` for every shipped kind_key mentioned in a file under ``root``.

    Every file, not only ``*.py``, and prose counts: the point of the rule is that nothing in
    the package knows a topic by name, and a comment that names one is a comment that will
    grow code around it.
    """
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            found += [f"{path}:{number}: {key}" for key in SHIPPED_KIND_KEYS if key in line]
    return found


def test_no_shipped_kind_key_appears_in_the_package() -> None:
    assert hits_in(PACKAGE_ROOT) == []


def test_the_kind_keys_do_exist_where_they_belong() -> None:
    """The negative test above would also pass if the keys had simply been renamed."""
    declared = CATALOG_FILE.read_text(encoding="utf-8")

    for key in SHIPPED_KIND_KEYS:
        assert f"kind_key: {key}" in declared


def test_the_check_would_notice_a_topic_leaking_into_the_package(tmp_path: Path) -> None:
    """A planted violation, so the test above is known to be able to fail."""
    planted = tmp_path / "leaky.py"
    planted.write_text('if kind_key == "lodging":  # a branch per topic\n    pass\n')

    assert hits_in(tmp_path) == [f"{planted}:1: lodging"]
