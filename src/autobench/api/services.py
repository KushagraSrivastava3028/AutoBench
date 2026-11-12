"""Query logic shared by the routers.

Kept out of the route handlers so the same aggregation feeds the REST API, the
CLI and the regression gate without three subtly different definitions of
"pass@1" drifting apart.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Integer, Select, and_, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from autobench.core.config import Settings
from autobench.core.errors import ValidationFailure
from autobench.core.ids import utcnow
from autobench.db.models import (
    Agent,
    Model,
    Run,
    RunStatus,
    Score,
    Suite,
    SuiteMember,
    Task,
    TaskVersion,
    Trial,
    TrialStatus,
    Worker,
)
from autobench.scoring.aggregate import (
    Interval,
    RunAggregate,
    TaskOutcome,
    aggregate_run,
    compare_runs,
)

logger = logging.getLogger(__name__)

WORKER_STALE_SEC = 120


# ---------------------------------------------------------------------------
# outcomes
# ---------------------------------------------------------------------------


async def run_outcomes(session: AsyncSession, run_id: UUID) -> list[TaskOutcome]:
    """Per-task attempts, passes and rewards for one run.

    Infra errors are counted but excluded from ``attempts``. A trial that never
    produced a measurement cannot be in the denominator of pass@1 — including
    it would mean a flaky Docker daemon lowers a model's score.
    """
    rows = (
        await session.execute(
            select(
                TaskVersion.task_id,
                Trial.status,
                Trial.error_class,
                Score.binary_pass,
                Score.reward,
            )
            .join(TaskVersion, TaskVersion.id == Trial.task_version_id)
            .outerjoin(Score, Score.trial_id == Trial.id)
            .where(Trial.run_id == run_id)
        )
    ).all()

    grouped: dict[str, TaskOutcome] = {}
    for task_id, status, error_class, binary_pass, reward in rows:
        outcome = grouped.setdefault(task_id, TaskOutcome(task_id=task_id, attempts=0, passes=0))
        is_infra = bool(
            error_class and (error_class.startswith("infra_") or error_class == "verifier_crash")
        )
        if is_infra:
            outcome.infra_errors += 1
            continue
        if status in {TrialStatus.QUEUED.value, TrialStatus.CANCELLED.value} or (
            status
            in {
                TrialStatus.LEASED.value,
                TrialStatus.SETUP.value,
                TrialStatus.AGENT.value,
                TrialStatus.VERIFY.value,
            }
        ):
            continue
        outcome.attempts += 1
        if binary_pass:
            outcome.passes += 1
        outcome.rewards.append(float(reward) if reward is not None else 0.0)
    return sorted(grouped.values(), key=lambda o: o.task_id)


async def aggregate_for_run(session: AsyncSession, run: Run, settings: Settings) -> RunAggregate:
    outcomes = await run_outcomes(session, run.id)
    return aggregate_run(
        outcomes,
        run.k,
        confidence=settings.confidence_level,
        # Fewer resamples for a live run: the interval is redrawn every few
        # seconds and a 10,000-resample BCa on every poll is wasted CPU.
        resamples=2_000 if run.status == RunStatus.RUNNING.value else settings.bootstrap_resamples,
        seed=run.seed,
        reportable_min_k=settings.reportable_min_k,
    )


def interval_json(interval: Interval) -> dict[str, Any]:
    return interval.to_json()


# ---------------------------------------------------------------------------
# matrix
# ---------------------------------------------------------------------------


async def run_matrix(session: AsyncSession, run: Run) -> dict[str, Any]:
    """The task x attempt grid. One query, ordered so the UI never sorts."""
    rows = (
        await session.execute(
            select(Trial, Score, TaskVersion, Task)
            .join(TaskVersion, TaskVersion.id == Trial.task_version_id)
            .join(Task, Task.id == TaskVersion.task_id)
            .outerjoin(Score, Score.trial_id == Trial.id)
            .where(Trial.run_id == run.id)
            .order_by(Task.id, Trial.attempt_index)
        )
    ).all()

    grouped: dict[str, dict[str, Any]] = {}
    for trial, score, version, task in rows:
        entry = grouped.setdefault(
            task.id,
            {
                "task_id": task.id,
                "task_version_id": version.id,
                "title": task.title,
                "difficulty": task.difficulty,
                "cells": [],
                "passes": 0,
                "attempts": 0,
            },
        )
        entry["cells"].append(
            {
                "trial_id": trial.id,
                "attempt": trial.attempt_index,
                "status": trial.status,
                "reward": float(score.reward) if score else None,
                "binary_pass": bool(score.binary_pass) if score else None,
                "error_class": trial.error_class,
                "duration_ms": trial.duration_ms,
                "attempt_count": trial.attempt_count,
                "cost_usd": float(score.cost_usd) if score and score.cost_usd is not None else None,
                "finished_at": trial.finished_at,
            }
        )
        if score is not None:
            entry["attempts"] += 1
            if score.binary_pass:
                entry["passes"] += 1

    return {
        "run_id": run.id,
        "k": run.k,
        # Hardest first: the rows a human wants to look at are the failing ones.
        "rows": sorted(
            grouped.values(), key=lambda r: (r["passes"] / max(r["attempts"], 1), r["task_id"])
        ),
    }


async def error_breakdown(session: AsyncSession, run_id: UUID) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Trial.error_class, func.count())
            .where(and_(Trial.run_id == run_id, Trial.error_class.is_not(None)))
            .group_by(Trial.error_class)
        )
    ).all()
    return {str(cls): int(count) for cls, count in rows}


# ---------------------------------------------------------------------------
# run enrichment
# ---------------------------------------------------------------------------


def with_names(run: Run) -> dict[str, Any]:
    """Slugs alongside ids, so the UI never needs a second round trip."""
    return {
        "suite_slug": run.suite.slug if run.suite else "",
        "agent_slug": run.agent.slug if run.agent else "",
        "model_slug": run.model.slug if run.model else "",
    }


def run_select() -> Select[tuple[Run]]:
    return select(Run).options(
        selectinload(Run.suite), selectinload(Run.agent), selectinload(Run.model)
    )


def estimate_eta(run: Run) -> tuple[float | None, float | None]:
    """``(elapsed_sec, eta_sec)`` from observed throughput.

    Linear extrapolation from completed trials. It is wrong early in a run and
    increasingly right later, which is the correct shape for a progress
    estimate — and it is labelled "~" in the UI for exactly that reason.
    """
    if run.started_at is None:
        return None, None
    end = run.finished_at or utcnow()
    elapsed = (end - run.started_at).total_seconds()
    if run.trials_done <= 0 or run.trials_done >= run.trials_total:
        return elapsed, 0.0 if run.trials_done >= run.trials_total else None
    per_trial = elapsed / run.trials_done
    remaining = run.trials_total - run.trials_done
    return elapsed, round(per_trial * remaining, 1)


# ---------------------------------------------------------------------------
# leaderboard and comparison
# ---------------------------------------------------------------------------


async def leaderboard(
    session: AsyncSession, suite_id: UUID, settings: Settings, limit: int = 50
) -> list[dict[str, Any]]:
    """Best run per (agent, model) on one suite, with intervals."""
    runs = (
        (
            await session.execute(
                run_select()
                .where(and_(Run.suite_id == suite_id, Run.status == RunStatus.COMPLETED.value))
                .order_by(Run.finished_at.desc())
                .limit(limit * 4)
            )
        )
        .scalars()
        .all()
    )

    best: dict[tuple[str, str], dict[str, Any]] = {}
    for run in runs:
        aggregate = await aggregate_for_run(session, run, settings)
        if aggregate.infra_errors / max(run.trials_total, 1) > 0.02 or not aggregate.reportable:
            continue
        latency = await p95_latency(session, run.id)
        solves = aggregate.total_passes
        key = (run.agent.slug if run.agent else "?", run.model.slug if run.model else "?")
        entry: dict[str, Any] = {
            "run_id": run.id,
            "agent_slug": key[0],
            "model_slug": key[1],
            "label": run.label,
            "k": run.k,
            "pass_at_1": aggregate.pass_at_1.to_json(),
            "pass_caret_k": aggregate.pass_caret_k.to_json(),
            "reliability_gap": round(aggregate.reliability_gap, 4),
            "cost_usd": float(run.cost_usd or 0),
            "cost_per_solve": (float(run.cost_usd or 0) / solves) if solves else None,
            "p95_latency_sec": latency,
            "finished_at": run.finished_at,
            "reportable": aggregate.reportable,
        }
        current = best.get(key)
        if current is None or entry["pass_at_1"]["point"] > current["pass_at_1"]["point"]:
            best[key] = entry

    return sorted(best.values(), key=lambda e: e["pass_at_1"]["point"], reverse=True)[:limit]


async def p95_latency(session: AsyncSession, run_id: UUID) -> float | None:
    durations = (
        (
            await session.execute(
                select(Trial.duration_ms).where(
                    and_(Trial.run_id == run_id, Trial.duration_ms.is_not(None))
                )
            )
        )
        .scalars()
        .all()
    )
    if not durations:
        return None
    ordered = sorted(int(d) for d in durations if d is not None)
    index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return round(ordered[index] / 1000, 2)


async def compare(
    session: AsyncSession, run_a: Run, run_b: Run, settings: Settings
) -> dict[str, Any]:
    baseline = await run_outcomes(session, run_a.id)
    candidate = await run_outcomes(session, run_b.id)
    for run, outcomes in ((run_a, baseline), (run_b, candidate)):
        if sum(task.infra_errors for task in outcomes) / max(run.trials_total, 1) > 0.02:
            raise ValidationFailure(
                "This comparison includes a run invalidated by harness errors. "
                "Resolve its infrastructure failures and rerun it first."
            )
    versions = (
        await session.execute(
            select(Trial.run_id, TaskVersion.task_id, TaskVersion.id)
            .join(TaskVersion, TaskVersion.id == Trial.task_version_id)
            .where(Trial.run_id.in_([run_a.id, run_b.id]))
            .distinct()
        )
    ).all()
    before_versions = {task: version for run_id, task, version in versions if run_id == run_a.id}
    after_versions = {task: version for run_id, task, version in versions if run_id == run_b.id}
    shared = {
        task for task, version in before_versions.items() if after_versions.get(task) == version
    }
    before_scored = {task.task_id for task in baseline if task.attempts > 0}
    after_scored = {task.task_id for task in candidate if task.attempts > 0}
    shared &= before_scored & after_scored
    if not shared:
        raise ValidationFailure("Choose runs with scored trials for the same pinned task versions.")
    baseline = [task for task in baseline if task.task_id in shared]
    candidate = [task for task in candidate if task.task_id in shared]
    comparison = compare_runs(
        baseline,
        candidate,
        confidence=settings.confidence_level,
        resamples=settings.bootstrap_resamples,
        seed=run_a.seed,
    )
    result = comparison.to_json()
    result["baseline_metrics"] = aggregate_run(
        baseline,
        run_a.k,
        confidence=settings.confidence_level,
        resamples=settings.bootstrap_resamples,
        seed=run_a.seed,
        reportable_min_k=settings.reportable_min_k,
    ).to_json()
    result["candidate_metrics"] = aggregate_run(
        candidate,
        run_b.k,
        confidence=settings.confidence_level,
        resamples=settings.bootstrap_resamples,
        seed=run_b.seed,
        reportable_min_k=settings.reportable_min_k,
    ).to_json()
    return result


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


async def dashboard(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    now = utcnow()
    week_ago = now - timedelta(days=7)
    fortnight_ago = now - timedelta(days=14)

    runs_this_week = int(
        (
            await session.execute(
                select(func.count()).select_from(Run).where(Run.created_at >= week_ago)
            )
        ).scalar_one()
    )
    runs_prior_week = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Run)
                .where(and_(Run.created_at >= fortnight_ago, Run.created_at < week_ago))
            )
        ).scalar_one()
    )
    delta_pct = (
        ((runs_this_week - runs_prior_week) / runs_prior_week * 100) if runs_prior_week else 0.0
    )

    trials_total = int(
        (await session.execute(select(func.count()).select_from(Trial))).scalar_one()
    )
    trials_scored = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Trial)
                .where(Trial.status == TrialStatus.SCORED.value)
            )
        ).scalar_one()
    )
    spend = float(
        (
            await session.execute(
                select(func.coalesce(func.sum(Run.cost_usd), 0)).where(Run.created_at >= week_ago)
            )
        ).scalar_one()
        or 0
    )

    recent = (
        (await session.execute(run_select().order_by(Run.created_at.desc()).limit(6)))
        .scalars()
        .all()
    )

    overall = None
    gap = None
    caret = None
    completed = [r for r in recent if r.status == RunStatus.COMPLETED.value]
    if completed:
        latest = completed[0]
        if latest.trials_done:
            aggregate = await aggregate_for_run(session, latest, settings)
            overall = aggregate.pass_at_1.to_json()
            caret = aggregate.pass_caret_k.to_json()
            gap = round(aggregate.reliability_gap, 4)

    workers = (
        (await session.execute(select(Worker).where(Worker.stopped_at.is_(None)))).scalars().all()
    )
    active = [w for w in workers if (now - w.last_heartbeat).total_seconds() < WORKER_STALE_SEC]

    queued = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Trial)
                .where(Trial.status == TrialStatus.QUEUED.value)
            )
        ).scalar_one()
    )
    in_flight = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Trial)
                .where(
                    Trial.status.in_(
                        [
                            TrialStatus.LEASED.value,
                            TrialStatus.SETUP.value,
                            TrialStatus.AGENT.value,
                            TrialStatus.VERIFY.value,
                        ]
                    )
                )
            )
        ).scalar_one()
    )

    latencies = (
        (
            await session.execute(
                select(Trial.duration_ms)
                .where(and_(Trial.duration_ms.is_not(None), Trial.finished_at >= week_ago))
                .order_by(Trial.duration_ms)
            )
        )
        .scalars()
        .all()
    )
    p95 = None
    if latencies:
        ordered = [int(d) for d in latencies if d is not None]
        p95 = round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] / 1000, 2)

    return {
        "runs_this_week": runs_this_week,
        "runs_delta_pct": round(delta_pct, 1),
        "trials_executed": trials_total,
        "completion_pct": round(trials_scored / trials_total * 100, 2) if trials_total else 0.0,
        "overall_pass_at_1": overall,
        "overall_pass_caret_k": caret,
        "reliability_gap": gap,
        "spend_this_week": round(spend, 2),
        "spend_per_trial": round(spend / trials_total, 4) if trials_total else 0.0,
        "p95_latency_sec": p95,
        "throttle_rate": 0.0,
        "workers_active": len(active),
        "workers_total": len(workers),
        "queue_depth": queued,
        "active_trials": in_flight,
        "suites": await suite_health(session, settings),
        "recent_runs": [_run_payload(r) for r in recent],
    }


def _run_payload(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "suite_id": run.suite_id,
        "agent_id": run.agent_id,
        "model_id": run.model_id,
        "k": run.k,
        "concurrency": run.concurrency,
        "status": run.status,
        "seed": run.seed,
        "label": run.label,
        "trials_total": run.trials_total,
        "trials_done": run.trials_done,
        "cost_usd": float(run.cost_usd or 0),
        "budget_usd": float(run.budget_usd) if run.budget_usd else None,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        **with_names(run),
    }


async def suite_health(session: AsyncSession, settings: Settings) -> list[dict[str, Any]]:
    """Per-suite best score and trend — the second row of the dashboard."""
    suites = (await session.execute(select(Suite))).scalars().all()
    out: list[dict[str, Any]] = []

    for suite in suites:
        member_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SuiteMember)
                    .where(SuiteMember.suite_id == suite.id)
                )
            ).scalar_one()
        )
        runs = (
            (
                await session.execute(
                    run_select()
                    .where(and_(Run.suite_id == suite.id, Run.status == RunStatus.COMPLETED.value))
                    .order_by(Run.finished_at.desc())
                    .limit(8)
                )
            )
            .scalars()
            .all()
        )

        trend: list[float] = []
        best: float | None = None
        margin: float | None = None
        sota: str | None = None
        intervals = []
        valid_runs = []
        for run in reversed(runs):
            aggregate = await aggregate_for_run(session, run, settings)
            if aggregate.infra_errors / max(run.trials_total, 1) > 0.02:
                continue
            trend.append(round(aggregate.pass_at_1.point, 4))
            intervals.append(aggregate.pass_at_1)
            valid_runs.append(run)
            if best is None or aggregate.pass_at_1.point > best:
                best = aggregate.pass_at_1.point
                margin = round(aggregate.pass_at_1.margin, 4)
                sota = run.model.slug if run.model else None

        delta = None
        status = "unknown"
        if len(trend) >= 2:
            delta = round(trend[-1] - trend[-2], 4)
            comparable = (valid_runs[-1].agent_id, valid_runs[-1].model_id, valid_runs[-1].k) == (
                valid_runs[-2].agent_id,
                valid_runs[-2].model_id,
                valid_runs[-2].k,
            )
            status = "stable"
            if comparable and not intervals[-1].overlaps(intervals[-2]):
                status = "improving" if delta > 0 else "regression"

        out.append(
            {
                "slug": suite.slug,
                "name": suite.name,
                "task_count": member_count,
                "best_pass_at_1": round(best, 4) if best is not None else None,
                "margin": margin,
                "sota_label": sota,
                "trend": trend,
                "delta": delta,
                "status": status,
            }
        )
    return out


async def worker_health(session: AsyncSession) -> list[dict[str, Any]]:
    now = utcnow()
    workers = (
        (
            await session.execute(
                select(Worker).where(Worker.stopped_at.is_(None)).order_by(Worker.created_at)
            )
        )
        .scalars()
        .all()
    )
    out = []
    for worker in workers:
        lanes = dict(worker.lanes or {})
        saturated = {
            name: lane.get("utilisation", 0)
            for name, lane in lanes.items()
            if isinstance(lane, dict) and lane.get("utilisation", 0) > 0.5
        }
        out.append(
            {
                "id": worker.id,
                "hostname": worker.hostname,
                "pid": worker.pid,
                "version": worker.version,
                "executor": worker.executor,
                "active_trials": worker.active_trials,
                "last_heartbeat": worker.last_heartbeat,
                "stale": (now - worker.last_heartbeat).total_seconds() > WORKER_STALE_SEC,
                "lanes": lanes,
                "bottleneck": max(saturated, key=lambda k: saturated[k]) if saturated else None,
            }
        )
    return out


async def task_history(session: AsyncSession, task_id: str, limit: int = 12) -> list[float]:
    """Recent pass rate per run for one task — the sparkline on the catalogue."""
    rows = (
        await session.execute(
            select(Trial.run_id, func.avg(cast(Score.binary_pass, Integer)))
            .join(TaskVersion, TaskVersion.id == Trial.task_version_id)
            .join(Score, Score.trial_id == Trial.id)
            .where(TaskVersion.task_id == task_id)
            .group_by(Trial.run_id)
            .limit(limit)
        )
    ).all()
    return [round(float(value or 0), 4) for _, value in rows]


async def resolve_suite(session: AsyncSession, reference: str) -> Suite | None:
    try:
        return await session.get(Suite, UUID(reference))
    except (ValueError, AttributeError):
        return (
            await session.execute(select(Suite).where(Suite.slug == reference))
        ).scalar_one_or_none()


async def resolve_agent(session: AsyncSession, reference: str) -> Agent | None:
    try:
        return await session.get(Agent, UUID(reference))
    except (ValueError, AttributeError):
        return (
            await session.execute(select(Agent).where(Agent.slug == reference))
        ).scalar_one_or_none()


async def resolve_model(session: AsyncSession, reference: str) -> Model | None:
    try:
        return await session.get(Model, UUID(reference))
    except (ValueError, AttributeError):
        return (
            await session.execute(select(Model).where(Model.slug == reference))
        ).scalar_one_or_none()


def published_versions(task_ids: Sequence[str] | None = None) -> Select[tuple[TaskVersion]]:
    query = select(TaskVersion).where(TaskVersion.published.is_(True))
    if task_ids:
        query = query.where(TaskVersion.task_id.in_(task_ids))
    return query
