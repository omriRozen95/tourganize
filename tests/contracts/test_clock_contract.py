"""The ``Clock`` contract, run against every adapter of the port — fakes included.

A new ``Clock`` adapter is done when this file passes **unmodified**.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest

from tourganize.adapters.clock.fake import DEFAULT_MOMENT, FrozenClock
from tourganize.adapters.clock.system import SystemClock
from tourganize.platform.errors import ContractViolationError
from tourganize.ports.platform import Clock

#: Every adapter of the port, keyed by the name the test ids use.
CLOCKS: dict[str, Callable[[], Clock]] = {
    "SystemClock": SystemClock,
    "FrozenClock": FrozenClock,
    "FrozenClock(stepping)": lambda: FrozenClock(step=timedelta(seconds=1)),
}


@pytest.mark.parametrize("build", CLOCKS.values(), ids=CLOCKS)
def test_now_returns_an_aware_datetime(build: Callable[[], Clock]) -> None:
    moment = build().now()

    assert isinstance(moment, datetime)
    assert moment.tzinfo is not None
    assert moment.tzinfo.utcoffset(moment) is not None


@pytest.mark.parametrize("build", CLOCKS.values(), ids=CLOCKS)
def test_time_never_runs_backwards(build: Callable[[], Clock]) -> None:
    clock = build()

    readings = [clock.now() for _ in range(5)]

    assert readings == sorted(readings)


@pytest.mark.parametrize("build", CLOCKS.values(), ids=CLOCKS)
def test_the_port_is_satisfied_structurally(build: Callable[[], Clock]) -> None:
    assert isinstance(build(), Clock)


def test_the_fake_is_controllable() -> None:
    clock = FrozenClock()

    assert clock.now() == DEFAULT_MOMENT
    assert clock.now() == DEFAULT_MOMENT

    clock.advance(timedelta(hours=2))
    assert clock.now() == DEFAULT_MOMENT + timedelta(hours=2)

    clock.set_to(DEFAULT_MOMENT)
    assert clock.now() == DEFAULT_MOMENT


def test_the_fake_steps_when_asked_to() -> None:
    clock = FrozenClock(step=timedelta(minutes=1))

    first, second = clock.now(), clock.now()

    assert second - first == timedelta(minutes=1)


def test_the_fake_refuses_a_naive_moment() -> None:
    with pytest.raises(ContractViolationError):
        FrozenClock(datetime(2026, 1, 1, 12, 0))
