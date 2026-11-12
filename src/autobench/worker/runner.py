"""The worker loop: lease, run inside the budget, persist, repeat.

This is where the three-tier budget, the lease protocol and the pipeline meet.
The shape is deliberately boring — an ``asyncio.gather`` over a
semaphore-wrapped coroutine (optimisation O3) — because the interesting part is
not the fan-out, it is that the lanes are separate and the lease is renewed
from inside the work.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autobench import __version__
from autobench.core.config import Settings, get_settings
from autobench.core.errors import ErrorClass
from autobench.core.ids import new_uuid, utcnow
from autobench.db.models import (
    Artifact,
    EventKind,
    Run,
    RunStatus,
    Score,
    Trial,
    TrialEvent,
    TrialStatus,
    Worker,
)
from autobench.db.session import session_scope
from autobench.models.router import ModelRef, ModelRouter
from autobench.taskspec.loader import TaskDirectory, load_task
from autobench.worker.pipeline import TrialOutcome, TrialPipeline, TrialRequest
from autobench.worker.queue import LeasedTrial, lease_trials, renew_lease
from autobench.worker.scheduler import ConcurrencyBudget, budget_from_settings

logger = logging.getLogger(__name__)

#: Renew no more often than this, however chatty the agent is.
LEASE_RENEW_THROTTLE_SEC = 10.0


@dataclass(slots=True)
class WorkerStats:
    leased: int = 0
    completed: int = 0
    infra_errors: int = 0
    agent_failures: int = 0
    passes: int = 0


class TrialWorker:
    """One worker process. N of these share a queue and a provider budget."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.id: UUID = new_uuid()
        self.budget: ConcurrencyBudget = budget_from_settings(self.settings.worker)
        self.router = ModelRouter(self.settings)
        self.pipeline = TrialPipeline(settings=self.settings, router=self.router)
        self.stats = WorkerStats()
        self._running = False
        self._task_cache: dict[str, TaskDirectory] = {}
        self._cancelled_runs: set[UUID] = set()
        self._last_renew: dict[UUID, float] = {}

    # --- lifecycle ---------------------------------------------------------

    async def register(self) -> None:
        async with session_scope(self.settings) as session:
            session.add(
                Worker(
                    id=self.id,
                    hostname=socket.gethostname(),
                    pid=os.getpid(),
                    version=__version__,
                    executor=self.settings.executor,
                    lanes=self.budget.snapshot(),
                )
            )
        logger.info(
            "worker %s registered (executor=%s, lanes=%s)",
            str(self.id)[:8],
            self.settings.executor,
            {k: v["capacity"] for k, v in self.budget.snapshot().items()},
        )

    async def heartbeat(self) -> None:
        """Liveness plus lane utilisation. Feeds ``WorkerStale`` and the UI."""
        while self._running:
            try:
                async with session_scope(self.settings) as session:
                    await session.execute(
                        update(Worker)
                        .where(Worker.id == self.id)
                        .values(
                            last_heartbeat=utcnow(),
                            lanes=self.budget.snapshot(),
                            active_trials=self.budget.active_containers,
                        )
                    )
            except Exception as exc:  # pragma: no cover
                logger.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(self.settings.worker.heartbeat_interval_sec)

    async def shutdown(self) -> None:
        self._running = False
        async with session_scope(self.settings) as session:
            await session.execute(
                update(Worker).where(Worker.id == self.id).values(stopped_at=utcnow())
            )
        await self.router.close()
        from autobench.executor import close_provider

        await close_provider()

    # --- main loop ---------------------------------------------------------

    async def run_forever(self, *, run_id: UUID | None = None, drain: bool = False) -> WorkerStats:
        """Poll, lease, execute. ``drain=True`` exits when the queue empties."""
        self._running = True
        await self.register()
        heartbeat = asyncio.create_task(self.heartbeat())
        in_flight: set[asyncio.Task[None]] = set()

        try:
            idle_polls = 0
            while self._running:
                capacity = self.settings.worker.max_containers - len(in_flight)
                if capacity > 0:
                    leased = await self._lease(
                        min(capacity, self.settings.worker.lease_batch), run_id
                    )
                    if leased:
                        idle_polls = 0
                        for item in leased:
                            task = asyncio.create_task(self._execute(item))
                            in_flight.add(task)
                            task.add_done_callback(in_flight.discard)
                    else:
                        idle_polls += 1

                if in_flight:
                    # Wake as soon as anything finishes so a freed lane is
                    # refilled immediately rather than on the next poll tick.
                    await asyncio.wait(
                        in_flight,
                        timeout=self.settings.worker.poll_interval_sec,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                else:
                    if drain and idle_polls >= 2:
                        break
                    await asyncio.sleep(self.settings.worker.poll_interval_sec)

            if in_flight:
                await asyncio.gather(*in_flight, return_exceptions=True)
        finally:
            self._running = False
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
        return self.stats

    async def _lease(self, limit: int, run_id: UUID | None) -> list[LeasedTrial]:
        async with session_scope(self.settings) as session:
            leased = await lease_trials(
                session,
                self.id,
                limit=limit,
                lease_seconds=self._lease_seconds(),
                run_id=run_id,
            )
        self.stats.leased += len(leased)
        return leased

    def _lease_seconds(self) -> int:
        return int(
            self.settings.sandbox.max_agent_timeout_sec * self.settings.worker.lease_multiplier
        )

    # --- one trial ---------------------------------------------------------

    async def _execute(self, leased: LeasedTrial) -> None:
        async with self.budget.lane("containers"):
            try:
                await self._execute_inner(leased)
            except Exception as exc:  # pragma: no cover - last line of defence
                logger.exception("trial %s crashed the worker loop", leased.trial_id)
                await self._persist_failure(leased, exc)

    async def _execute_inner(self, leased: LeasedTrial) -> None:
        async with session_scope(self.settings) as session:
            context = await self._load_context(session, leased)
            last_seq = await session.scalar(
                select(func.max(TrialEvent.seq)).where(TrialEvent.trial_id == leased.trial_id)
            )
            event_offset = int(last_seq if last_seq is not None else -1) + 1
        if context is None:
            return
        task, model_ref, run = context

        started = time.perf_counter()

        async def on_status(status: TrialStatus) -> None:
            async with session_scope(self.settings) as session:
                result = await session.execute(
                    update(Trial)
                    .where(
                        Trial.id == leased.trial_id,
                        Trial.worker_id == self.id,
                        Trial.status.in_(["leased", "setup", "agent", "verify"]),
                        Trial.run_id.in_(
                            select(Run.id).where(Run.status.in_(["queued", "running"]))
                        ),
                    )
                    .values(status=status.value)
                    .returning(Trial.id)
                )
                if result.scalar_one_or_none() is None:
                    self._cancelled_runs.add(leased.run_id)

        async def on_event(kind: EventKind, payload: dict[str, Any], seq: int) -> None:
            async with session_scope(self.settings) as session:
                owned = await session.scalar(
                    select(Trial.id)
                    .where(
                        Trial.id == leased.trial_id,
                        Trial.worker_id == self.id,
                        Trial.status.in_(["leased", "setup", "agent", "verify"]),
                    )
                    .with_for_update()
                )
                if owned is None:
                    self._cancelled_runs.add(leased.run_id)
                    return
                session.add(
                    TrialEvent(
                        trial_id=leased.trial_id,
                        seq=event_offset + seq,
                        kind=kind.value,
                        payload=payload,
                        elapsed_ms=int((time.perf_counter() - started) * 1000),
                    )
                )

        async def renew() -> None:
            # Throttled: a chatty agent emits hundreds of events per minute and
            # each renewal is a write.
            async with session_scope(self.settings) as session:
                run_status = await session.scalar(select(Run.status).where(Run.id == leased.run_id))
                if run_status not in {"queued", "running"}:
                    self._cancelled_runs.add(leased.run_id)
                    return
            last = self._last_renew.get(leased.trial_id, 0.0)
            if time.monotonic() - last < LEASE_RENEW_THROTTLE_SEC:
                return
            self._last_renew[leased.trial_id] = time.monotonic()
            async with session_scope(self.settings) as session:
                still_ours = await renew_lease(
                    session, leased.trial_id, self.id, lease_seconds=self._lease_seconds()
                )
            if not still_ours:
                self._cancelled_runs.add(leased.run_id)

        def should_cancel() -> bool:
            return leased.run_id in self._cancelled_runs

        request = TrialRequest(
            trial_id=str(leased.trial_id),
            task=task,
            agent_slug=run["agent_adapter"],
            seed=leased.seed,
            model=model_ref,
            agent_config=run["agent_config"],
            run_id=str(leased.run_id),
            attempt_index=leased.attempt_index,
        )

        outcome = await self.pipeline.run(
            request,
            on_status=on_status,
            on_event=on_event,
            should_cancel=should_cancel,
            renew_lease=renew,
        )
        await self._persist(leased, outcome)

    async def _load_context(
        self, session: AsyncSession, leased: LeasedTrial
    ) -> tuple[TaskDirectory, ModelRef | None, dict[str, Any]] | None:
        from autobench.db.models import Agent as AgentRow
        from autobench.db.models import Model as ModelRow
        from autobench.db.models import TaskVersion

        version = await session.get(TaskVersion, leased.task_version_id)
        run = await session.get(Run, leased.run_id)
        if version is None or run is None:
            logger.error("trial %s references a missing run or task version", leased.trial_id)
            return None

        agent_row = await session.get(AgentRow, run.agent_id)
        model_row = await session.get(ModelRow, run.model_id)
        if agent_row is None:
            logger.error("run %s references a missing agent", run.id)
            return None

        source = version.source_path
        if not source:
            logger.error("task version %s has no source path on this worker", version.id)
            return None

        cache_key = f"{version.id}"
        task = self._task_cache.get(cache_key)
        if task is None:
            task = load_task(source)
            self._task_cache[cache_key] = task

        model_ref: ModelRef | None = None
        if model_row is not None:
            model_ref = ModelRef(
                provider=model_row.provider,
                model_id=model_row.model_id,
                slug=model_row.slug,
                api_key=self.settings.provider_key(model_row.provider),
                base_url=(
                    self.settings.openai_compat_base_url
                    if model_row.provider == "openai_compat"
                    else None
                ),
            )

        return (
            task,
            model_ref,
            {
                "agent_adapter": agent_row.adapter,
                "agent_config": dict(agent_row.config or {}),
            },
        )

    # --- persistence -------------------------------------------------------

    async def _persist(self, leased: LeasedTrial, outcome: TrialOutcome) -> None:
        async with session_scope(self.settings) as session:
            trial = await session.scalar(
                select(Trial)
                .where(
                    Trial.id == leased.trial_id,
                    Trial.worker_id == self.id,
                    Trial.status.in_(["leased", "setup", "agent", "verify"]),
                )
                .with_for_update()
            )
            if trial is None:
                return

            infra = outcome.error_class is not None and outcome.error_class.is_infra

            if infra and trial.attempt_count + 1 < self.settings.worker.max_infra_attempts:
                # Infrastructure failure with retries left: back into the queue,
                # and nothing is written to `score`. It contributed no measurement.
                trial.status = TrialStatus.QUEUED.value
                trial.attempt_count += 1
                trial.worker_id = None
                trial.lease_expires_at = None
                trial.queued_at = utcnow()
                trial.error_class = outcome.error_class.value if outcome.error_class else None
                trial.error_detail = outcome.error_detail
                self.stats.infra_errors += 1
                await session.commit()
                logger.info(
                    "trial %s re-queued after %s (attempt %d)",
                    str(leased.trial_id)[:8],
                    outcome.error_class.value if outcome.error_class else "?",
                    trial.attempt_count,
                )
                return

            trial.status = outcome.status.value
            trial.container_id = outcome.container_id
            trial.finished_at = utcnow()
            trial.duration_ms = outcome.duration_ms
            trial.lease_expires_at = None
            trial.error_class = outcome.error_class.value if outcome.error_class else None
            trial.error_detail = outcome.error_detail

            if infra:
                self.stats.infra_errors += 1
            else:
                # Only non-infra outcomes produce a score row. That is the whole
                # retry rule, expressed as a database constraint rather than a
                # convention: an infra failure literally cannot contribute to
                # pass@k because it has no score.
                session.add(
                    Score(
                        trial_id=trial.id,
                        reward=outcome.report.reward,
                        binary_pass=outcome.report.binary_pass,
                        items={
                            "items": [i.model_dump() for i in outcome.report.items],
                            "error": outcome.report.error,
                        },
                        verifier_exit=None,
                        tool_calls=outcome.tool_calls,
                        input_tokens=outcome.usage.input_tokens,
                        output_tokens=outcome.usage.output_tokens,
                        cached_tokens=outcome.usage.cached_tokens,
                        cost_usd=outcome.cost_usd,
                        setup_ms=outcome.setup_ms,
                        agent_ms=outcome.agent_ms,
                        verify_ms=outcome.verify_ms,
                    )
                )
                self.stats.completed += 1
                if outcome.report.binary_pass:
                    self.stats.passes += 1
                elif outcome.error_class is not None:
                    self.stats.agent_failures += 1

            for kind, key in outcome.artifacts.items():
                session.add(
                    Artifact(
                        trial_id=trial.id,
                        kind=kind,
                        storage_key=key,
                        content_type="application/json",
                    )
                )

            # Atomic increment, not read-modify-write.
            #
            # Several workers finish trials of the same run concurrently, each
            # in its own session. `run.trials_done += 1` on an ORM object reads
            # a value, adds one and writes it back, so two overlapping commits
            # lose an increment — the run then sits at 32/35 forever and never
            # reaches `completed`. Pushing the arithmetic into SQL lets the
            # database serialise it.
            await session.execute(
                update(Run)
                .where(Run.id == leased.run_id)
                .values(
                    trials_done=Run.trials_done + 1,
                    cost_usd=Run.cost_usd + outcome.cost_usd,
                    status=case(
                        (Run.status == RunStatus.QUEUED.value, RunStatus.RUNNING.value),
                        else_=Run.status,
                    ),
                    started_at=func.coalesce(Run.started_at, utcnow()),
                )
            )
            from autobench.api.events import append_transition

            await append_transition(
                session,
                str(leased.run_id),
                {
                    "event": "trial_update",
                    "trial_id": str(leased.trial_id),
                    "status": outcome.status.value,
                    "reward": outcome.report.reward,
                    "binary_pass": outcome.report.binary_pass,
                    "error_class": outcome.error_class.value if outcome.error_class else None,
                    "duration_ms": outcome.duration_ms,
                    "cost_usd": round(outcome.cost_usd, 6),
                },
            )
            await session.commit()

            # Re-read the authoritative counters to decide completion.
            run = await session.get(Run, leased.run_id, populate_existing=True)
            if run is not None:
                if run.trials_done >= run.trials_total and run.status == RunStatus.RUNNING.value:
                    run.status = RunStatus.COMPLETED.value
                    run.finished_at = utcnow()
                if run.budget_usd and float(run.cost_usd) > float(run.budget_usd):
                    # Cost runaway: stop the run rather than discover it on the
                    # invoice. R6 in the risk register.
                    logger.warning(
                        "run %s exceeded its $%.2f budget; cancelling",
                        run.id,
                        float(run.budget_usd),
                    )
                    self._cancelled_runs.add(run.id)
                    from autobench.worker.queue import cancel_run_trials

                    await cancel_run_trials(session, run.id)
                    run.status = RunStatus.CANCELLED.value
                    run.finished_at = utcnow()

            await session.commit()

        await _publish_progress(self.settings, leased.run_id, leased.trial_id, outcome)

    async def _persist_failure(self, leased: LeasedTrial, exc: Exception) -> None:
        async with session_scope(self.settings) as session:
            trial = await session.scalar(
                select(Trial)
                .where(
                    Trial.id == leased.trial_id,
                    Trial.worker_id == self.id,
                    Trial.status.in_(["leased", "setup", "agent", "verify"]),
                )
                .with_for_update()
            )
            if trial is None:
                return
            trial.status = TrialStatus.ERROR.value
            trial.error_class = ErrorClass.INFRA_DOCKER.value
            trial.error_detail = f"{type(exc).__name__}: {exc}"
            trial.finished_at = utcnow()
            trial.lease_expires_at = None
            await session.execute(
                update(Run).where(Run.id == leased.run_id).values(trials_done=Run.trials_done + 1)
            )
            await session.flush()
            run = await session.get(Run, leased.run_id, populate_existing=True)
            if run and run.status in {"queued", "running"} and run.trials_done >= run.trials_total:
                run.status = RunStatus.COMPLETED.value
                run.finished_at = utcnow()
            await session.commit()


async def _publish_progress(
    settings: Settings, run_id: UUID, trial_id: UUID, outcome: TrialOutcome
) -> None:
    """Push a trial transition onto the pub/sub channel the SSE endpoint reads."""
    from autobench.api.events import publish

    await publish(
        settings,
        str(run_id),
        {
            "event": "trial_update",
            "trial_id": str(trial_id),
            "status": outcome.status.value,
            "reward": outcome.report.reward,
            "binary_pass": outcome.report.binary_pass,
            "error_class": outcome.error_class.value if outcome.error_class else None,
            "duration_ms": outcome.duration_ms,
            "cost_usd": round(outcome.cost_usd, 6),
        },
        persist=False,
    )
