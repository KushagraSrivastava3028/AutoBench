"""Ops surface: health, readiness, metrics, and local artifact serving."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, cast

from fastapi import APIRouter, Response
from sqlalchemy import func, select, text

from autobench import __version__
from autobench.api.deps import PrincipalDep, SessionDep, SettingsDep
from autobench.core.errors import NotFoundError
from autobench.db.models import Run, RunStatus, Trial, TrialStatus, Worker

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness. Deliberately dependency-free — it answers "is the process up".

    A liveness probe that checks the database restarts the API when Postgres
    blips, which turns a recoverable outage into a cascading one.
    """
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
async def readyz(session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    """Readiness. This one *does* check dependencies — that is the difference."""
    checks: dict[str, str] = {}
    healthy = True

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        healthy = False

    if settings.queue == "redis":
        try:
            import redis.asyncio as redis_async

            factory = cast(Callable[..., Any], redis_async.from_url)
            client = factory(settings.redis_url)
            await client.ping()
            await client.aclose()
            checks["redis"] = "ok"
        except Exception as exc:
            checks["redis"] = f"error: {exc}"
            healthy = False
    else:
        checks["redis"] = "not configured (queue=memory)"

    checks["executor"] = settings.executor
    return {"status": "ready" if healthy else "degraded", "checks": checks}


@router.get("/metrics")
async def metrics(session: SessionDep, settings: SettingsDep) -> Response:
    """Prometheus exposition (PRD §13.2).

    Counters that belong to the worker process are exported by the worker; what
    the API can answer authoritatively is the *state of the warehouse*: queue
    depth, trial outcomes, spend, and worker liveness.
    """
    lines: list[str] = []

    def add(name: str, kind: str, help_text: str, samples: list[tuple[str, float]]) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            lines.append(f"{name}{labels} {value}")

    status_rows = (
        await session.execute(select(Trial.status, func.count()).group_by(Trial.status))
    ).all()
    add(
        "autobench_trials_total",
        "counter",
        "Trials by terminal or in-flight status.",
        [(f'{{status="{status}"}}', float(count)) for status, count in status_rows],
    )

    error_rows = (
        await session.execute(
            select(Trial.error_class, func.count())
            .where(Trial.error_class.is_not(None))
            .group_by(Trial.error_class)
        )
    ).all()
    add(
        "autobench_trial_errors_total",
        "counter",
        "Trials by error class. infra_* means the harness failed, not the agent.",
        [(f'{{error_class="{cls}"}}', float(count)) for cls, count in error_rows],
    )

    queued = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Trial)
                .where(Trial.status == TrialStatus.QUEUED.value)
            )
        ).scalar_one()
    )
    add("autobench_queue_depth", "gauge", "Trials waiting to be leased.", [("", float(queued))])

    active_runs = int(
        (
            await session.execute(
                select(func.count()).select_from(Run).where(Run.status == RunStatus.RUNNING.value)
            )
        ).scalar_one()
    )
    add("autobench_runs_active", "gauge", "Runs currently executing.", [("", float(active_runs))])

    spend = float(
        (await session.execute(select(func.coalesce(func.sum(Run.cost_usd), 0)))).scalar_one() or 0
    )
    add("autobench_cost_usd_total", "counter", "Cumulative reported spend.", [("", spend)])

    workers = (
        (await session.execute(select(Worker).where(Worker.stopped_at.is_(None)))).scalars().all()
    )
    from autobench.core.ids import utcnow

    now = utcnow()
    add(
        "autobench_worker_heartbeat_age_seconds",
        "gauge",
        "Seconds since each worker last reported. Alert above 120.",
        [
            (
                f'{{worker_id="{w.id}",hostname="{w.hostname}"}}',
                (now - w.last_heartbeat).total_seconds(),
            )
            for w in workers
        ],
    )
    add(
        "autobench_containers_active",
        "gauge",
        "Containers alive per worker. Above capacity for 2m means teardown is failing.",
        [(f'{{worker_id="{w.id}"}}', float(w.active_trials)) for w in workers],
    )

    lane_samples: list[tuple[str, float]] = []
    for worker in workers:
        for lane, stats in (worker.lanes or {}).items():
            if isinstance(stats, dict):
                lane_samples.append(
                    (
                        f'{{worker_id="{worker.id}",lane="{lane}"}}',
                        float(stats.get("utilisation", 0.0)),
                    )
                )
    add(
        "autobench_lane_utilisation",
        "gauge",
        "Fraction of wall-clock each concurrency lane spent saturated.",
        lane_samples,
    )

    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/v1/artifacts/{key:path}")
async def get_artifact(key: str, settings: SettingsDep, principal: PrincipalDep) -> Response:
    """Serve a locally-stored artifact.

    Only used when ``artifact_backend=local``; the S3 backend hands out
    presigned URLs that never touch the API.
    """
    from autobench.artifacts.store import LocalArtifactStore, get_store

    store = get_store(settings)
    if not isinstance(store, LocalArtifactStore):
        raise NotFoundError("this deployment serves artifacts by presigned URL")
    try:
        data = await store.get(key)
    except (OSError, ValueError) as exc:
        raise NotFoundError(f"no artifact {key!r}") from exc

    media = "application/json" if key.endswith(".json") else "text/plain; charset=utf-8"
    if key.endswith(".jsonl"):
        media = "application/x-ndjson"
    return Response(data, media_type=media)
