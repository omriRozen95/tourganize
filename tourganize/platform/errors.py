"""The exception root for Tourganize.

Every error raised anywhere in the application derives from :class:`TourganizeError`, so a
surface can distinguish "something we modelled went wrong" from a genuine bug. Later
features derive their own exceptions from one of the four classes here, never from bare
``Exception``.
"""

from __future__ import annotations

__all__ = [
    "ConfigurationError",
    "ContractViolationError",
    "PortUnavailableError",
    "TourganizeError",
]


class TourganizeError(Exception):
    """Base class for every error Tourganize raises deliberately."""


class ConfigurationError(TourganizeError):
    """Settings or secrets could not be resolved into a valid configuration.

    Raised at start-up, before any port is built, so the process never runs
    half-configured. The CLI turns this into exit code 3.
    """


class PortUnavailableError(TourganizeError):
    """A port has no adapter wired, or its adapter cannot be reached."""


class ContractViolationError(TourganizeError):
    """Data crossing a port boundary did not satisfy the declared contract."""
