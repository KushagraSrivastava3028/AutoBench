"""Leasing, renewal and reaping — crash recovery without a distributed lock.

The protocol, in full:

1.  A worker **leases** a trial: one ``UPDATE ... WHERE status='queued'`` that
    sets ``status='leased'``, stamps ``worker_id`` and sets ``lease_expires_at``
    to ``now + 2 x agent_timeout``.
2.  The worker **renews** the lease from inside the agent loop. A worker that is
    merely slow — blocked on a five-minute model call — keeps its trial.
3.  A worker that is SIGKILLed renews nothing. Its lease expires.
4.  A **reaper** on the API, every 30 seconds, finds expired leases, increments
    ``attempt_count``, and re-queues — unless the count has reached the limit,
    in which case the trial is marked ``infra_abandoned``.

No distributed lock, no consensus, no durable-workflow engine. The only
critical section is a single-row conditional UPDATE, and Postgres already
serialises that. The cost of this simplicity is bounded double-execution: a
worker that is partitioned rather than dead can finish a trial whose lease has
already been reaped. That is acceptable because a trial is idempotent by
construction — it leaves nothing behind — and the unique constraint on
``(run_id, task_version_id, attempt_index)`` means the second write updates the
same row rather than inventing a duplicate result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from autobench.core.errors import ErrorClass
from autobench.core.ids import utcnow
from autobench.db.models import Run, RunStatus, Trial, TrialStatus

logger = logging.getLogger(__name__)

DEFAULT_LEASE_MULTIPLIER = 2.0
REAPER_INTERVAL_SEC = 30


@dataclass(slots=True)
class LeasedTrial:
    trial_id: UUID
    run_id: UUID
    task_version_id: UUID
    attempt_index: int
    seed: int
    attempt_count: int
    lease_expires_at: Any


async def lease_trials(
    session: AsyncSession,
    worker_id: UUID,
    *,
    limit: int = 4,
    lease_seconds: int = 1800,
    run_id: UUID | None = None,
) -> list[LeasedTrial]:
    """Claim up to ``limit`` queued trials, highest priority first.

    ``with_for_update(skip_locked=True)`` is what makes this safe under
    concurrency: two workers polling simultaneously each get a distinct set of
    rows instead of one blocking on the other, and nobody hands out the same
    trial twice.
    """
    now = utcnow()
    expires = now + timedelta(seconds=lease_seconds)

    conditions = [Trial.status == TrialStatus.QUEUED.value]
    if run_id is not None:
        conditions.append(Trial.run_id == run_id)

    candidates = (
        select(Trial.id)
        .join(Run, Run.id == Trial.run_id)
        .where(and_(*conditions, Run.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value])))
        .order_by(Trial.priority.desc(), Trial.queued_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True, of=Trial)
    )
    trial_ids = list((await session.execute(candidates)).scalars().all())
    if not trial_ids:
        return []

    await session.execute(
        update(Trial)
        .where(Trial.id.in_(trial_ids))
        .values(
            status=TrialStatus.LEASED.value,
            worker_id=worker_id,
            lease_expires_at=expires,
            started_at=func.coalesce(Trial.started_at, now),
        )
    )
    await session.commit()

    rows = (await session.execute(select(Trial).where(Trial.id.in_(trial_ids)))).scalars().all()
    return [
        LeasedTrial(
            trial_id=t.id,
            run_id=t.run_id,
            task_version_id=t.task_version_id,
            attempt_index=t.attempt_index,
            seed=t.seed,
            attempt_count=t.attempt_count,
            lease_expires_at=t.lease_expires_at,
        )
        for t in rows
    ]


async def renew_lease(
    session: AsyncSession, trial_id: UUID, worker_id: UUID, *, lease_seconds: int
) -> bool:
    """Extend the lease. ``False`` means it was reaped — stop working on it.

    The ``worker_id`` guard is the important part: a worker whose lease was
    reaped and re-leased elsewhere learns so here, rather than continuing to
    write results for a trial another worker now owns.
    """
    result = await session.execute(
        update(Trial)
        .where(
            and_(
                Trial.id == trial_id,
                Trial.worker_id == worker_id,
                Trial.status.in_(
                    [
                        TrialStatus.LEASED.value,
                        TrialStatus.SETUP.value,
                        TrialStatus.AGENT.value,
                        TrialStatus.VERIFY.value,
                    ]
                ),
            )
        )
        .values(lease_expires_at=utcnow() + timedelta(seconds=lease_seconds))
        .returning(Trial.id)
    )
    renewed = result.scalar_one_or_none() is not None
    await session.commit()
    return renewed


async def reap_expired(session: AsyncSession, *, max_attempts: int = 3) -> tuple[int, int]:
    """Re-queue expired leases; abandon the ones that have used up their retries.

    Returns ``(requeued, abandoned)``.
    """
    now = utcnow()
    expired = (
        (
            await session.execute(
                select(Trial)
                .where(
                    and_(
                        Trial.lease_expires_at.is_not(None),
                        Trial.lease_expires_at < now,
                        Trial.status.in_(
                            [
                                TrialStatus.LEASED.value,
                                TrialStatus.SETUP.value,
                                TrialStatus.AGENT.value,
                                TrialStatus.VERIFY.value,
                            ]
                        ),
                    )
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )

    requeued = abandoned = 0
    for trial in expired:
        trial.attempt_count += 1
        if trial.attempt_count >= max_attempts:
            trial.status = TrialStatus.ERROR.value
            trial.error_class = ErrorClass.INFRA_ABANDONED.value
            trial.error_detail = (
                f"lease expired {trial.attempt_count} times; the worker holding this "
                "trial never reported back. This is a harness failure and is excluded "
                "from the score."
            )
            trial.finished_at = now
            trial.lease_expires_at = None
            finished = (Run.trials_done + 1 >= Run.trials_total) & Run.status.in_(
                ["queued", "running"]
            )
            await session.execute(
                update(Run)
                .where(Run.id == trial.run_id)
                .values(
                    trials_done=Run.trials_done + 1,
                    status=case((finished, RunStatus.COMPLETED.value), else_=Run.status),
                    finished_at=case((finished, now), else_=Run.finished_at),
                )
            )
            abandoned += 1
        else:
            trial.status = TrialStatus.QUEUED.value
            trial.worker_id = None
            trial.lease_expires_at = None
            trial.container_id = None
            trial.queued_at = now
            requeued += 1

    if expired:
        await session.commit()
        logger.warning("reaper: %d re-queued, %d abandoned", requeued, abandoned)
    return requeued, abandoned


async def cancel_run_trials(session: AsyncSession, run_id: UUID) -> int:
    """Cooperative cancel: mark everything not yet finished as cancelled.

    In-flight trials notice on their next status check — within a few seconds —
    and their containers are killed by the pipeline's teardown.
    """
    result = await session.execute(
        update(Trial)
        .where(
            and_(
                Trial.run_id == run_id,
                Trial.status.not_in(
                    [
                        TrialStatus.SCORED.value,
                        TrialStatus.ERROR.value,
                        TrialStatus.TIMEOUT.value,
                        TrialStatus.CANCELLED.value,
                    ]
                ),
            )
        )
        .values(
            status=TrialStatus.CANCELLED.value,
            error_class=ErrorClass.CANCELLED.value,
            error_detail="run cancelled by operator",
            finished_at=utcnow(),
            lease_expires_at=None,
        )
        .returning(Trial.id)
    )
    affected = len(result.scalars().all())
    await session.commit()
    return affected


async def requeue_infra_failures(session: AsyncSession, run_id: UUID) -> int:
    """``POST /runs/{id}/retry-failed``.

    Deliberately narrow: only ``infra_*`` classes are eligible. A timed-out or
    wrong-answer trial is the agent's result, and re-rolling it until it passes
    is how a benchmark inflates its own numbers.
    """
    result = await session.execute(
        update(Trial)
        .where(
            and_(
                Trial.run_id == run_id,
                Trial.status.in_([TrialStatus.ERROR.value, TrialStatus.CANCELLED.value]),
                or_(
                    Trial.error_class.like("infra_%"),
                    Trial.error_class == ErrorClass.VERIFIER_CRASH.value,
                ),
            )
        )
        .values(
            status=TrialStatus.QUEUED.value,
            worker_id=None,
            lease_expires_at=None,
            error_class=None,
            error_detail=None,
            finished_at=None,
            queued_at=utcnow(),
        )
        .returning(Trial.id)
    )
    affected = len(result.scalars().all())
    await session.commit()
    return affected


async def queue_depth(session: AsyncSession, run_id: UUID | None = None) -> dict[str, int]:
    """Feeds ``autobench_queue_depth`` and the ``QueueStalled`` alert."""
    query = select(Trial.status, func.count()).group_by(Trial.status)
    if run_id is not None:
        query = query.where(Trial.run_id == run_id)
    rows = (await session.execute(query)).all()
    return {str(status): int(count) for status, count in rows}
