"""The trial pipeline against a real executor.

These are the tests that would catch a regression in the grading contract:
calibration, post-hoc test injection, freeze semantics, teardown, and the
reproducibility guarantee.
"""

from __future__ import annotations

import uuid

import pytest

from autobench.core.errors import ErrorClass
from autobench.db.models import TrialStatus
from autobench.worker.pipeline import TrialRequest

pytestmark = pytest.mark.integration


def _request(task, agent: str = "oracle", seed: int = 1337) -> TrialRequest:
    return TrialRequest(trial_id=str(uuid.uuid4()), task=task, agent_slug=agent, seed=seed)


# ---------------------------------------------------------------------------
# calibration — the gates that decide whether a task may be published
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "task_name",
    ["always-pass", "partial-credit", "deletes-own-tests", "hello-world", "fix-flaky-retry"],
)
async def test_oracle_scores_exactly_one(pipeline, fixture_task, task_name: str) -> None:
    outcome = await pipeline.run(_request(fixture_task(task_name)))
    assert outcome.status is TrialStatus.SCORED
    assert outcome.report.reward == 1.0, outcome.report.summary()
    assert outcome.report.binary_pass


@pytest.mark.parametrize(
    "task_name",
    ["always-pass", "partial-credit", "hello-world", "parse-malformed-csv"],
)
async def test_null_agent_scores_exactly_zero(pipeline, fixture_task, task_name: str) -> None:
    """Anything above zero means the starting state already satisfies the grader."""
    outcome = await pipeline.run(_request(fixture_task(task_name), agent="null"))
    assert outcome.report.reward == 0.0
    assert not outcome.report.binary_pass


async def test_always_fail_is_unsolvable_even_by_the_oracle(pipeline, fixture_task) -> None:
    """The negative fixture: its `oracle` gate is expected to fail."""
    outcome = await pipeline.run(_request(fixture_task("always-fail")))
    assert outcome.report.reward == 0.0


async def test_partial_credit_is_weighted(pipeline, fixture_task) -> None:
    outcome = await pipeline.run(_request(fixture_task("partial-credit")))
    weights = {item.id: item.weight for item in outcome.report.items}
    assert weights == {"alpha": 0.5, "beta": 0.3, "gamma": 0.2}


# ---------------------------------------------------------------------------
# the grading contract
# ---------------------------------------------------------------------------


async def test_agent_cannot_delete_the_tests(pipeline, fixture_task) -> None:
    """Post-hoc injection: the tests are not in the agent's world to delete.

    The fixture's solution runs `rm -rf /opt/tests` and then does the work. It
    must still be graded normally, because the verifier is a separate container
    and the tests arrive only after the freeze.
    """
    outcome = await pipeline.run(_request(fixture_task("deletes-own-tests")))
    assert outcome.report.reward == 1.0
    assert outcome.report.items[0].id == "graded_despite_tampering"


async def test_tests_are_absent_during_the_agent_phase(pipeline, fixture_task) -> None:
    """Assert the negative directly rather than trusting the fixture."""
    from autobench.agents import build_agent
    from autobench.agents.base import AgentContext
    from autobench.executor import SandboxSpec, get_provider

    task = fixture_task("always-pass")
    provider = get_provider()
    spec = SandboxSpec.from_task(
        task,
        await provider.ensure_image(task),
        labels={"autobench.seed_dir": str(task.root / "environment" / "seed")},
    )
    sandbox = await provider.create(spec)
    try:
        context = AgentContext(
            task=task,
            sandbox=sandbox,
            instruction=task.instruction,
            seed=1337,
            trial_id="probe",
        )
        await build_agent("null").run(context)
        listing = await sandbox.exec("ls /opt/tests 2>&1 || true", timeout=30)
        assert "check.py" not in listing.stdout
    finally:
        await sandbox.destroy()


async def test_freeze_stops_further_execution(pipeline, fixture_task) -> None:
    from autobench.core.errors import SandboxError
    from autobench.executor import SandboxSpec, get_provider

    task = fixture_task("always-pass")
    provider = get_provider()
    spec = SandboxSpec.from_task(task, await provider.ensure_image(task))
    sandbox = await provider.create(spec)
    try:
        await sandbox.freeze()
        with pytest.raises(SandboxError, match="frozen"):
            await sandbox.exec("echo late", timeout=10)
        with pytest.raises(SandboxError, match="frozen"):
            await sandbox.write_file("/app/late.txt", "late")
    finally:
        await sandbox.destroy()


async def test_diff_captures_what_the_agent_changed(pipeline, fixture_task) -> None:
    outcome = await pipeline.run(_request(fixture_task("always-pass")))
    assert outcome.diff is not None
    added = [f.path for f in outcome.diff.files if f.change == "added"]
    assert "done.txt" in added
    # Harness scratch is not the agent's diff.
    assert not any(path.startswith(".autobench-") for path in added)


# ---------------------------------------------------------------------------
# reproducibility — the M1 exit gate
# ---------------------------------------------------------------------------


async def test_same_task_and_seed_produce_an_identical_report(pipeline, fixture_task) -> None:
    task = fixture_task("partial-credit")
    fingerprints = set()
    for _ in range(5):
        outcome = await pipeline.run(_request(task))
        fingerprints.add(
            (
                outcome.report.reward,
                outcome.report.binary_pass,
                tuple((i.id, i.passed, i.weight) for i in outcome.report.items),
            )
        )
    assert len(fingerprints) == 1, "five oracle runs must agree exactly"


async def test_trials_leave_nothing_behind(pipeline, fixture_task, settings) -> None:
    """Container-leak count must be zero; a leaking harness fills the disk."""
    from autobench.executor import get_provider

    task = fixture_task("always-pass")
    for _ in range(10):
        await pipeline.run(_request(task))

    leaked = await get_provider().list_leaked_resources(task.id)
    assert leaked == [], f"leaked: {leaked}"


# ---------------------------------------------------------------------------
# agent limits — final, never retried
# ---------------------------------------------------------------------------


async def test_agent_timeout_is_scored_not_retried(pipeline, fixture_task) -> None:
    from autobench.agents.base import AgentAdapter, AgentContext, AgentResult
    from autobench.core.errors import AgentTimeout

    class HangingAgent(AgentAdapter):
        slug = "hanging"
        needs_model = False

        async def run(self, context: AgentContext) -> AgentResult:
            raise AgentTimeout("agent exceeded its budget")

    from autobench.agents import ADAPTERS

    ADAPTERS["hanging"] = HangingAgent
    try:
        outcome = await pipeline.run(_request(fixture_task("always-pass"), agent="hanging"))
    finally:
        ADAPTERS.pop("hanging", None)

    assert outcome.error_class is ErrorClass.AGENT_TIMEOUT
    assert outcome.status is TrialStatus.TIMEOUT
    assert outcome.report.reward == 0.0
    assert not outcome.retryable, "an agent failure is the result, not a retry"


async def test_infra_failure_is_retryable_and_unscored(pipeline, fixture_task) -> None:
    from autobench.agents import ADAPTERS
    from autobench.agents.base import AgentAdapter, AgentContext, AgentResult
    from autobench.core.errors import TrialError

    class BrokenInfraAgent(AgentAdapter):
        slug = "broken-infra"
        needs_model = False

        async def run(self, context: AgentContext) -> AgentResult:
            raise TrialError("the daemon fell over", ErrorClass.INFRA_DOCKER, stage="agent")

    ADAPTERS["broken-infra"] = BrokenInfraAgent
    try:
        outcome = await pipeline.run(_request(fixture_task("always-pass"), agent="broken-infra"))
    finally:
        ADAPTERS.pop("broken-infra", None)

    assert outcome.error_class is ErrorClass.INFRA_DOCKER
    assert outcome.status is TrialStatus.ERROR
    assert outcome.retryable


# ---------------------------------------------------------------------------
# validator, end to end
# ---------------------------------------------------------------------------


async def test_validator_calibrates_a_good_task(fixture_task) -> None:
    from autobench.taskspec.validator import validate_task
    from autobench.worker.pipeline import PipelineCalibrationRunner

    report = await validate_task(fixture_task("always-pass"), runner=PipelineCalibrationRunner())
    assert report.oracle_reward == 1.0
    assert report.null_reward == 0.0
    assert report.ok, [g.detail for g in report.failed]
    assert report.publishable


async def test_validator_refuses_the_negative_fixture(fixture_task) -> None:
    """bad-output-grading must fail exactly the state-based and tolerance gates."""
    from autobench.taskspec.validator import validate_task

    report = await validate_task(fixture_task("bad-output-grading"))
    failed = {gate.gate for gate in report.failed}
    assert failed == {"state-based", "tolerance"}
    assert not report.publishable


async def test_skipped_gates_are_not_passed_gates(fixture_task) -> None:
    from autobench.taskspec.validator import validate_task

    report = await validate_task(fixture_task("always-pass"))
    assert report.ok, "static lints are clean"
    assert not report.publishable, "but calibration never ran"
    assert {g.gate for g in report.skipped} >= {"oracle", "null", "hermetic", "determinism"}
