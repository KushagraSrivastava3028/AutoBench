"""Run the verifier and parse its report.

This module is where the grading contract is actually enforced, so it is worth
being explicit about the sequence:

1. The agent's container is **frozen** — paused, no further execution.
2. A **separate, clean** container is created from the verifier image.
3. The frozen workspace is copied in.
4. ``tests/`` is mounted **read-only**, for the first time in the trial's life.
5. The tests run with no network and their own timeout.
6. The JSON report is read from the container. The exit code is a fallback only.

Step 4 is the one that matters. The agent could not read the tests, could not
modify them, and could not overfit to them, because during its phase they did
not exist inside its world. The ``deletes-own-tests`` fixture asserts this on
every CI run: an agent that runs ``rm -rf /opt/tests`` still gets graded normally.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from autobench.core.errors import ErrorClass, TrialError, VerifierError
from autobench.core.ids import hash_seed
from autobench.executor.base import Sandbox, SandboxProvider, SandboxSpec
from autobench.scoring.report import ScoreReport
from autobench.taskspec.loader import TaskDirectory
from autobench.taskspec.schema import NetworkMode

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VerificationResult:
    report: ScoreReport
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


async def verify(
    provider: SandboxProvider,
    task: TaskDirectory,
    agent_sandbox: Sandbox,
    *,
    verifier_image: str | None = None,
) -> VerificationResult:
    """Grade the frozen container state. Never the agent's words."""
    spec = task.spec.verifier
    started = time.perf_counter()

    tests_dir = task.tests_dir
    if not tests_dir.is_dir():
        raise VerifierError(f"{task.id}: tests/ directory is missing")

    verifier_spec = SandboxSpec(
        image=verifier_image or spec.image,
        workdir=task.spec.environment.workdir,
        network=NetworkMode.NONE,
        cpus=task.spec.environment.cpus,
        memory=task.spec.environment.memory,
        pids_limit=task.spec.environment.pids_limit,
        tmpfs_size=task.spec.environment.tmpfs_size,
        env={**_verifier_env(task), **spec.env},
        labels={"autobench.task": task.id, "autobench.role": "verifier"},
    )

    sandbox: Sandbox | None = None
    try:
        sandbox = await provider.create_verifier(
            verifier_spec,
            tests_dir=tests_dir,
            tests_mount=spec.tests_mount,
            workspace_from=agent_sandbox,
        )

        result = await sandbox.exec(spec.command, timeout=spec.timeout_sec)

        if result.timed_out:
            # A verifier timeout is the harness's problem, not the agent's: the
            # trial is retried rather than scored zero. Grading an agent 0.0
            # because the grader hung is how a broken harness becomes a model
            # result.
            raise TrialError(
                f"verifier exceeded {spec.timeout_sec}s:\n{result.combined(2000)}",
                ErrorClass.VERIFIER_CRASH,
                stage="verify",
            )

        report = await _read_report(sandbox, task, result.exit_code, result.stdout)
        return VerificationResult(
            report=report,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    finally:
        if sandbox is not None:
            await sandbox.destroy()


async def _read_report(
    sandbox: Sandbox, task: TaskDirectory, exit_code: int, stdout: str
) -> ScoreReport:
    """Prefer the report file; fall back to stdout; fall back to the exit code."""
    threshold = task.spec.scoring.pass_threshold
    report_path = task.spec.scoring.report_path

    try:
        raw = await sandbox.read_file(report_path)
        report = ScoreReport.parse(raw, pass_threshold=threshold, exit_code=exit_code)
        if report.error is None or report.items:
            return _reconcile(report, task)
    except Exception as exc:
        logger.debug("%s: no report at %s (%s); falling back", task.id, report_path, exc)

    # Some runners print the JSON rather than writing it. Accept that, but only
    # if it parses — a stray "{" in a traceback should not become a score.
    candidate = _last_json_object(stdout)
    if candidate:
        report = ScoreReport.parse(candidate, pass_threshold=threshold, exit_code=exit_code)
        if report.items or report.error is None:
            return _reconcile(report, task)

    if exit_code == 0:
        return ScoreReport(
            reward=1.0,
            binary_pass=True,
            error=(
                f"verifier exited 0 but wrote no report to {report_path}; scored on "
                "the exit code alone, which cannot express partial credit"
            ),
        )
    return ScoreReport.from_failure(
        f"verifier exited {exit_code} and wrote no report to {report_path}"
    )


def _reconcile(report: ScoreReport, task: TaskDirectory) -> ScoreReport:
    """Cross-check the report against the task's declared scoring items.

    A report that grades items the task never declared, or silently omits one
    it did, is a task/test mismatch — usually a rename on one side only. It is
    surfaced rather than swallowed, because the alternative is a score that
    quietly means something different from what the author thinks.
    """
    declared = {item.id for item in task.spec.scoring.items}
    if not declared:
        return report
    reported = {item.id for item in report.items}
    missing = declared - reported
    unexpected = reported - declared
    if missing or unexpected:
        notes = []
        if missing:
            notes.append(f"declared but not graded: {sorted(missing)}")
        if unexpected:
            notes.append(f"graded but not declared: {sorted(unexpected)}")
        report.error = "scoring item mismatch — " + "; ".join(notes)
    return report


def _last_json_object(text: str) -> str | None:
    """Find the last balanced ``{...}`` in the output."""
    depth = 0
    end = -1
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == "}":
            if depth == 0:
                end = index
            depth += 1
        elif char == "{":
            depth -= 1
            if depth == 0 and end != -1:
                return text[index : end + 1]
    return None


def _verifier_env(task: TaskDirectory) -> dict[str, str]:
    det = task.spec.determinism
    env = {
        "AUTOBENCH_SEED": str(det.seed),
        "PYTHONHASHSEED": str(hash_seed(det.seed)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "AUTOBENCH_REPORT_PATH": task.spec.scoring.report_path,
        "AUTOBENCH_TESTS_DIR": task.spec.verifier.tests_mount,
        "AUTOBENCH_WORKDIR": task.spec.environment.workdir,
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
    }
    if det.freeze_time:
        env["AUTOBENCH_FREEZE_TIME"] = det.freeze_time
    return env


def verifier_image_for(task: TaskDirectory, fallback: str) -> str:
    """Optimisation O6: one verifier base per language, not one per task.

    Building a verifier image per task is the single easiest way to spend 20% of
    a run's wall-clock on Docker layers nobody reads.
    """
    declared = task.spec.verifier.image
    return declared or fallback


def tests_fingerprint(tests_dir: Path) -> str:
    """Digest of the test tree. Recorded so a grader change is visible in history."""
    from autobench.taskspec.hashing import hash_task_directory

    return hash_task_directory(tests_dir) if tests_dir.is_dir() else "sha256:absent"
