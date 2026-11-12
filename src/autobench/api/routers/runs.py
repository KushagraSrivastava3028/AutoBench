"""Runs: launch, watch, cancel, compare.

``POST /v1/runs`` returns ``202`` as soon as the trials are planned and
committed. It does **not** wait for a worker. That separation is the single most
important structural decision in the system: launching a run never blocks on
running a trial, which is what makes the API fast, workers restartable, and the
whole thing crash-tolerant without durable-workflow machinery.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import AsyncIterator
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from autobench.api.deps import PrincipalDep, SessionDep, SettingsDep, write_audit
from autobench.api.events import format_sse, subscribe
from autobench.api.schemas import (
    MatrixOut,
    ProvenanceOut,
    RunCreateIn,
    RunDetailOut,
    RunMetricsOut,
    RunOut,
)
from autobench.api.services import (
    aggregate_for_run,
    error_breakdown,
    estimate_eta,
    resolve_agent,
    resolve_model,
    resolve_suite,
    run_matrix,
    run_select,
    with_names,
)
from autobench.core.errors import ConflictError, NotFoundError, ValidationFailure
from autobench.core.ids import utcnow
from autobench.core.security import Scope
from autobench.db.models import (
    Model,
    Run,
    RunStatus,
    SuiteMember,
    TaskVersion,
    Trial,
)
from autobench.worker.planner import TaskEntry, estimate_makespan, plan_run
from autobench.worker.queue import cancel_run_trials, requeue_infra_failures

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.post("", response_model=RunOut, status_code=202)
async def launch_run(
    body: RunCreateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    request: Request,
    response: Response,
) -> RunOut:
    principal.require(Scope.RUN)

    suite = await resolve_suite(session, body.suite)
    if suite is None:
        raise NotFoundError(f"no suite {body.suite!r}")
    agent = await resolve_agent(session, body.agent)
    if agent is None:
        raise NotFoundError(f"no agent {body.agent!r}")

    model = await resolve_model(session, body.model)
    if model is None:
        model = await _autoregister_model(session, body.model)

    members = (
        await session.execute(
            select(SuiteMember, TaskVersion)
            .join(TaskVersion, TaskVersion.id == SuiteMember.task_version_id)
            .where(SuiteMember.suite_id == suite.id)
        )
    ).all()
    if not members:
        raise ValidationFailure(f"suite {suite.slug!r} has no members")

    from autobench import __version__

    seed = body.seed if body.seed is not None else secrets.randbits(48)
    run = Run(
        suite_id=suite.id,
        agent_id=agent.id,
        model_id=model.id,
        k=body.k,
        concurrency=body.concurrency,
        status=RunStatus.QUEUED.value,
        seed=seed,
        config={
            **body.config,
            "_provenance": {
                "harness_version": __version__,
                "agent_version": agent.version,
                "pricing_version": model.pricing_version,
            },
        },
        label=body.label,
        budget_usd=body.budget_usd,
        trials_total=0,
        created_by=principal.name,
    )
    session.add(run)
    await session.flush()

    entries = [
        TaskEntry(
            task_version_id=version.id,
            task_id=version.task_id,
            agent_timeout_sec=int((version.spec.get("agent") or {}).get("timeout_sec", 900)),
            observed_p50_ms=await _observed_p50(session, version.id),
            image_digest=version.image_digest,
        )
        for _member, version in members
    ]

    plan = plan_run(run.id, entries, k=body.k, run_seed=seed)
    for planned in plan.trials:
        session.add(
            Trial(
                id=planned.id,
                run_id=run.id,
                task_version_id=planned.task_version_id,
                attempt_index=planned.attempt_index,
                seed=planned.seed,
                priority=planned.priority,
                expected_duration_ms=planned.expected_duration_ms,
            )
        )
    run.trials_total = plan.total

    await write_audit(session, principal, "run.launch", str(run.id), request, plan.summary())
    await session.commit()

    response.headers["Location"] = f"/v1/runs/{run.id}"
    logger.info("run %s queued: %s", run.id, plan.summary())

    loaded = (await session.execute(run_select().where(Run.id == run.id))).scalar_one()
    return RunOut(
        **{**loaded.__dict__, **with_names(loaded), "cost_usd": float(loaded.cost_usd or 0)}
    )


async def _autoregister_model(session: SessionDep, reference: str) -> Model:
    """Accept ``provider/model-id`` for a model nobody registered yet.

    The alternative is making every user pre-register a model before their first
    run, which is friction with no safety benefit — the pricing table is keyed
    on the same string either way.
    """
    from autobench.models.pricing import PRICING_VERSION, lookup
    from autobench.models.router import ModelRef

    ref = ModelRef.parse(reference)
    price = lookup(ref.provider, ref.model_id)
    model = Model(
        slug=reference,
        provider=ref.provider,
        model_id=ref.model_id,
        display_name=ref.model_id,
        context_window=price.context_window if price else None,
        input_price_per_mtok=price.input_per_mtok if price else None,
        output_price_per_mtok=price.output_per_mtok if price else None,
        cached_price_per_mtok=price.cached_input_per_mtok if price else None,
        pricing_version=PRICING_VERSION,
    )
    session.add(model)
    await session.flush()
    return model


async def _observed_p50(session: SessionDep, task_version_id: UUID) -> int | None:
    """p50 duration from the last ten trials of this exact version (LPT input)."""
    rows = (
        (
            await session.execute(
                select(Trial.duration_ms)
                .where(Trial.task_version_id == task_version_id, Trial.duration_ms.is_not(None))
                .order_by(Trial.finished_at.desc())
                .limit(10)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    ordered = sorted(int(r) for r in rows if r is not None)
    return ordered[len(ordered) // 2]


@router.get("", response_model=list[RunOut])
async def list_runs(
    session: SessionDep,
    principal: PrincipalDep,
    status: Annotated[str | None, Query()] = None,
    suite: Annotated[str | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[RunOut]:
    query = run_select().order_by(Run.created_at.desc()).limit(limit)
    if status:
        query = query.where(Run.status == status)
    rows = (await session.execute(query)).scalars().all()

    # Slug filters apply after loading: the joins are one-to-one and the page
    # size is bounded, so an extra WHERE buys nothing legible.
    def matches(run: Run) -> bool:
        return (
            (not suite or run.suite.slug == suite)
            and (not agent or run.agent.slug == agent)
            and (not model or run.model.slug == model)
        )

    return [
        RunOut(**{**r.__dict__, **with_names(r), "cost_usd": float(r.cost_usd or 0)})
        for r in rows
        if matches(r)
    ]


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: UUID, session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> RunDetailOut:
    run = (await session.execute(run_select().where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"no run {run_id}")

    aggregate = await aggregate_for_run(session, run, settings)
    elapsed, eta = estimate_eta(run)

    return RunDetailOut(
        **{**run.__dict__, **with_names(run), "cost_usd": float(run.cost_usd or 0)},
        metrics=RunMetricsOut.model_validate(aggregate.to_json()),
        elapsed_sec=elapsed,
        eta_sec=eta,
        error_breakdown=await error_breakdown(session, run_id),
    )


@router.get("/{run_id}/matrix", response_model=MatrixOut)
async def get_matrix(run_id: UUID, session: SessionDep, principal: PrincipalDep) -> MatrixOut:
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFoundError(f"no run {run_id}")
    return MatrixOut.model_validate(await run_matrix(session, run))


@router.get("/{run_id}/provenance", response_model=ProvenanceOut)
async def provenance(run_id: UUID, session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    run = (await session.scalars(run_select().where(Run.id == run_id))).first()
    if run is None:
        raise NotFoundError("Run not found")
    versions = (
        await session.scalars(
            select(TaskVersion)
            .join(Trial, Trial.task_version_id == TaskVersion.id)
            .where(Trial.run_id == run_id)
            .distinct()
        )
    ).all()
    captured = (run.config or {}).get("_provenance", {})
    return {
        "run_id": run.id,
        "seed": run.seed,
        "harness_version": captured.get("harness_version"),
        "agent_version": captured.get("agent_version"),
        "model": run.model.slug,
        "pricing_version": captured.get("pricing_version"),
        "tasks": [
            {
                "task_id": v.task_id,
                "version_id": v.id,
                "semver": v.semver,
                "content_hash": v.content_hash,
                "image_digest": v.image_digest,
            }
            for v in versions
        ],
    }


@router.post("/{run_id}/cancel", response_model=RunOut)
async def cancel_run(
    run_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> RunOut:
    """Cooperative cancel. In-flight trials notice within a few seconds."""
    principal.require(Scope.RUN)
    run = (await session.execute(run_select().where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"no run {run_id}")
    if RunStatus(run.status).terminal:
        raise ConflictError(f"run {run_id} is already {run.status}")

    cancelled = await cancel_run_trials(session, run_id)
    run.status = RunStatus.CANCELLED.value
    run.finished_at = utcnow()
    await write_audit(
        session, principal, "run.cancel", str(run_id), request, {"trials_cancelled": cancelled}
    )
    await session.commit()

    from autobench.api.events import publish
    from autobench.core.config import get_settings

    await publish(get_settings(), str(run_id), {"event": "run_cancelled", "run_id": str(run_id)})
    return RunOut(**{**run.__dict__, **with_names(run), "cost_usd": float(run.cost_usd or 0)})


@router.post("/{run_id}/retry-failed", response_model=RunOut)
async def retry_failed(
    run_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> RunOut:
    """Re-queue ``infra_*`` failures only.

    Agent failures are the agent's result. Re-rolling them until they pass is
    exactly how a benchmark inflates its own numbers, so this endpoint cannot
    do it even if asked.
    """
    principal.require(Scope.RUN)
    run = (await session.execute(run_select().where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise NotFoundError(f"no run {run_id}")

    requeued = await requeue_infra_failures(session, run_id)
    if requeued:
        run.status = RunStatus.RUNNING.value
        run.finished_at = None
        run.trials_done = max(0, run.trials_done - requeued)
    await write_audit(
        session, principal, "run.retry_failed", str(run_id), request, {"requeued": requeued}
    )
    await session.commit()
    return RunOut(**{**run.__dict__, **with_names(run), "cost_usd": float(run.cost_usd or 0)})


@router.get("/{run_id}/events")
async def run_events(
    run_id: UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    last_event_id: Annotated[int, Query(alias="Last-Event-ID", ge=0, le=2**63 - 1)] = 0,
    last_event_header: Annotated[
        int | None, Header(alias="Last-Event-ID", ge=0, le=2**63 - 1)
    ] = None,
) -> StreamingResponse:
    """SSE stream of trial transitions and run progress."""
    if await session.get(Run, run_id) is None:
        raise NotFoundError(f"no run {run_id}")
    cursor = last_event_header if last_event_header is not None else last_event_id

    async def generator() -> AsyncIterator[str]:
        async for payload in subscribe(settings, str(run_id), last_event_id=cursor):
            yield format_sse(payload)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers text/event-stream by default, which turns a live
            # feed into a batch delivered at the end of the run.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{run_id}/estimate")
async def estimate(run_id: UUID, session: SessionDep, principal: PrincipalDep) -> dict[str, float]:
    """Makespan bounds for a queued run — shown before launch, not after."""
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFoundError(f"no run {run_id}")
    rows = (
        await session.execute(
            select(Trial.expected_duration_ms, Trial.task_version_id).where(Trial.run_id == run_id)
        )
    ).all()
    entries = [
        TaskEntry(
            task_version_id=version_id,
            task_id=str(version_id),
            agent_timeout_sec=900,
            observed_p50_ms=int(duration or 0) or None,
        )
        for duration, version_id in rows
    ]
    return estimate_makespan(entries, k=1, concurrency=run.concurrency)
