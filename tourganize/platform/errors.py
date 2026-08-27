"""The exception root for Tourganize.

Every error raised anywhere in the application derives from :class:`TourganizeError`, so a
surface can distinguish "something we modelled went wrong" from a genuine bug. Later
features derive their own exceptions from one of the classes here, never from bare
``Exception``.

:class:`TourganizeError` itself is defined in :mod:`tourganize.domain.errors` and re-exported
here, as is :class:`ContractViolationError`. The domain may import nothing but the standard
library and itself, so an error the domain raises has to live where the domain can reach it —
the root because everything inherits from it, and the contract violation because F04's
Planning Agenda checks a replaceable ``PriorityPolicy``'s output inside the domain. This
module stays the one place to read the hierarchy from, and both documented import paths are
unchanged.
"""

from __future__ import annotations

from tourganize.domain.errors import ContractViolationError, TourganizeError

__all__ = [
    "CatalogError",
    "ConfigurationError",
    "ContractViolationError",
    "OptionSourcingError",
    "PortUnavailableError",
    "SchemaError",
    "TourganizeError",
]


class ConfigurationError(TourganizeError):
    """Settings or secrets could not be resolved into a valid configuration.

    Raised at start-up, before any port is built, so the process never runs
    half-configured. The CLI turns this into exit code 3.
    """


class PortUnavailableError(TourganizeError):
    """A port has no adapter wired, or its adapter cannot be reached."""


class CatalogError(ConfigurationError):
    """The Component Catalog could not be loaded, or is not a valid catalog.

    A configuration error rather than a domain error: the *invariants* it reports are the
    domain's (``tourganize.domain.catalog.catalog_problems`` finds them), but a file that is
    missing, unreadable or self-contradictory is a misconfigured installation, and callers
    already treat ``ConfigurationError`` as "exit 3, do not start".
    """


class SchemaError(CatalogError):
    """A Requirement Schema is missing, unreadable, or not a valid schema.

    A *Catalog* error because a Component Kind and the Requirement Schema it names are one
    declaration split across two files: a kind whose ``schema_key`` resolves to nothing is as
    broken an installation as a kind with a dangling Outcome Dependency, and both are exit 3.
    Values that fail a schema are a different thing entirely — those are the traveller's, and
    they raise ``RequirementValueError`` from the domain instead.
    """


class OptionSourcingError(PortUnavailableError):
    """Every Option Source registered for one Component Kind failed.

    A :class:`PortUnavailableError` because that is exactly what it is: the ``OptionSource``
    port had adapters, and none of them could be reached. *One* source failing is not this —
    it is a diagnostic on the result and a source the Planning Service skips — because a slate
    from the survivor is worth more than a refusal.

    The Dialogue Director turns it into a ``report_sourcing_failure`` Act and moves to the next
    Agenda entry. A conversation must not end because a provider did.
    """
