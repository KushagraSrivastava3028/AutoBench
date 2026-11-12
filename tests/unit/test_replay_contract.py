import shlex

from autobench.api.schemas import TrialOut
from autobench.executor.base import SandboxSpec
from autobench.executor.docker_provider import replay_commands


def test_replay_preserves_trial_seed_and_grades_the_captured_workspace(fixture_task):
    task = fixture_task("hello-world")
    seed = 8_385_703_829_685_637_123
    spec = SandboxSpec.from_task(task, "autobench/replay:test", seed=seed)
    commands = replay_commands(task, spec.image, spec, trial_id="9f2c8a1e")
    launches = [shlex.split(command) for command in commands if command.startswith("docker run ")]
    assert len(launches) == 2
    agent, verifier = launches
    assert f"AUTOBENCH_SEED={seed}" in agent
    assert f"AUTOBENCH_SEED={seed}" in verifier
    assert f"PYTHONHASHSEED={seed % 2**32}" in verifier
    assert "--rm" not in agent, (
        "The stopped agent container must survive long enough to capture its state"
    )
    assert any(command.startswith("docker cp ") for command in commands)
    assert "$REPLAY_DIR:/app:ro" in verifier
    assert verifier[-1] == task.spec.verifier.command, (
        "The test command must expand environment variables inside the verifier"
    )
    build = shlex.split(
        next(command for command in commands if command.startswith("docker build "))
    )
    assert build[-1] == task.root.as_posix(), (
        "Spaces in the checkout path must survive shell parsing"
    )


def test_trial_seed_has_a_lossless_wire_representation():
    seed = 8_385_703_829_685_637_123
    trial = TrialOut(
        id="00000000-0000-0000-0000-000000000001",
        run_id="00000000-0000-0000-0000-000000000002",
        task_id="hello-world",
        task_version_id="00000000-0000-0000-0000-000000000003",
        attempt_index=0,
        status="queued",
        seed=seed,
        attempt_count=0,
        container_id=None,
        error_class=None,
        error_detail=None,
        started_at=None,
        finished_at=None,
        duration_ms=None,
    )
    assert trial.model_dump(mode="json")["seed_exact"] == str(seed)
