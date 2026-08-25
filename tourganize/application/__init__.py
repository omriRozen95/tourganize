"""Application services and the Composition Root.

``composition.build_container`` is the only place in the codebase that constructs adapters.
Later features add ``PlanningService`` (F05) and ``ExportService`` (F13) here.
"""

from __future__ import annotations
