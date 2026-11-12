"""The three-tier concurrency budget (PRD §8.2).

One global concurrency number is always wrong. Container work is CPU and disk
bound, model calls are network bound, and the Docker daemon serialises creates
regardless of how many cores you have. A single semaphore tuned for one of
those starves the others:

* tuned for the API  -> the Docker host thrashes, trials time out
* tuned for the host -> the provider sits idle waiting on container churn

So each bottleneck gets its own lane, and the run is bounded by whichever one
actually binds. ``/metrics`` exposes per-lane utilisation precisely so you can
see which one that is — "are we container-bound or API-bound" is the first
question when a run is slower than expected, and it should not require a guess.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LaneStats:
    """Utilisation for one lane, sampled continuously."""

    capacity: int
    in_use: int = 0
    peak: int = 0
    waits: int = 0
    total_wait_ms: float = 0.0
    _busy_since: float | None = None
    _busy_ms: float = 0.0
    _created: float = field(default_factory=time.monotonic)

    @property
    def utilisation(self) -> float:
        """Fraction of wall-clock this lane spent fully saturated.

        Saturation, not average occupancy: a lane that is 60% occupied is fine,
        a lane that is *full* 60% of the time is the bottleneck.
        """
        elapsed = (time.monotonic() - self._created) * 1000
        busy = self._busy_ms + (
            (time.monotonic() - self._busy_since) * 1000 if self._busy_since else 0.0
        )
        return min(1.0, busy / elapsed) if elapsed > 0 else 0.0

    @property
    def mean_wait_ms(self) -> float:
        return self.total_wait_ms / self.waits if self.waits else 0.0

    def acquired(self, wait_ms: float) -> None:
        self.in_use += 1
        self.peak = max(self.peak, self.in_use)
        if wait_ms > 0:
            self.waits += 1
            self.total_wait_ms += wait_ms
        if self.in_use >= self.capacity and self._busy_since is None:
            self._busy_since = time.monotonic()

    def released(self) -> None:
        self.in_use = max(0, self.in_use - 1)
        if self.in_use < self.capacity and self._busy_since is not None:
            self._busy_ms += (time.monotonic() - self._busy_since) * 1000
            self._busy_since = None

    def to_json(self) -> dict[str, float | int]:
        return {
            "capacity": self.capacity,
            "in_use": self.in_use,
            "peak": self.peak,
            "utilisation": round(self.utilisation, 4),
            "mean_wait_ms": round(self.mean_wait_ms, 1),
            "waits": self.waits,
        }


class ConcurrencyBudget:
    """Independent semaphores per bottleneck, each individually observable."""

    def __init__(
        self,
        *,
        max_containers: int,
        max_docker_ops: int,
        max_verifiers: int,
    ) -> None:
        #: Containers alive at once on this worker. ~= cores / task.cpus - 1.
        self.containers = asyncio.Semaphore(max_containers)
        #: The daemon serialises creates badly past ~4 concurrent; this stops
        #: us from queueing work inside Docker where we cannot see it.
        self.docker_ops = asyncio.Semaphore(max_docker_ops)
        #: Verifiers are short and CPU-hungry. Their own lane keeps a burst of
        #: verifies from starving agent tool execution.
        self.verifiers = asyncio.Semaphore(max_verifiers)

        self.stats: dict[str, LaneStats] = {
            "containers": LaneStats(max_containers),
            "docker_ops": LaneStats(max_docker_ops),
            "verifiers": LaneStats(max_verifiers),
        }

    @asynccontextmanager
    async def lane(self, name: str) -> AsyncIterator[None]:
        semaphore = getattr(self, name)
        stats = self.stats[name]
        started = time.monotonic()
        await semaphore.acquire()
        stats.acquired((time.monotonic() - started) * 1000)
        try:
            yield
        finally:
            stats.released()
            semaphore.release()

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        return {name: stats.to_json() for name, stats in self.stats.items()}

    def bottleneck(self) -> str | None:
        """The lane that is saturated most of the time, if any is.

        This is the answer to "why is my run slow", and the UI's worker panel
        shows it directly rather than making you read four gauges.
        """
        saturated = {
            name: stats.utilisation for name, stats in self.stats.items() if stats.utilisation > 0.5
        }
        if not saturated:
            return None
        return max(saturated, key=lambda k: saturated[k])

    @property
    def active_containers(self) -> int:
        return self.stats["containers"].in_use


def budget_from_settings(worker_settings: object) -> ConcurrencyBudget:
    return ConcurrencyBudget(
        max_containers=int(getattr(worker_settings, "max_containers", 8)),
        max_docker_ops=int(getattr(worker_settings, "max_docker_ops", 4)),
        max_verifiers=int(getattr(worker_settings, "max_verifiers", 4)),
    )


def recommended_containers(cores: int, task_cpus: float) -> int:
    """``(cores / task.cpus) - 1``.

    The minus one leaves headroom for the worker process itself, the Docker
    daemon and the verifier lane. Without it, the host's own scheduler becomes
    a source of trial timeouts, which then look like model failures.
    """
    if task_cpus <= 0:
        return 1
    return max(1, int(cores / task_cpus) - 1)
