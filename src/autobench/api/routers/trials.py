"""Trial detail: trajectory, diff, verdict, replay.

This is the debugging surface. A red cell is worth nothing if you cannot find
out why, so every trial exposes what it did (trajectory), what it changed
(diff), how it was judged (verdict) and how to reproduce it (replay).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from autobench.api.deps import PrincipalDep, SessionDep, SettingsDep
from autobench.api.schemas import (
    ArtifactOut,
    DiffOut,
    ReplayOut,
    ScoreOut,
    TrajectoryOut,
    TrialEventOut,
    TrialOut,
)
from autobench.artifacts.store import get_store
from autobench.core.errors import ConflictError, NotFoundError
from autobench.db.models import Artifact, ArtifactKind, Score, TaskVersion, Trial
from autobench.taskspec.loader import load_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/trials", tags=["trials"])


async def _load(session: SessionDep, trial_id: UUID) -> tuple[Trial, TaskVersion]:
    row = (
        await session.execute(
            select(Trial, TaskVersion)
            .join(TaskVersion, TaskVersion.id == Trial.task_version_id)
            .where(Trial.id == trial_id)
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError(f"no trial {trial_id}")
    return row[0], row[1]


@router.get("/{trial_id}", response_model=TrialOut)
async def get_trial(trial_id: UUID, session: SessionDep, principal: PrincipalDep) -> TrialOut:
    trial, version = await _load(session, trial_id)
    score = await session.get(Score, trial_id)

    score_out = None
    if score is not None:
        payload = score.items if isinstance(score.items, dict) else {}
        score_out = ScoreOut(
            reward=float(score.reward),
            binary_pass=bool(score.binary_pass),
            items=payload.get("items") or [],
            error=payload.get("error"),
            tool_calls=score.tool_calls,
            input_tokens=score.input_tokens,
            output_tokens=score.output_tokens,
            cached_tokens=score.cached_tokens,
            cost_usd=float(score.cost_usd) if score.cost_usd is not None else None,
            setup_ms=score.setup_ms,
            agent_ms=score.agent_ms,
            verify_ms=score.verify_ms,
        )

    return TrialOut(
        id=trial.id,
        run_id=trial.run_id,
        task_id=version.task_id,
        task_version_id=version.id,
        attempt_index=trial.attempt_index,
        status=trial.status,
        seed=trial.seed,
        attempt_count=trial.attempt_count,
        container_id=trial.container_id,
        error_class=trial.error_class,
        error_detail=trial.error_detail,
        started_at=trial.started_at,
        finished_at=trial.finished_at,
        duration_ms=trial.duration_ms,
        score=score_out,
    )


@router.get("/{trial_id}/trajectory", response_model=TrajectoryOut)
async def get_trajectory(
    trial_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> TrajectoryOut:
    """Paginated. A long trial has thousands of events and the timeline is virtualised."""
    from autobench.db.models import TrialEvent

    total = int(
        (
            await session.execute(
                select(func.count()).select_from(TrialEvent).where(TrialEvent.trial_id == trial_id)
            )
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                select(TrialEvent)
                .where(TrialEvent.trial_id == trial_id)
                .order_by(TrialEvent.seq)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    if total == 0:
        # Events older than the retention window live only as an archived
        # artifact. Fall back to it rather than showing an empty timeline.
        archived = await _trajectory_from_artifact(session, trial_id)
        if archived:
            return TrajectoryOut(
                trial_id=trial_id,
                total=len(archived),
                events=archived[offset : offset + limit],
            )

    return TrajectoryOut(
        trial_id=trial_id,
        total=total,
        events=[TrialEventOut.model_validate(r) for r in rows],
    )


async def _trajectory_from_artifact(session: SessionDep, trial_id: UUID) -> list[TrialEventOut]:
    artifact = (
        await session.execute(
            select(Artifact).where(
                Artifact.trial_id == trial_id,
                Artifact.kind == ArtifactKind.TRAJECTORY.value,
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        return []
    try:
        raw = await get_store().get(artifact.storage_key)
    except Exception as exc:  # pragma: no cover
        logger.warning("could not read archived trajectory for %s: %s", trial_id, exc)
        return []

    events: list[TrialEventOut] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(TrialEventOut.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return events


@router.get("/{trial_id}/artifacts", response_model=list[ArtifactOut])
async def get_artifacts(
    trial_id: UUID, session: SessionDep, principal: PrincipalDep
) -> list[ArtifactOut]:
    """Presigned URLs, 15-minute TTL, scoped per object."""
    rows = (
        (await session.execute(select(Artifact).where(Artifact.trial_id == trial_id)))
        .scalars()
        .all()
    )
    store = get_store()
    return [
        ArtifactOut(
            kind=row.kind,
            url=await store.url(row.storage_key),
            size_bytes=row.size_bytes,
            content_type=row.content_type,
        )
        for row in rows
    ]


@router.get("/{trial_id}/diff", response_model=DiffOut)
async def get_diff(trial_id: UUID, session: SessionDep, principal: PrincipalDep) -> DiffOut:
    """What the agent changed. Usually where the answer is."""
    artifact = (
        await session.execute(
            select(Artifact).where(
                Artifact.trial_id == trial_id, Artifact.kind == ArtifactKind.DIFF.value
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        return DiffOut(counts={"added": 0, "modified": 0, "deleted": 0}, truncated=False, files=[])
    raw = await get_store().get(artifact.storage_key)
    return DiffOut.model_validate(json.loads(raw.decode("utf-8")))


@router.get("/{trial_id}/replay", response_model=ReplayOut)
async def get_replay(
    trial_id: UUID, session: SessionDep, settings: SettingsDep, principal: PrincipalDep
) -> ReplayOut:
    """Shell commands that reproduce this trial locally.

    Replayability is what turns a red cell into a fixed bug, which is why it is
    a v1 requirement rather than a nice-to-have.
    """
    trial, version = await _load(session, trial_id)
    if not version.source_path or not Path(version.source_path).is_dir():
        raise NotFoundError(
            f"task version {version.id} has no source directory on this host; "
            "replay needs the task checked out"
        )

    task = load_task(version.source_path)
    if task.content_hash != version.content_hash:
        raise ConflictError(
            "The task source has changed. "
            "Restore the recorded task version before replaying this trial."
        )
    from autobench.executor.base import SandboxSpec
    from autobench.executor.docker_provider import replay_commands

    image = version.image_digest or task.image_tag(settings.image_namespace)
    spec = SandboxSpec.from_task(task, image, seed=trial.seed)
    commands = replay_commands(task, image, spec, trial_id=str(trial_id)[:8])
    commands.insert(0, f"autobench replay --trial {str(trial_id)[:8]} --interactive")

    return ReplayOut(
        trial_id=trial.id,
        task_id=version.task_id,
        content_hash=version.content_hash,
        commands=commands,
    )
