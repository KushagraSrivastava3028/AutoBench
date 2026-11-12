"""Cross-run statistics: comparison, leaderboard, dashboard, workers, export."""

from __future__ import annotations

import csv
import io
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from autobench.api.deps import PrincipalDep, SessionDep, SettingsDep
from autobench.api.schemas import (
    ComparisonOut,
    DashboardOut,
    LeaderboardRowOut,
    WorkerOut,
)
from autobench.api.services import (
    compare,
    dashboard,
    leaderboard,
    resolve_suite,
    run_select,
    worker_health,
)
from autobench.core.errors import NotFoundError
from autobench.db.models import Run, Score, TaskVersion, Trial

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/stats", tags=["stats"])
export_router = APIRouter(prefix="/v1/export", tags=["export"])


@router.get("/dashboard", response_model=DashboardOut)
async def get_dashboard(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> DashboardOut:
    """Everything the landing page needs, in one round trip."""
    return DashboardOut.model_validate(await dashboard(session, settings))


@router.get("/compare", response_model=ComparisonOut)
async def compare_runs_endpoint(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    a: Annotated[UUID, Query(description="Baseline run.")],
    b: Annotated[UUID, Query(description="Candidate run.")],
) -> ComparisonOut:
    """Paired bootstrap over the shared task set, plus McNemar on the flips.

    ``inconclusive`` is the field the UI must respect: when the delta interval
    straddles zero, the comparison renders grey. The frontend is not allowed to
    imply a difference the statistics do not support.
    """
    run_a = (await session.execute(run_select().where(Run.id == a))).scalar_one_or_none()
    run_b = (await session.execute(run_select().where(Run.id == b))).scalar_one_or_none()
    if run_a is None or run_b is None:
        raise NotFoundError("one or both runs do not exist")
    return ComparisonOut.model_validate(await compare(session, run_a, run_b, settings))


@router.get("/leaderboard", response_model=list[LeaderboardRowOut])
async def get_leaderboard(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    suite: Annotated[str, Query(description="Suite slug or UUID.")],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[LeaderboardRowOut]:
    resolved = await resolve_suite(session, suite)
    if resolved is None:
        raise NotFoundError(f"no suite {suite!r}")
    rows = await leaderboard(session, resolved.id, settings, limit)
    return [LeaderboardRowOut.model_validate(r) for r in rows]


@router.get("/workers", response_model=list[WorkerOut])
async def get_workers(session: SessionDep, principal: PrincipalDep) -> list[WorkerOut]:
    """Live lane utilisation — shows whether you are container-bound or API-bound."""
    return [WorkerOut.model_validate(w) for w in await worker_health(session)]


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


async def _flat_rows(session: SessionDep, run_id: UUID) -> list[dict[str, object]]:
    rows = (
        await session.execute(
            select(Trial, Score, TaskVersion)
            .join(TaskVersion, TaskVersion.id == Trial.task_version_id)
            .outerjoin(Score, Score.trial_id == Trial.id)
            .where(Trial.run_id == run_id)
            .order_by(TaskVersion.task_id, Trial.attempt_index)
        )
    ).all()
    return [
        {
            "trial_id": str(trial.id),
            "run_id": str(trial.run_id),
            "task_id": version.task_id,
            "task_version": version.semver,
            "content_hash": version.content_hash,
            "attempt_index": trial.attempt_index,
            "status": trial.status,
            "seed": trial.seed,
            "error_class": trial.error_class or "",
            "duration_ms": trial.duration_ms or 0,
            "reward": float(score.reward) if score else "",
            "binary_pass": bool(score.binary_pass) if score else "",
            "tool_calls": score.tool_calls if score else "",
            "input_tokens": score.input_tokens if score else "",
            "output_tokens": score.output_tokens if score else "",
            "cost_usd": float(score.cost_usd) if score and score.cost_usd else "",
            "setup_ms": score.setup_ms if score else "",
            "agent_ms": score.agent_ms if score else "",
            "verify_ms": score.verify_ms if score else "",
        }
        for trial, score, version in rows
    ]


@export_router.get("/{run_id}.csv")
async def export_csv(
    run_id: UUID, session: SessionDep, principal: PrincipalDep
) -> StreamingResponse:
    rows = await _flat_rows(session, run_id)
    if not rows:
        raise NotFoundError(f"run {run_id} has no trials")

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}.csv"'},
    )


@export_router.get("/{run_id}.parquet")
async def export_parquet(
    run_id: UUID, session: SessionDep, principal: PrincipalDep
) -> StreamingResponse:
    """Full flat export. Falls back to CSV when pyarrow is not installed."""
    rows = await _flat_rows(session, run_id)
    if not rows:
        raise NotFoundError(f"run {run_id} has no trials")

    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return await export_csv(run_id, session, principal)

    table = pa.Table.from_pylist(
        [{k: (v if v != "" else None) for k, v in r.items()} for r in rows]
    )
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd")
    sink.seek(0)
    return StreamingResponse(
        iter([sink.getvalue()]),
        media_type="application/vnd.apache.parquet",
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}.parquet"'},
    )
