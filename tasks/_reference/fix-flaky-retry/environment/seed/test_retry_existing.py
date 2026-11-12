"""Behaviour that must keep working."""
from __future__ import annotations

import pytest

from retry import retry


def test_returns_on_first_success() -> None:
    calls = []

    @retry(attempts=3)
    def ok() -> str:
        calls.append(1)
        return "fine"

    assert ok() == "fine"
    assert len(calls) == 1


def test_reraises_the_last_error() -> None:
    @retry(attempts=2, base_delay=0.0)
    def always_bad() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        always_bad()
