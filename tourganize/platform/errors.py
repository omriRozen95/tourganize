"""The exception root for Tourganize.

Every error raised anywhere in the application derives from :class:`TourganizeError`, so a
surface can distinguish "something we modelled went wrong" from a genuine bug. Later
features derive their own exceptions from one of the classes here, never from bare
``Exception``.

:class:`TourganizeError` itself is defined in :mod:`tourganize.domain.errors` and re-exported
here. The domain may import nothing but the standard library and itself, so the root has to
live where the domain can reach it; this module stays the one place to read the hierarchy
from, and the documented import path is unchanged.
"""

from __future__ import annotations

from tourganize.domain.errors import TourganizeError

__all__ = [
    "CatalogError",
    "ConfigurationError",
    "ContractViolationError",
    "PortUnavailableError",
    "TourganizeError",
]


class ConfigurationError(TourganizeError):
    """Settings or secrets could not be resolved into a valid configuration.

    Raised at start-up, before any port is built, so the process never runs
    half-configured. The CLI turns this into exit code 3.
    """


class PortUnavailableError(TourganizeError):
    """A port has no adapter wired, or its adapter cannot be reached."""


class ContractViolationError(TourganizeError):
    """Data crossing a port boundary did not satisfy the declared contract."""


class CatalogError(ConfigurationError):
    """The Component Catalog could not be loaded, or is not a valid catalog.

    A configuration error rather than a domain error: the *invariants* it reports are the
    domain's (``tourganize.domain.catalog.catalog_problems`` finds them), but a file that is
    missing, unreadable or self-contradictory is a misconfigured installation, and callers
    already treat ``ConfigurationError`` as "exit 3, do not start".
    """
