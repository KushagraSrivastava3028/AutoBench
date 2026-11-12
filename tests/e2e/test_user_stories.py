"""The five user stories from the PRD, end to end.

Author -> validate -> run -> inspect -> compare -> gate, through the real
pipeline and the real API, with no Docker daemon and no API key.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from autobench.core.config import get_settings
from autobench.db.models import RunStatus

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
async def stack(session):
    """A seeded database plus an API client sharing it."""
    from autobench.api.main import create_app
    from autobench.cli.bootstrap import bootstrap

    counts = await bootstrap(session, get_settings())
    await session.commit()
    assert counts["tasks"] > 0

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _drain(run_id: uuid.UUID) -> None:
    """Run a worker until the queue for this run is empty."""
    from autobench.worker.runner import TrialWorker

    worker = TrialWorker(get_settings())
    try:
        await worker.run_forever(run_id=run_id, drain=True)
    finally:
        await worker.shutdown()


# ---------------------------------------------------------------------------
# story 1 — an author pushes a task and Autobench validates it
# ---------------------------------------------------------------------------


async def test_story_author_validates_a_task(fixture_task) -> None:
    from autobench.taskspec.validator import validate_task
    from autobench.worker.pipeline import PipelineCalibrationRunner

    report = await validate_task(fixture_task("hello-world"), runner=PipelineCalibrationRunner())

    assert report.publishable
    assert report.oracle_reward == 1.0
    assert report.null_reward == 0.0
    statuses = {gate.gate: gate.status.value for gate in report.gates}
    assert statuses["oracle"] == "passed"
    assert statuses["null"] == "passed"
    assert statuses["state-based"] == "passed"
    assert statuses["determinism"] == "passed"


# ---------------------------------------------------------------------------
# stories 2 and 3 — launch a run, watch it, inspect a failure
# ---------------------------------------------------------------------------


async def test_story_launch_watch_and_inspect(stack) -> None:
    client = stack

    launch = await client.post(
        "/v1/runs",
        json={
            "suite": "core-reference",
            "agent": "oracle",
            "model": "mock/mock-1",
            "k": 3,
            "concurrency": 4,
            "label": "e2e oracle",
        },
    )
    assert launch.status_code == 202
    run = launch.json()
    run_id = uuid.UUID(run["id"])
    assert run["trials_total"] == run["k"] * 4  # four reference tasks

    await _drain(run_id)

    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    assert detail["status"] == RunStatus.COMPLETED.value
    assert detail["trials_done"] == detail["trials_total"]

    metrics = detail["metrics"]
    # The oracle replays each task's own solution, so a perfect score here is
    # the harness certifying itself rather than a model result.
    assert metrics["pass_at_1"]["point"] == 1.0
    assert metrics["pass_caret_k"]["point"] == 1.0
    assert metrics["reliability_gap"] == 0.0
    assert metrics["reportable"], "k=3 is reportable"
    assert metrics["infra_errors"] == 0

    # Every aggregate carries an interval.
    for key in ("pass_at_1", "pass_at_k", "pass_caret_k"):
        assert set(metrics[key]) >= {"point", "low", "high", "margin", "method"}

    matrix = (await client.get(f"/v1/runs/{run_id}/matrix")).json()
    assert len(matrix["rows"]) == 4
    assert all(len(row["cells"]) == 3 for row in matrix["rows"])

    # Story 3: drill into a trial and get everything needed to diagnose it.
    trial_id = matrix["rows"][0]["cells"][0]["trial_id"]

    trial = (await client.get(f"/v1/trials/{trial_id}")).json()
    assert trial["score"]["binary_pass"] is True
    assert trial["score"]["setup_ms"] is not None

    trajectory = (await client.get(f"/v1/trials/{trial_id}/trajectory")).json()
    assert trajectory["total"] > 0
    kinds = {event["kind"] for event in trajectory["events"]}
    assert {"tool_call", "tool_result", "verdict"} <= kinds

    diff = (await client.get(f"/v1/trials/{trial_id}/diff")).json()
    # Some tasks are solved by adding a file, others by modifying one. What
    # matters is that the agent changed the world at all — a passing trial with
    # an empty diff would mean the task was already solved at setup.
    assert sum(diff["counts"].values()) >= 1, diff["counts"]
    assert diff["files"], "a passing trial must show what it changed"

    replay = (await client.get(f"/v1/trials/{trial_id}/replay")).json()
    assert any("docker run" in command for command in replay["commands"])

    artifacts = (await client.get(f"/v1/trials/{trial_id}/artifacts")).json()
    assert {a["kind"] for a in artifacts} >= {"trajectory", "report"}


# ---------------------------------------------------------------------------
# story 4 — compare two runs
# ---------------------------------------------------------------------------


async def test_story_compare_two_runs(stack) -> None:
    client = stack
    run_ids = []

    for label in ("baseline", "candidate"):
        run = (
            await client.post(
                "/v1/runs",
                json={
                    "suite": "core-reference",
                    "agent": "oracle",
                    "model": "mock/mock-1",
                    "k": 2,
                    "label": label,
                },
            )
        ).json()
        await _drain(uuid.UUID(run["id"]))
        run_ids.append(run["id"])

    comparison = (await client.get(f"/v1/stats/compare?a={run_ids[0]}&b={run_ids[1]}")).json()

    assert comparison["shared_tasks"] == 4
    # The oracle is deterministic, so two runs of it must agree exactly. Any
    # flip here would mean the harness itself is nondeterministic.
    assert comparison["flips"] == []
    assert comparison["delta"]["point"] == 0.0
    assert comparison["inconclusive"] is True

    board = (await client.get("/v1/stats/leaderboard?suite=core-reference")).json()
    assert board and board[0]["pass_at_1"]["point"] == 1.0


# ---------------------------------------------------------------------------
# story 5 — CI gates on a regression
# ---------------------------------------------------------------------------


async def test_story_gate_blocks_a_seeded_regression(stack) -> None:
    from autobench.api.services import run_outcomes
    from autobench.scoring.aggregate import compare_runs, gate_regression

    client = stack
    run = (
        await client.post(
            "/v1/runs",
            json={"suite": "core-reference", "agent": "oracle", "model": "mock/mock-1", "k": 3},
        )
    ).json()
    await _drain(uuid.UUID(run["id"]))

    from autobench.db.session import session_scope

    async with session_scope() as active:
        baseline = await run_outcomes(active, uuid.UUID(run["id"]))

    assert baseline, "the baseline must have outcomes to compare against"

    # Seed a regression: half the tasks stop solving.
    regressed = []
    for index, outcome in enumerate(baseline):
        copy = type(outcome)(
            task_id=outcome.task_id,
            attempts=outcome.attempts,
            passes=0 if index % 2 == 0 else outcome.passes,
            rewards=list(outcome.rewards),
        )
        regressed.append(copy)

    comparison = compare_runs(baseline, regressed, resamples=1_000)
    verdict = gate_regression(comparison, max_regression=0.03)

    assert not verdict.passed
    assert verdict.exit_code == 1
    assert len(verdict.regressed_tasks) == len(baseline) // 2 + len(baseline) % 2

    # And the same gate passes when nothing moved.
    clean = gate_regression(compare_runs(baseline, baseline, resamples=1_000))
    assert clean.passed
    assert clean.exit_code == 0


# ---------------------------------------------------------------------------
# the export path a research lead uses
# ---------------------------------------------------------------------------


async def test_run_exports_as_csv(stack) -> None:
    client = stack
    run = (
        await client.post(
            "/v1/runs",
            json={"suite": "core-reference", "agent": "oracle", "model": "mock/mock-1", "k": 1},
        )
    ).json()
    await _drain(uuid.UUID(run["id"]))

    response = await client.get(f"/v1/export/{run['id']}.csv")
    assert response.status_code == 200
    header, *rows = response.text.strip().splitlines()
    assert "task_id" in header and "binary_pass" in header and "content_hash" in header
    assert len(rows) == 4
