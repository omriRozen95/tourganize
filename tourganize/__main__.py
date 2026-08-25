"""Allow ``python -m tourganize`` to run the CLI without an installed console script."""

from __future__ import annotations

import sys

from tourganize.cli import main

if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
