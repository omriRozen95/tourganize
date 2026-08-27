"""``SourceRegistry`` — which Option Sources serve which Component Kind, and in what order.

This is where ``TOURGANIZE_OPTION_SOURCE_PROFILE`` lands. The value names a **Source Profile**
— ``fixture`` today, ``world`` in F17, ``live`` in F24 — either once for every Component Kind or
per Kind, and this object turns the parsed profile into the ordered sources the Planning
Service calls. Nothing above it knows a profile exists; nothing below it knows a Component
Catalog does.

**Two sources for one Kind is a supported configuration, not an accident.** F17's stated design
is a world-backed source with the Fixture Provider behind it as the fallback a dead server
cannot take down, so the registry returns a *sequence* and the Planning Service calls it in
order, merging what comes back. Registering the same source twice is refused: it would
double-count every option and de-duplication would hide the mistake.

The registry is built by the Composition Root and reads no file, exactly as the catalog adapter
does not: a fixture tree that is missing is a failing ``doctor`` check, not an unwireable
application.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import final

from tourganize.domain.errors import UnknownComponentKindError
from tourganize.platform.errors import ConfigurationError
from tourganize.platform.settings import OptionSourceProfile, SourceProfileName
from tourganize.ports.options import OptionSource

__all__ = ["SourceRegistry"]


@final
class SourceRegistry:
    """Maps a ``kind_key`` to the ordered Option Sources its Source Profile names."""

    def __init__(
        self,
        profile: OptionSourceProfile,
        sources: Mapping[SourceProfileName, Sequence[OptionSource]],
    ) -> None:
        self._profile = profile
        self._sources = {name: tuple(items) for name, items in sources.items()}
        for name, items in self._sources.items():
            identifiers = [source.source_id for source in items]
            if len(set(identifiers)) != len(identifiers):
                # A `ConfigurationError`, and not the `UnknownComponentKindError` a missing
                # source raises: no `kind_key` is involved here at all. The glossary reserves
                # that error for a Kind, and a Source Profile that names the same provider
                # twice is a wiring mistake in the installation — exit code 3, like every
                # other configuration that cannot be resolved.
                raise ConfigurationError(
                    f"the {name!r} Source Profile registers {', '.join(identifiers)}, which "
                    f"repeats a source_id: every option would be counted twice"
                )

    def sources_for(self, kind_key: str) -> tuple[OptionSource, ...]:
        """The sources to call for ``kind_key``, in call order."""
        name = self._profile.for_kind(kind_key)
        found = self._sources.get(name, ())
        if not found:
            raise UnknownComponentKindError(
                f"no Option Source is registered for {kind_key!r}: its Source Profile is "
                f"{name!r}, and this installation wires "
                f"{', '.join(sorted(self._sources)) or 'no profile at all'}"
            )
        return found

    def profile_for(self, kind_key: str) -> str:
        """The Source Profile name in force for ``kind_key``."""
        return self._profile.for_kind(kind_key)

    def describe(self, kind_key: str) -> str:
        """``profile: source, source`` for ``kind_key`` — one line of ``doctor``'s report."""
        try:
            sources = self.sources_for(kind_key)
        except UnknownComponentKindError:
            return f"{self.profile_for(kind_key)}: nothing wired"
        return f"{self.profile_for(kind_key)}: {', '.join(source.source_id for source in sources)}"
