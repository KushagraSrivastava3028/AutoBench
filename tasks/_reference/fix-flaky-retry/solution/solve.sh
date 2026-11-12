#!/usr/bin/env bash
set -euo pipefail
APP="${AUTOBENCH_WORKDIR:-/app}"

cat > "$APP"/retry.py <<'EOF'
"""A retry decorator with deterministic attempt counts."""
from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def retry(
    attempts: int = 3,
    base_delay: float = 0.05,
    sleeper: Callable[[float], None] | None = None,
) -> Callable[[F], F]:
    """Retry a callable up to ``attempts`` times.

    The attempt count is a property of the code, not of the host: the loop
    counts attempts rather than watching a wall-clock deadline. The delay is
    injectable so tests can run at full speed without patching time.
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if sleeper is not None and attempt < attempts - 1:
                        sleeper(base_delay * (2**attempt))
            assert last_error is not None
            raise last_error

        return wrapper  # type: ignore[return-value]

    return decorator
EOF
