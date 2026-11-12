"""The lease protocol and the REST surface.

The lease tests are the crash-recovery contract: a worker that dies must lose
its trials to the reaper, and a worker that is merely slow must keep them.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autobench.core.errors import ErrorClass
from autobench.core.ids import utcnow
from autobench.db.models import Run, RunStatus, Trial, TrialStatus, Worker
from autobench.worker.planner import TaskEntry, estimate_makespan, plan_run
from autobench.worker.queue import (
    cancel_run_trials,
    lease_trials,
    queue_depth,
    reap_expired,
    renew_lease,
    requeue_infra_failures,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# planner
# ---------------------------------------------------------------------------


def test_plan_expands_suite_times_k() -> None:
    entries = [
        TaskEntry(uuid.uuid4(), f"task-{i}", agent_timeout_sec=100 * (i + 1)) for i in range(10)
    ]
    plan = plan_run(uuid.uuid4(), entries, k=5, run_seed=42)
    assert plan.total == 50
    assert len({t.id for t in plan.trials}) == 50


def test_plan_is_longest_first_within_a_wave() -> None:
    """LPT: the long task must not be left running alone at the end."""
    entries = [
        TaskEntry(uuid.uuid4(), "short", agent_timeout_sec=60),
        TaskEntry(uuid.uuid4(), "long", agent_timeout_sec=900),
        TaskEntry(uuid.uuid4(), "medium", agent_timeout_sec=300),
    ]
    plan = plan_run(uuid.uuid4(), entries, k=1, run_seed=1)
    order = [t.task_id for t in sorted(plan.trials, key=lambda t: -t.priority)]
    assert order == ["long", "medium", "short"]


def test_attempts_are_spread_not_adjacent() -> None:
    """All of attempt 0 outranks all of attempt 1, so progress samples the suite."""
    entries = [TaskEntry(uuid.uuid4(), f"t{i}", agent_timeout_sec=100) for i in range(4)]
    plan = plan_run(uuid.uuid4(), entries, k=3, run_seed=1)
    ordered = sorted(plan.trials, key=lambda t: -t.priority)
    assert [t.attempt_index for t in ordered[:4]] == [0, 0, 0, 0]
    assert [t.attempt_index for t in ordered[4:8]] == [1, 1, 1, 1]


def test_observed_p50_beats_the_declared_timeout() -> None:
    entry = TaskEntry(uuid.uuid4(), "t", agent_timeout_sec=900, observed_p50_ms=4_000)
    assert entry.expected_duration_ms == 4_000
    unseen = TaskEntry(uuid.uuid4(), "t", agent_timeout_sec=900)
    assert unseen.expected_duration_ms == 900_000


def test_seeds_are_derived_and_distinct_per_attempt() -> None:
    entries = [TaskEntry(uuid.uuid4(), "t", agent_timeout_sec=100)]
    plan = plan_run(uuid.uuid4(), entries, k=4, run_seed=7)
    seeds = [t.seed for t in plan.trials]
    assert len(set(seeds)) == 4


def test_makespan_estimate_respects_the_lower_bound() -> None:
    entries = [TaskEntry(uuid.uuid4(), f"t{i}", 100, observed_p50_ms=10_000) for i in range(16)]
    estimate = estimate_makespan(entries, k=1, concurrency=8)
    # Cannot finish faster than the work divided by the lanes.
    assert estimate["parallel_lower_bound_sec"] >= 20.0
    assert estimate["lpt_bound_sec"] >= estimate["parallel_lower_bound_sec"]
    assert estimate["sequential_sec"] == 160.0


# ---------------------------------------------------------------------------
# lease / renew / reap
# ---------------------------------------------------------------------------


async def _register_worker(session) -> uuid.UUID:
    """Trial.worker_id has a foreign key, so a worker must exist to lease.

    That constraint is deliberate: a leased trial that points at no worker is
    unattributable, and the reaper could not tell a dead worker from a typo.
    """
    worker = Worker(hostname="test-host", pid=1, version="1.0.0", executor="local", lanes={})
    session.add(worker)
    await session.flush()
    return worker.id


async def _seeded_run(session) -> tuple[Run, list[Trial]]:
    from autobench.cli.bootstrap import bootstrap
    from autobench.core.config import get_settings

    await bootstrap(session, get_settings())
    await session.flush()

    from autobench.db.models import Agent, Model, Suite, SuiteMember, TaskVersion

    suite = (await session.execute(select(Suite).limit(1))).scalar_one()
    agent = (await session.execute(select(Agent).where(Agent.slug == "oracle"))).scalar_one()
    model = (await session.execute(select(Model).limit(1))).scalar_one()
    versions = (
        (
            await session.execute(
                select(TaskVersion)
                .join(SuiteMember, SuiteMember.task_version_id == TaskVersion.id)
                .where(SuiteMember.suite_id == suite.id)
            )
        )
        .scalars()
        .all()
    )

    run = Run(
        suite_id=suite.id,
        agent_id=agent.id,
        model_id=model.id,
        k=1,
        concurrency=4,
        status=RunStatus.RUNNING.value,
        seed=1337,
        config={},
        trials_total=len(versions),
    )
    session.add(run)
    await session.flush()

    trials = [
        Trial(
            run_id=run.id,
            task_version_id=version.id,
            attempt_index=0,
            seed=1337 + index,
            priority=100 - index,
        )
        for index, version in enumerate(versions)
    ]
    session.add_all(trials)
    await session.commit()
    return run, trials


async def test_lease_claims_highest_priority_first(session) -> None:
    run, _ = await _seeded_run(session)
    worker = await _register_worker(session)
    leased = await lease_trials(session, worker, limit=2, lease_seconds=60, run_id=run.id)
    assert len(leased) == 2

    rows = (
        (await session.execute(select(Trial).where(Trial.id.in_([t.trial_id for t in leased]))))
        .scalars()
        .all()
    )
    assert all(row.status == TrialStatus.LEASED.value for row in rows)
    assert all(row.worker_id == worker for row in rows)


async def test_two_workers_never_get_the_same_trial(session) -> None:
    run, _ = await _seeded_run(session)
    first = await lease_trials(
        session, await _register_worker(session), limit=2, lease_seconds=60, run_id=run.id
    )
    second = await lease_trials(
        session, await _register_worker(session), limit=2, lease_seconds=60, run_id=run.id
    )
    assert not ({t.trial_id for t in first} & {t.trial_id for t in second})


async def test_renewal_keeps_a_slow_worker_s_trial(session) -> None:
    run, _ = await _seeded_run(session)
    worker = await _register_worker(session)
    leased = await lease_trials(session, worker, limit=1, lease_seconds=60, run_id=run.id)
    assert await renew_lease(session, leased[0].trial_id, worker, lease_seconds=120)
    # A different worker cannot renew someone else's lease.
    other = await _register_worker(session)
    assert not await renew_lease(session, leased[0].trial_id, other, lease_seconds=120)


async def test_reaper_requeues_an_expired_lease(session) -> None:
    """A SIGKILLed worker renews nothing; its trial must come back."""
    run, _ = await _seeded_run(session)
    leased = await lease_trials(
        session, await _register_worker(session), limit=1, lease_seconds=60, run_id=run.id
    )

    trial = await session.get(Trial, leased[0].trial_id)
    trial.lease_expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()

    requeued, abandoned = await reap_expired(session, max_attempts=3)
    assert (requeued, abandoned) == (1, 0)

    await session.refresh(trial)
    assert trial.status == TrialStatus.QUEUED.value
    assert trial.worker_id is None
    assert trial.attempt_count == 1


async def test_reaper_abandons_after_the_attempt_limit(session) -> None:
    run, _ = await _seeded_run(session)
    leased = await lease_trials(
        session, await _register_worker(session), limit=1, lease_seconds=60, run_id=run.id
    )

    trial = await session.get(Trial, leased[0].trial_id)
    trial.attempt_count = 2
    trial.lease_expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()

    requeued, abandoned = await reap_expired(session, max_attempts=3)
    assert (requeued, abandoned) == (0, 1)

    await session.refresh(trial)
    assert trial.error_class == ErrorClass.INFRA_ABANDONED.value
    assert trial.status == TrialStatus.ERROR.value
    await session.refresh(run)
    assert run.trials_done == 1, "An abandoned trial must count as terminal progress"


async def test_retry_failed_never_re_rolls_an_agent_failure(session) -> None:
    """Re-rolling agent failures until they pass is how a benchmark inflates."""
    run, trials = await _seeded_run(session)

    infra = await session.get(Trial, trials[0].id)
    infra.status = TrialStatus.ERROR.value
    infra.error_class = ErrorClass.INFRA_DOCKER.value

    agent = await session.get(Trial, trials[1].id)
    agent.status = TrialStatus.TIMEOUT.value
    agent.error_class = ErrorClass.AGENT_TIMEOUT.value
    await session.commit()

    requeued = await requeue_infra_failures(session, run.id)
    assert requeued == 1

    await session.refresh(infra)
    await session.refresh(agent)
    assert infra.status == TrialStatus.QUEUED.value
    assert agent.status == TrialStatus.TIMEOUT.value, "agent failures are final"


async def test_cancel_marks_everything_unfinished(session) -> None:
    run, trials = await _seeded_run(session)
    done = await session.get(Trial, trials[0].id)
    done.status = TrialStatus.SCORED.value
    await session.commit()

    cancelled = await cancel_run_trials(session, run.id)
    assert cancelled == len(trials) - 1

    await session.refresh(done)
    assert done.status == TrialStatus.SCORED.value, "finished trials are left alone"


async def test_queue_depth_reports_by_status(session) -> None:
    run, _ = await _seeded_run(session)
    depth = await queue_depth(session, run.id)
    assert depth.get(TrialStatus.QUEUED.value, 0) > 0


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(session):
    from autobench.api.main import create_app
    from autobench.cli.bootstrap import bootstrap
    from autobench.core.config import get_settings

    await bootstrap(session, get_settings())
    await session.commit()

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as instance:
        yield instance


async def test_health_and_readiness(client) -> None:
    assert (await client.get("/healthz")).status_code == 200
    body = (await client.get("/readyz")).json()
    assert body["checks"]["database"] == "ok"


async def test_openapi_generates(client) -> None:
    """The frontend's types are generated from this; it must not 500."""
    spec = (await client.get("/openapi.json")).json()
    assert "/v1/runs" in spec["paths"]
    assert "/v1/runs/{run_id}/matrix" in spec["paths"]


async def test_catalogue_endpoints(client) -> None:
    tasks = (await client.get("/v1/tasks")).json()
    assert any(task["id"] == "hello-world" for task in tasks)

    agents = (await client.get("/v1/agents")).json()
    assert {"oracle", "null", "react"} <= {agent["slug"] for agent in agents}


async def test_launch_returns_202_without_waiting(client) -> None:
    """Launching a run must never block on executing one."""
    response = await client.post(
        "/v1/runs",
        json={"suite": "core-reference", "agent": "oracle", "model": "mock/mock-1", "k": 2},
    )
    assert response.status_code == 202
    assert "Location" in response.headers

    run = response.json()
    assert run["status"] == "queued"
    assert run["trials_total"] > 0

    matrix = (await client.get(f"/v1/runs/{run['id']}/matrix")).json()
    assert matrix["k"] == 2
    assert all(cell["status"] == "queued" for row in matrix["rows"] for cell in row["cells"])


async def test_unknown_run_is_problem_json(client) -> None:
    response = await client.get(f"/v1/runs/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "title" in response.json()


async def test_validation_errors_do_not_echo_input(client) -> None:
    """A 422 must not contain the value someone put in the wrong field."""
    response = await client.post(
        "/v1/runs",
        json={"suite": "core-reference", "agent": "oracle", "k": "sk-ant-secret-value"},
    )
    assert response.status_code == 422
    assert "sk-ant-secret-value" not in response.text


async def test_cancel_is_idempotent_about_terminal_runs(client) -> None:
    run = (
        await client.post(
            "/v1/runs",
            json={"suite": "core-reference", "agent": "oracle", "model": "mock/mock-1", "k": 1},
        )
    ).json()
    assert (await client.post(f"/v1/runs/{run['id']}/cancel")).status_code == 200
    # Cancelling twice is a conflict, not a second cancel.
    assert (await client.post(f"/v1/runs/{run['id']}/cancel")).status_code == 409
