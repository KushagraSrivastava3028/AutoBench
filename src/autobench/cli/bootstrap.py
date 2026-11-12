"""Seeding: register the task corpus, the built-in agents and the known models.

Called by ``autobench seed`` and by ``autobench run`` when it finds an empty
database, so that the first command a new user types works rather than telling
them to run three other commands first.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autobench.agents import ADAPTERS
from autobench.core.config import Settings
from autobench.core.ids import utcnow
from autobench.db.models import Agent, Model, Suite, SuiteMember, Task, TaskVersion
from autobench.models.pricing import PRICES, PRICING_VERSION
from autobench.taskspec.loader import TaskDirectory, discover_tasks, load_task

logger = logging.getLogger(__name__)


async def seed_agents(session: AsyncSession) -> int:
    """One row per built-in adapter."""
    created = 0
    for slug, adapter_cls in ADAPTERS.items():
        exists = (
            await session.execute(select(Agent).where(Agent.slug == slug))
        ).scalar_one_or_none()
        if exists is not None:
            continue
        instance = adapter_cls()
        session.add(
            Agent(
                slug=slug,
                name=slug.replace("-", " ").title(),
                adapter=slug,
                mode=instance.mode.value,
                version=instance.version,
                egress_allowlist=list(instance.egress_allowlist),
            )
        )
        created += 1
    return created


async def seed_models(session: AsyncSession) -> int:
    """Every model in the pricing table, plus a deterministic offline model.

    ``mock/mock-1`` is not a placeholder: it is what ``bench-harness`` and CI
    use, because a real model's sampling variance is larger than the harness
    overhead being measured.
    """
    created = 0
    entries: Iterable[tuple[str, object]] = PRICES.items()
    for qualified, price in entries:
        provider, model_id = qualified.split("/", 1)
        if model_id == "*":
            continue
        exists = (
            await session.execute(select(Model).where(Model.slug == qualified))
        ).scalar_one_or_none()
        if exists is not None:
            continue
        session.add(
            Model(
                slug=qualified,
                provider=provider,
                model_id=model_id,
                display_name=model_id,
                context_window=getattr(price, "context_window", None),
                input_price_per_mtok=getattr(price, "input_per_mtok", None),
                output_price_per_mtok=getattr(price, "output_per_mtok", None),
                cached_price_per_mtok=getattr(price, "cached_input_per_mtok", None),
                pricing_version=PRICING_VERSION,
            )
        )
        created += 1

    if not (
        await session.execute(select(Model).where(Model.slug == "mock/mock-1"))
    ).scalar_one_or_none():
        session.add(
            Model(
                slug="mock/mock-1",
                provider="mock",
                model_id="mock-1",
                display_name="Mock (deterministic, offline)",
                context_window=128_000,
                input_price_per_mtok=0.0,
                output_price_per_mtok=0.0,
                pricing_version=PRICING_VERSION,
            )
        )
        created += 1
    return created


async def register_task(
    session: AsyncSession, task: TaskDirectory, *, publish: bool = True
) -> TaskVersion:
    """Content-addressed upsert. Re-registering unchanged content is a no-op."""
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

    existing = (
        await session.execute(
            select(TaskVersion).where(
                TaskVersion.task_id == task.id,
                TaskVersion.content_hash == task.content_hash,
            )
        )
    ).scalar_one_or_none()
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


async def seed_tasks(
    session: AsyncSession, roots: list[Path], *, publish: bool = True
) -> list[TaskVersion]:
    versions: list[TaskVersion] = []
    for root in roots:
        for directory in discover_tasks(root):
            try:
                versions.append(await register_task(session, load_task(directory), publish=publish))
            except Exception as exc:
                logger.warning("skipping %s: %s", directory.name, exc)
    return versions


async def ensure_suite(
    session: AsyncSession,
    slug: str,
    name: str,
    versions: list[TaskVersion],
    *,
    description: str | None = None,
) -> Suite:
    suite = (await session.execute(select(Suite).where(Suite.slug == slug))).scalar_one_or_none()
    if suite is None:
        suite = Suite(slug=slug, name=name, description=description)
        session.add(suite)
        await session.flush()

    existing = set(
        (
            await session.execute(
                select(SuiteMember.task_version_id).where(SuiteMember.suite_id == suite.id)
            )
        )
        .scalars()
        .all()
    )
    for version in versions:
        if version.id not in existing:
            session.add(SuiteMember(suite_id=suite.id, task_version_id=version.id))
    return suite


async def bootstrap(session: AsyncSession, settings: Settings) -> dict[str, int]:
    """Everything a fresh install needs to run its first suite."""
    agents = await seed_agents(session)
    models = await seed_models(session)

    reference = await seed_tasks(session, [settings.tasks_dir])
    fixtures = await seed_tasks(
        session, [Path.cwd() / "tests" / "fixtures" / "tasks"], publish=True
    )

    if reference:
        await ensure_suite(
            session,
            "core-reference",
            "Core reference suite",
            reference,
            description="The tasks used for harness benchmarking and regression gating.",
        )
    if fixtures:
        # Only the fixtures that are *meant* to calibrate cleanly. The negative
        # ones exist to be rejected, and a suite containing them would report a
        # permanently depressed score for no reason.
        healthy = [v for v in fixtures if v.task_id not in _NEGATIVE_FIXTURES]
        await ensure_suite(
            session,
            "fixtures",
            "Harness fixtures",
            healthy,
            description="The harness's own unit tests, as tasks.",
        )

    return {
        "agents": agents,
        "models": models,
        "tasks": len(reference) + len(fixtures),
        "reference_suite": len(reference),
    }


#: These fail their gates by design; see each one's README.
_NEGATIVE_FIXTURES = frozenset({"always-fail", "bad-output-grading", "opens-socket"})
