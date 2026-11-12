"""A retry decorator. Currently nondeterministic under load."""
from __future__ import annotations

import functools
import time
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def retry(attempts: int = 3, base_delay: float = 0.05) -> Callable[[F], F]:
    """Retry a callable with exponential backoff.

    BUG: the loop gives up once a wall-clock deadline passes, so a slow or
    loaded machine performs fewer attempts than a fast one. The number of
    attempts is supposed to be a property of the code, not of the host.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            deadline = time.time() + attempts * base_delay * 4
            last_error: Exception | None = None
            tries = 0
            while time.time() < deadline:
                tries += 1
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    time.sleep(base_delay * (2 ** (tries - 1)))
            if last_error is not None:
                raise last_error
            raise RuntimeError("retry exhausted without calling the function")

        return wrapper  # type: ignore[return-value]

    return decorator
