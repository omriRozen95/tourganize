"""The Trip Planning domain: pure, dependency-free planning types.

Modules under this package may import the standard library and ``tourganize.dialogue``
and nothing else — no HTTP client, no LLM SDK, no MCP, no PDF library, no terminal
library, no database driver. The rule is enforced by import-linter (see ``pyproject.toml``)
and by ``tests/architecture/test_import_boundaries.py``.

Filled by F02 (trip plan core), F03 (requirements and gaps) and F04 (prioritization).
"""

from __future__ import annotations
