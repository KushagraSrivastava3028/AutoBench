"""Tasks, suites, agents and models — the things a run is assembled from."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from autobench.api.deps import PrincipalDep, SessionDep, SettingsDep, require_scope, write_audit
from autobench.api.schemas import (
    AgentOut,
    ModelOut,
    SuiteCreateIn,
    SuiteDetailOut,
    SuiteOut,
    TaskDetailOut,
    TaskOut,
    TaskRegisterIn,
    TaskVersionOut,
    ValidationOut,
)
from autobench.api.services import task_history
from autobench.core.errors import ConflictError, NotFoundError, ValidationFailure
from autobench.core.ids import utcnow
from autobench.core.security import Scope
from autobench.db.models import Agent, Model, Suite, SuiteMember, Task, TaskVersion
from autobench.taskspec.loader import TaskDirectory, load_task, load_task_from_tar
from autobench.taskspec.validator import validate_task

logger = logging.getLogger(__name__)

tasks_router = APIRouter(prefix="/v1/tasks", tags=["tasks"])
suites_router = APIRouter(prefix="/v1/suites", tags=["suites"])
catalog_router = APIRouter(prefix="/v1", tags=["catalog"])


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


@tasks_router.post("/validate", response_model=ValidationOut)
async def validate_upload(
    principal: Annotated[object, require_scope(Scope.AUTHOR)],
    file: Annotated[UploadFile, File(description="tar/tar.gz of the task directory")],
    settings: SettingsDep,
    deep: bool = False,
) -> ValidationOut:
    """Upload a task directory and run the static gates.

    Static only: the calibration gates need an executor, and this endpoint has
    to answer while an author is still typing. They come back ``skipped``, which
    is not the same as ``passed`` — ``publishable`` stays false until something
    has actually run the oracle.
    """
    payload = await file.read(32 * 1024 * 1024 + 1)
    if len(payload) > 32 * 1024 * 1024:
        raise ValidationFailure("task archives must be at most 32 MiB")
    if not payload:
        raise ValidationFailure("the uploaded archive is empty")

    task, tmp = load_task_from_tar(payload)
    try:
        runner = None
        if deep:
            if settings.executor == "local":
                raise ValidationFailure(
                    "Dynamic validation of uploads requires the Docker executor. "
                    "Local execution does not isolate uploaded code."
                )
            from autobench.worker.pipeline import PipelineCalibrationRunner

            runner = PipelineCalibrationRunner()
        report = await validate_task(task, runner=runner)
        return ValidationOut.model_validate(report.to_json())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@tasks_router.post("", response_model=TaskVersionOut, status_code=201)
async def register_task(
    body: TaskRegisterIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    request: Request,
) -> TaskVersionOut:
    """Register a task version from a server-side directory.

    Content-addressed: registering unchanged content returns the existing
    version rather than creating a duplicate, so a CI job that re-registers the
    whole corpus on every push is idempotent.
    """
    principal.require(Scope.AUTHOR)
    root = Path(body.path).expanduser()
    if not root.is_absolute():
        root = (settings.tasks_dir / body.path).resolve()
    if not root.resolve().is_relative_to(settings.tasks_dir.resolve()):
        raise ValidationFailure("task directories must be inside the configured tasks directory")
    task = load_task(root)
    if body.publish:
        from autobench.worker.pipeline import PipelineCalibrationRunner

        report = await validate_task(task, runner=PipelineCalibrationRunner())
        if not report.publishable:
            raise ValidationFailure(
                "Publication requires every quality gate to pass. "
                "Run deep validation and resolve all failures first."
            )

    version = await _upsert_version(session, task, publish=body.publish)
    await write_audit(
        session, principal, "task.register", task.id, request, {"content_hash": task.content_hash}
    )
    await session.commit()
    return TaskVersionOut.model_validate(version)


@tasks_router.post("/import", response_model=TaskVersionOut, status_code=201)
async def import_task(
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    publish: bool = False,
) -> TaskVersionOut:
    """Persist an uploaded draft, or publish only after all dynamic gates pass."""
    principal.require(Scope.AUTHOR)
    payload = await file.read(32 * 1024 * 1024 + 1)
    if not payload or len(payload) > 32 * 1024 * 1024:
        raise ValidationFailure("Upload a non-empty task archive no larger than 32 MiB")
    task, tmp = load_task_from_tar(payload)
    try:
        runner = None
        if publish:
            if settings.executor == "local":
                raise ValidationFailure(
                    "Publishing uploaded executable tasks requires Docker isolation."
                )
            from autobench.worker.pipeline import PipelineCalibrationRunner

            runner = PipelineCalibrationRunner()
        report = await validate_task(task, runner=runner)
        if not report.ok or (publish and not report.publishable):
            raise ValidationFailure(
                "Resolve validation failures before saving. "
                "Publication also requires every dynamic gate to pass."
            )
        destination = (
            settings.tasks_dir / "imported" / task.id / task.content_hash.removeprefix("sha256:")
        )
        if not destination.is_dir():
            await asyncio.to_thread(shutil.copytree, task.root, destination)
        stored = load_task(destination)
        version = await _upsert_version(session, stored, publish=publish)
        if publish:
            version.oracle_reward = report.oracle_reward
            version.null_reward = report.null_reward
        await write_audit(
            session,
            principal,
            "task.publish" if publish else "task.import",
            task.id,
            detail={"content_hash": task.content_hash},
        )
        await session.commit()
        return TaskVersionOut.model_validate(version)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _upsert_version(
    session: SessionDep, task: TaskDirectory, *, publish: bool
) -> TaskVersion:
    existing = (
        await session.execute(
            select(TaskVersion).where(
                TaskVersion.task_id == task.id,
                TaskVersion.content_hash == task.content_hash,
            )
        )
    ).scalar_one_or_none()

    row = await session.get(Task, task.id)
    if row is None:
        session.add(
            Task(
                id=task.id,
                title=task.spec.task.title,
                category=task.spec.task.category,
                difficulty=task.spec.task.difficulty.value,
                tags=list(task.spec.task.tags),
                author=task.spec.task.author,
            )
        )
        await session.flush()
    else:
        row.title = task.spec.task.title
        row.category = task.spec.task.category
        row.difficulty = task.spec.task.difficulty.value
        row.tags = list(task.spec.task.tags)

    if existing is not None:
        existing.source_path = str(task.root)
        if publish:
            existing.published = True
        return existing

    version = TaskVersion(
        task_id=task.id,
        semver=task.semver,
        content_hash=task.content_hash,
        spec=task.spec.to_json(),
        instruction=task.instruction,
        source_path=str(task.root),
        published=publish,
        validated_at=utcnow() if publish else None,
    )
    session.add(version)
    await session.flush()
    return version


@tasks_router.get("", response_model=list[TaskOut])
async def list_tasks(
    session: SessionDep,
    principal: PrincipalDep,
    category: Annotated[str | None, Query()] = None,
    difficulty: Annotated[str | None, Query()] = None,
    tag: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(description="Substring of the id or title.")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[TaskOut]:
    query = select(Task).options(selectinload(Task.versions)).order_by(Task.id).limit(limit)
    if category:
        query = query.where(Task.category == category)
    if difficulty:
        query = query.where(Task.difficulty == difficulty)
    if q:
        pattern = f"%{q.lower()}%"
        query = query.where(
            func.lower(Task.id).like(pattern) | func.lower(Task.title).like(pattern)
        )

    rows = (await session.execute(query)).scalars().all()
    # Tag filtering happens here rather than in SQL: the column is a JSON list
    # on SQLite and TEXT[] on Postgres, and the catalogue is hundreds of rows,
    # not millions.
    if tag:
        rows = [row for row in rows if tag in (row.tags or [])]

    out: list[TaskOut] = []
    for row in rows:
        latest = row.versions[0] if row.versions else None
        out.append(
            TaskOut(
                id=row.id,
                title=row.title,
                category=row.category,
                difficulty=row.difficulty,
                tags=list(row.tags or []),
                author=row.author,
                created_at=row.created_at,
                latest_version=TaskVersionOut.model_validate(latest) if latest else None,
                history=await task_history(session, row.id),
            )
        )
    return out


@tasks_router.get("/{task_id}", response_model=TaskDetailOut)
async def get_task(task_id: str, session: SessionDep, principal: PrincipalDep) -> TaskDetailOut:
    row = (
        await session.execute(
            select(Task).options(selectinload(Task.versions)).where(Task.id == task_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"no task {task_id!r}")

    latest = row.versions[0] if row.versions else None
    dockerfile = ""
    if latest and latest.source_path:
        candidate = Path(latest.source_path) / latest.spec.get("environment", {}).get(
            "dockerfile", "environment/Dockerfile"
        )
        if candidate.is_file():
            dockerfile = candidate.read_text(encoding="utf-8", errors="replace")

    return TaskDetailOut(
        id=row.id,
        title=row.title,
        category=row.category,
        difficulty=row.difficulty,
        tags=list(row.tags or []),
        author=row.author,
        created_at=row.created_at,
        latest_version=TaskVersionOut.model_validate(latest) if latest else None,
        history=await task_history(session, row.id),
        instruction=latest.instruction if latest else "",
        dockerfile=dockerfile,
        spec=latest.spec if latest else {},
        versions=[TaskVersionOut.model_validate(v) for v in row.versions],
    )


@tasks_router.get("/{task_id}/versions", response_model=list[TaskVersionOut])
async def task_versions(
    task_id: str, session: SessionDep, principal: PrincipalDep
) -> list[TaskVersionOut]:
    rows = (
        (
            await session.execute(
                select(TaskVersion)
                .where(TaskVersion.task_id == task_id)
                .order_by(TaskVersion.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFoundError(f"no versions for task {task_id!r}")
    return [TaskVersionOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------
# suites
# ---------------------------------------------------------------------------


@suites_router.post("", response_model=SuiteDetailOut, status_code=201)
async def create_suite(
    body: SuiteCreateIn,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> SuiteDetailOut:
    principal.require(Scope.AUTHOR)
    if (await session.execute(select(Suite).where(Suite.slug == body.slug))).scalar_one_or_none():
        raise ConflictError(f"a suite with slug {body.slug!r} already exists")

    suite = Suite(slug=body.slug, name=body.name, description=body.description)
    session.add(suite)
    await session.flush()

    version_ids = list(body.task_version_ids)
    if body.task_ids:
        # Pin the newest published version of each named task. The suite stores
        # the version id, so a later edit to the task cannot change what this
        # suite means.
        for task_id in body.task_ids:
            latest = (
                await session.execute(
                    select(TaskVersion)
                    .where(TaskVersion.task_id == task_id, TaskVersion.published.is_(True))
                    .order_by(TaskVersion.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if latest is None:
                raise ValidationFailure(
                    f"task {task_id!r} has no published version; validate and publish it first"
                )
            version_ids.append(latest.id)

    if not version_ids:
        version_ids = list(
            (await session.execute(select(TaskVersion.id).where(TaskVersion.published.is_(True))))
            .scalars()
            .all()
        )
    if not version_ids:
        raise ValidationFailure("no published task versions to put in this suite")

    suite_tasks: set[str] = set()
    for version_id in dict.fromkeys(version_ids):
        version = await session.get(TaskVersion, version_id)
        if version is None or not version.published:
            raise ValidationFailure("Suites can contain only published task versions")
        if version.task_id in suite_tasks:
            raise ValidationFailure("A suite must pin exactly one version of each task")
        suite_tasks.add(version.task_id)
        session.add(SuiteMember(suite_id=suite.id, task_version_id=version_id))

    await write_audit(
        session, principal, "suite.create", body.slug, request, {"members": len(version_ids)}
    )
    await session.commit()
    return await _suite_detail(session, suite)


@suites_router.get("", response_model=list[SuiteOut])
async def list_suites(session: SessionDep, principal: PrincipalDep) -> list[SuiteOut]:
    suites = (await session.execute(select(Suite).order_by(Suite.slug))).scalars().all()
    out = []
    for suite in suites:
        count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SuiteMember)
                    .where(SuiteMember.suite_id == suite.id)
                )
            ).scalar_one()
        )
        out.append(
            SuiteOut(
                id=suite.id,
                slug=suite.slug,
                name=suite.name,
                description=suite.description,
                created_at=suite.created_at,
                member_count=count,
            )
        )
    return out


@suites_router.get("/{suite_ref}", response_model=SuiteDetailOut)
async def get_suite(suite_ref: str, session: SessionDep, principal: PrincipalDep) -> SuiteDetailOut:
    from autobench.api.services import resolve_suite

    suite = await resolve_suite(session, suite_ref)
    if suite is None:
        raise NotFoundError(f"no suite {suite_ref!r}")
    return await _suite_detail(session, suite)


async def _suite_detail(session: SessionDep, suite: Suite) -> SuiteDetailOut:
    rows = (
        await session.execute(
            select(SuiteMember, TaskVersion, Task)
            .join(TaskVersion, TaskVersion.id == SuiteMember.task_version_id)
            .join(Task, Task.id == TaskVersion.task_id)
            .where(SuiteMember.suite_id == suite.id)
            .order_by(Task.id)
        )
    ).all()
    return SuiteDetailOut(
        id=suite.id,
        slug=suite.slug,
        name=suite.name,
        description=suite.description,
        created_at=suite.created_at,
        member_count=len(rows),
        members=[
            {
                "task_version_id": member.task_version_id,
                "task_id": task.id,
                "title": task.title,
                "difficulty": task.difficulty,
                "weight": float(member.weight),
            }
            for member, _version, task in rows
        ],
    )


# ---------------------------------------------------------------------------
# agents and models
# ---------------------------------------------------------------------------


@catalog_router.get("/agents", response_model=list[AgentOut])
async def list_agents(session: SessionDep, principal: PrincipalDep) -> list[AgentOut]:
    rows = (await session.execute(select(Agent).order_by(Agent.slug))).scalars().all()
    return [AgentOut.model_validate(r) for r in rows]


@catalog_router.get("/models", response_model=list[ModelOut])
async def list_models(session: SessionDep, principal: PrincipalDep) -> list[ModelOut]:
    rows = (
        (await session.execute(select(Model).where(Model.enabled.is_(True)).order_by(Model.slug)))
        .scalars()
        .all()
    )
    return [ModelOut.model_validate(r) for r in rows]


@catalog_router.get("/task-versions/{version_id}", response_model=TaskVersionOut)
async def get_version(
    version_id: UUID, session: SessionDep, principal: PrincipalDep
) -> TaskVersionOut:
    version = await session.get(TaskVersion, version_id)
    if version is None:
        raise NotFoundError(f"no task version {version_id}")
    return TaskVersionOut.model_validate(version)
