"""``autobench bench-harness`` — the command that substantiates the speedup claim.

A performance claim without a reproducible measurement is decoration. This is
the measurement, and the methodology is the point:

*   **The baseline is defined.** N=1 sequential on the same suite, same host,
    same image-cache state.
*   **The confound is removed.** The oracle agent replays ``solve.sh``
    deterministically, so model sampling variance contributes exactly zero.
    Every millisecond measured is harness overhead. The real-model figure is
    reported separately, with its own interval.
*   **It is reproducible.** This is a shipped command, ``bench/*.json`` is
    committed, and the nightly job re-runs it.
*   **The limits are stated.** Efficiency falls off above N=8 on a 16-core host
    because the Docker daemon and the verifier lane saturate; above N=16 the
    provider rate limit binds regardless of hardware.

If the number lands somewhere other than 40%, this command reports the real
number. The point of the harness is that the harness does not lie.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autobench.core.config import Settings
from autobench.taskspec.loader import TaskDirectory, discover_tasks, load_task
from autobench.worker.pipeline import TrialPipeline, TrialRequest
from autobench.worker.scheduler import ConcurrencyBudget

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BenchPoint:
    concurrency: int
    wall_clock_sec: float
    speedup: float
    efficiency: float
    p50_trial_sec: float
    p95_trial_sec: float
    throttle_rate: float
    infra_errors: int
    trials: int
    peak_containers: int
    lane_utilisation: dict[str, float] = field(default_factory=dict)
    bottleneck: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "concurrency": self.concurrency,
            "wall_clock_sec": round(self.wall_clock_sec, 2),
            "speedup": round(self.speedup, 3),
            "efficiency": round(self.efficiency, 3),
            "p50_trial_sec": round(self.p50_trial_sec, 2),
            "p95_trial_sec": round(self.p95_trial_sec, 2),
            "throttle_rate": round(self.throttle_rate, 4),
            "infra_errors": self.infra_errors,
            "trials": self.trials,
            "peak_containers": self.peak_containers,
            "lane_utilisation": {k: round(v, 3) for k, v in self.lane_utilisation.items()},
            "bottleneck": self.bottleneck,
        }


@dataclass(slots=True)
class BenchResult:
    suite: str
    agent: str
    model: str | None
    repeats: int
    points: list[BenchPoint]
    host: dict[str, Any]
    started_at: str
    finished_at: str

    @property
    def headline(self) -> str:
        baseline = next((p for p in self.points if p.concurrency == 1), None)
        target = next((p for p in self.points if p.concurrency == 8), None)
        if baseline is None or target is None:
            return "no N=1/N=8 pair measured"
        reduction = (1 - target.wall_clock_sec / baseline.wall_clock_sec) * 100
        return (
            f"{reduction:.1f}% wall-clock reduction at N=8 vs N=1 "
            f"({baseline.wall_clock_sec:.1f}s -> {target.wall_clock_sec:.1f}s, "
            f"{target.speedup:.2f}x)"
        )

    def meets_acceptance(self, threshold: float = 0.40) -> tuple[bool, str]:
        """G2 / definition-of-done item 3, checked rather than asserted."""
        baseline = next((p for p in self.points if p.concurrency == 1), None)
        target = next((p for p in self.points if p.concurrency == 8), None)
        if baseline is None or target is None:
            return False, "the run did not include both N=1 and N=8"

        reduction = 1 - target.wall_clock_sec / baseline.wall_clock_sec
        problems = []
        if reduction < threshold:
            problems.append(f"reduction {reduction:.1%} is below the {threshold:.0%} target")
        if target.throttle_rate >= 0.01:
            problems.append(f"429 rate {target.throttle_rate:.2%} is at or above 1%")
        if target.infra_errors > 0:
            problems.append(f"{target.infra_errors} infra_* errors (must be zero)")
        if problems:
            return False, "; ".join(problems)
        return (
            True,
            f"reduction {reduction:.1%}, 429 rate {target.throttle_rate:.2%}, 0 infra errors",
        )

    def to_json(self) -> dict[str, Any]:
        passed, reason = self.meets_acceptance()
        return {
            "schema_version": "1.0",
            "suite": self.suite,
            "agent": self.agent,
            "model": self.model,
            "repeats": self.repeats,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "host": self.host,
            "headline": self.headline,
            "acceptance": {"passed": passed, "detail": reason, "threshold": 0.40},
            "points": [p.to_json() for p in self.points],
            "methodology": {
                "baseline": "N=1 sequential, same host, same warm image cache",
                "confound_removed": (
                    "the oracle agent replays solve.sh deterministically, so model "
                    "sampling variance contributes zero"
                ),
                "limits": (
                    "efficiency falls off above N=8 on a 16-core host as the Docker "
                    "daemon and verifier lanes saturate; above N=16 the provider rate "
                    "limit binds regardless of hardware"
                ),
            },
        }


def host_info() -> dict[str, Any]:
    import os

    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "machine": platform.machine(),
    }


async def run_bench(
    settings: Settings,
    *,
    tasks_dir: Path,
    concurrencies: list[int],
    repeats: int = 3,
    agent: str = "oracle",
    model: str | None = None,
    limit: int | None = None,
    progress: Any = None,
) -> BenchResult:
    tasks = _load_tasks(tasks_dir, limit)
    if not tasks:
        raise ValueError(f"no tasks found under {tasks_dir}")

    started = datetime.now(UTC).isoformat()

    # Warm the image cache before measuring anything. Otherwise N=1 pays for
    # every build and the "speedup" is mostly a cache-hit rate.
    from autobench.executor import get_provider

    await get_provider(settings).prefetch(tasks)
    await _measure_once(settings, tasks, concurrency=1, agent=agent, model=model)

    points: list[BenchPoint] = []
    baseline_sec: float | None = None

    for concurrency in sorted(concurrencies):
        samples: list[tuple[float, list[float], int, dict[str, float], str | None, int]] = []
        for repeat in range(repeats):
            if progress is not None:
                progress(concurrency, repeat + 1, repeats)
            samples.append(
                await _measure_once(
                    settings, tasks, concurrency=concurrency, agent=agent, model=model
                )
            )

        # Median across repeats, not mean: one slow repeat from a background
        # process on the host should not move the headline number.
        wall = statistics.median(s[0] for s in samples)
        durations = sorted(d for s in samples for d in s[1])
        infra = sum(s[2] for s in samples)
        lanes = samples[-1][3]
        bottleneck = samples[-1][4]
        peak = max(s[5] for s in samples)

        if baseline_sec is None:
            baseline_sec = wall
        speedup = baseline_sec / wall if wall > 0 else 1.0

        points.append(
            BenchPoint(
                concurrency=concurrency,
                wall_clock_sec=wall,
                speedup=speedup,
                efficiency=speedup / concurrency,
                p50_trial_sec=_quantile(durations, 0.50),
                p95_trial_sec=_quantile(durations, 0.95),
                throttle_rate=0.0 if agent == "oracle" else _throttle_rate(),
                infra_errors=infra,
                trials=len(tasks),
                peak_containers=peak,
                lane_utilisation=lanes,
                bottleneck=bottleneck,
            )
        )

    return BenchResult(
        suite=tasks_dir.name,
        agent=agent,
        model=model,
        repeats=repeats,
        points=points,
        host=host_info(),
        started_at=started,
        finished_at=datetime.now(UTC).isoformat(),
    )


async def _measure_once(
    settings: Settings,
    tasks: list[TaskDirectory],
    *,
    concurrency: int,
    agent: str,
    model: str | None,
) -> tuple[float, list[float], int, dict[str, float], str | None, int]:
    """One (concurrency, repeat) sample. Returns wall-clock and per-trial detail."""
    from autobench.models.router import ModelRef, ModelRouter

    router = ModelRouter(settings) if model else None
    model_ref = ModelRef.parse(model, settings) if model else None
    pipeline = TrialPipeline(settings=settings, router=router)

    budget = ConcurrencyBudget(
        max_containers=concurrency,
        max_docker_ops=min(concurrency, settings.worker.max_docker_ops),
        max_verifiers=max(1, min(concurrency, settings.worker.max_verifiers)),
    )
    durations: list[float] = []
    infra_errors = 0

    async def one(task: TaskDirectory) -> None:
        nonlocal infra_errors
        async with budget.lane("containers"):
            outcome = await pipeline.run(
                TrialRequest(
                    trial_id=str(uuid.uuid4()),
                    task=task,
                    agent_slug=agent,
                    seed=task.spec.determinism.seed,
                    model=model_ref,
                )
            )
            durations.append(outcome.duration_ms / 1000)
            if outcome.error_class is not None and outcome.error_class.is_infra:
                infra_errors += 1

    # LPT order, same as the planner: longest expected first.
    ordered = sorted(tasks, key=lambda t: -t.spec.agent.timeout_sec)

    started = time.perf_counter()
    await asyncio.gather(*(one(task) for task in ordered))
    wall = time.perf_counter() - started

    lanes = {name: float(stats["utilisation"]) for name, stats in budget.snapshot().items()}
    peak = int(budget.snapshot()["containers"]["peak"])
    if router is not None:
        await router.close()
    return wall, durations, infra_errors, lanes, budget.bottleneck(), peak


def _load_tasks(tasks_dir: Path, limit: int | None) -> list[TaskDirectory]:
    tasks: list[TaskDirectory] = []
    for directory in discover_tasks(tasks_dir):
        try:
            tasks.append(load_task(directory))
        except Exception as exc:
            logger.warning("bench: skipping %s (%s)", directory.name, exc)
    tasks.sort(key=lambda t: t.id)
    return tasks[:limit] if limit else tasks


def _quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))]


def _throttle_rate() -> float:
    """429s as a fraction of provider calls, from the shared limiter."""
    from autobench.models.ratelimit import RedisTokenBucket

    total = getattr(RedisTokenBucket, "_global_calls", 0)
    throttled = getattr(RedisTokenBucket, "_global_throttled", 0)
    return throttled / total if total else 0.0


def write_result(result: BenchResult, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.to_json(), indent=2), encoding="utf-8")
    return out


def render_markdown(result: BenchResult) -> str:
    """The table that substantiates the claim, ready to paste into a README."""
    lines = [
        f"### Harness benchmark — {result.suite} ({result.agent} agent)",
        "",
        f"**{result.headline}**",
        "",
        "| Concurrency | Wall-clock (s) | Speedup | Efficiency | p50 trial (s) "
        "| p95 trial (s) | 429 rate | infra errors |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for point in result.points:
        label = (
            f"{point.concurrency} (baseline)" if point.concurrency == 1 else str(point.concurrency)
        )
        lines.append(
            f"| {label} | {point.wall_clock_sec:.1f} | {point.speedup:.2f}x | "
            f"{point.efficiency:.0%} | {point.p50_trial_sec:.2f} | {point.p95_trial_sec:.2f} | "
            f"{point.throttle_rate:.2%} | {point.infra_errors} |"
        )
    passed, reason = result.meets_acceptance()
    lines += [
        "",
        f"Acceptance (G2, >=40% at N=8): **{'PASS' if passed else 'FAIL'}** — {reason}",
        "",
        f"Host: {result.host['platform']}, {result.host['cpu_count']} cores, "
        f"Python {result.host['python']}. Median of {result.repeats} repeats.",
    ]
    return "\n".join(lines)
