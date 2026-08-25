"""Every deliberate failure must be catchable as one exception type."""

from __future__ import annotations

import pytest

from tourganize.platform.errors import (
    ConfigurationError,
    ContractViolationError,
    PortUnavailableError,
    TourganizeError,
)


@pytest.mark.parametrize(
    "error_type",
    [ConfigurationError, PortUnavailableError, ContractViolationError],
)
def test_every_error_descends_from_the_root(error_type: type[TourganizeError]) -> None:
    assert issubclass(error_type, TourganizeError)
    assert issubclass(error_type, Exception)

    with pytest.raises(TourganizeError):
        raise error_type("boom")
