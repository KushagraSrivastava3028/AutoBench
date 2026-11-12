"""The eleven publication gates (PRD §10.1).

    schema  image  oracle  null  leak  hermetic  determinism
    tolerance  state-based  timing  cleanup

Any failure blocks publication. That is the whole point: a task where the null
agent scores above 0 has a leak, and a task where the oracle scores below 1.0
has a bug. Neither is allowed into a suite, because both quietly corrupt every
number computed from that suite afterwards.

Gates split into two tiers:

* **Static** gates read the directory. They always run, they are fast, and they
  are what the authoring wizard shows you live as you type.
* **Dynamic** gates need to actually execute the task — oracle, null, hermetic,
  determinism, timing, cleanup. They run when a :class:`CalibrationRunner` is
  supplied and are reported as ``skipped`` otherwise, never as ``passed``.
  A skipped gate is not a passed gate, and ``ValidationReport.publishable``
  knows the difference.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from autobench.scoring.report import ScoreReport
from autobench.taskspec.lints import (
    LintFinding,
    LintResult,
    lint_dockerfile,
    lint_leak,
    lint_security,
    lint_state_based,
    lint_tolerance,
)
from autobench.taskspec.loader import TaskDirectory, load_task
from autobench.taskspec.schema import NetworkMode

#: Oracle wall-clock must fit inside this fraction of the agent's budget.
ORACLE_TIME_BUDGET_FRACTION = 0.4
#: Repeats for the determinism gate: identical report.json, five times.
DETERMINISM_REPEATS = 5
LEAK_THRESHOLD = 0.15


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    #: Needs an executor that was not supplied. Never counts as passed.
    SKIPPED = "skipped"
    #: Advisory. Does not block publication.
    WARNING = "warning"


@dataclass(slots=True)
class GateResult:
    gate: str
    status: GateStatus
    detail: str
    findings: list[LintFinding] = field(default_factory=list)
    duration_ms: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.status is GateStatus.FAILED

    def to_json(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status.value,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "evidence": self.evidence,
            "findings": [
                {
                    "rule": f.rule,
                    "file": f.file,
                    "line": f.line,
                    "message": f.message,
                    "remediation": f.remediation,
                    "severity": f.severity,
                }
                for f in self.findings
            ],
        }


@dataclass(slots=True)
class ValidationReport:
    task_id: str
    content_hash: str
    gates: list[GateResult] = field(default_factory=list)
    oracle_reward: float | None = None
    null_reward: float | None = None

    @property
    def failed(self) -> list[GateResult]:
        return [g for g in self.gates if g.status is GateStatus.FAILED]

    @property
    def skipped(self) -> list[GateResult]:
        return [g for g in self.gates if g.status is GateStatus.SKIPPED]

    @property
    def ok(self) -> bool:
        """No gate failed. Skipped gates do not fail a *lint*."""
        return not self.failed

    @property
    def publishable(self) -> bool:
        """Stricter: nothing failed **and** calibration actually ran.

        ``published = true`` is a claim that the oracle scored 1.0 and the null
        agent scored 0.0 on this exact content hash. You cannot make that claim
        from a static lint.
        """
        return (
            self.ok and not self.skipped and self.oracle_reward == 1.0 and self.null_reward == 0.0
        )

    def gate(self, name: str) -> GateResult | None:
        return next((g for g in self.gates if g.gate == name), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "content_hash": self.content_hash,
            "ok": self.ok,
            "publishable": self.publishable,
            "oracle_reward": self.oracle_reward,
            "null_reward": self.null_reward,
            "gates": [g.to_json() for g in self.gates],
        }


class CalibrationRunner(Protocol):
    """Runs one task with one calibration agent and returns its report.

    Implemented by :mod:`autobench.worker.pipeline`. Kept as a Protocol so the
    validator has no import dependency on the execution plane — which is what
    lets the static gates run in a CI job with no Docker daemon.
    """

    async def __call__(
        self,
        task: TaskDirectory,
        *,
        agent: str,
        seed: int,
        network: NetworkMode | None = None,
    ) -> ScoreReport: ...


class TaskValidator:
    def __init__(
        self,
        *,
        runner: CalibrationRunner | None = None,
        require_digest_pin: bool = True,
        leak_threshold: float = LEAK_THRESHOLD,
    ) -> None:
        self.runner = runner
        self.require_digest_pin = require_digest_pin
        self.leak_threshold = leak_threshold

    async def validate(self, task: TaskDirectory | Path | str) -> ValidationReport:
        if not isinstance(task, TaskDirectory):
            task = load_task(task)

        report = ValidationReport(task_id=task.id, content_hash=task.content_hash)

        # Static tier. Always runs, cheap, and what the authoring wizard shows live.
        report.gates.append(self._gate_schema(task))
        report.gates.append(self._gate_image(task))
        report.gates.append(self._gate_security(task))
        report.gates.append(self._gate_leak(task))
        report.gates.append(self._gate_tolerance(task))
        report.gates.append(self._gate_state_based(task))

        # Dynamic tier. Skipped — never silently passed — without a runner.
        if self.runner is None:
            reason = "no executor supplied; run `autobench validate --deep` to calibrate"
            for name in ("oracle", "null", "hermetic", "determinism", "timing", "cleanup"):
                report.gates.append(GateResult(name, GateStatus.SKIPPED, reason))
            return report

        oracle_gate, oracle_report, oracle_ms = await self._gate_oracle(task)
        report.gates.append(oracle_gate)
        report.oracle_reward = oracle_report.reward if oracle_report else None

        null_gate, null_report = await self._gate_null(task)
        report.gates.append(null_gate)
        report.null_reward = null_report.reward if null_report else None

        report.gates.append(await self._gate_hermetic(task))
        report.gates.append(await self._gate_determinism(task))
        report.gates.append(self._gate_timing(task, oracle_ms))
        report.gates.append(await self._gate_cleanup(task))
        return report

    # --- static gates ------------------------------------------------------

    def _gate_schema(self, task: TaskDirectory) -> GateResult:
        """Parsing already happened; this gate checks the structural requirements."""
        started = time.perf_counter()
        findings: list[LintFinding] = []
        for label, getter in (
            ("test entrypoint", task.test_entrypoint),
            ("oracle solution", task.solution_entrypoint),
        ):
            try:
                getter()
            except Exception as exc:
                findings.append(
                    LintFinding(
                        rule="schema",
                        file="task.toml",
                        line=1,
                        message=str(exc),
                        remediation=f"Add the {label}.",
                    )
                )
        # Verification is meant to be the cheap half of the trial. A verifier
        # budget larger than the agent's usually means one of the two numbers is
        # a copy-paste, but a deliberately tiny agent budget is legitimate — so
        # this is a warning the author can justify, not a refusal.
        if task.spec.verifier.timeout_sec > task.spec.agent.timeout_sec:
            findings.append(
                LintFinding(
                    rule="schema",
                    file="task.toml",
                    line=1,
                    message=(
                        f"verifier.timeout_sec ({task.spec.verifier.timeout_sec}s) exceeds "
                        f"agent.timeout_sec ({task.spec.agent.timeout_sec}s)."
                    ),
                    remediation="Verification should be cheaper than solving. Check both numbers.",
                    severity="warning",
                )
            )
        if task.spec.environment.network is NetworkMode.FULL:
            findings.append(
                LintFinding(
                    rule="schema",
                    file="task.toml",
                    line=1,
                    message="environment.network='full' cannot be published.",
                    remediation=(
                        "Use 'none' (Mode A) or 'proxy' with an allowlist (Mode B). "
                        "Unrestricted egress makes the trial depend on the internet's "
                        "mood on the day you ran it."
                    ),
                )
            )
        return _from_lint(
            "schema",
            LintResult(findings),
            started,
            ok_detail=f"{task.id} v{task.semver}, schema {task.spec.schema_version}",
        )

    def _gate_image(self, task: TaskDirectory) -> GateResult:
        started = time.perf_counter()
        result = lint_dockerfile(task.dockerfile, require_digest=self.require_digest_pin)
        if task.spec.determinism.pinned_base_digest is None and self.require_digest_pin:
            result.findings.append(
                LintFinding(
                    rule="image",
                    file="task.toml",
                    line=1,
                    message="determinism.pinned_base_digest is unset.",
                    remediation="Record the base image digest so drift is detectable.",
                    severity="warning",
                )
            )
        return _from_lint("image", result, started, ok_detail="base image pinned by digest")

    def _gate_security(self, task: TaskDirectory) -> GateResult:
        """§12.1 prohibitions. Not a warning, not a config key — a hard refusal."""
        started = time.perf_counter()
        sources: dict[str, str] = {}
        for path in (task.dockerfile, task.compose_file):
            if path and path.is_file():
                sources[path.name] = path.read_text(encoding="utf-8", errors="replace")
        result = lint_security(sources)
        return _from_lint(
            "security", result, started, ok_detail="no prohibited sandbox configuration"
        )

    def _gate_leak(self, task: TaskDirectory) -> GateResult:
        started = time.perf_counter()
        tests = task.read_tests()
        result = lint_leak(task.instruction, tests, self.leak_threshold)
        from autobench.taskspec.lints import ngram_overlap

        overlap = ngram_overlap(task.instruction, tests)
        gate = _from_lint(
            "leak", result, started, ok_detail=f"instruction/test 8-gram overlap {overlap:.1%}"
        )
        gate.evidence["overlap"] = round(overlap, 4)
        return gate

    def _gate_tolerance(self, task: TaskDirectory) -> GateResult:
        started = time.perf_counter()
        result = lint_tolerance(task.read_tests())
        return _from_lint(
            "tolerance", result, started, ok_detail="every numeric assertion declares a tolerance"
        )

    def _gate_state_based(self, task: TaskDirectory) -> GateResult:
        started = time.perf_counter()
        result = lint_state_based(task.read_tests())
        return _from_lint(
            "state-based", result, started, ok_detail="tests assert on container state"
        )

    # --- dynamic gates -----------------------------------------------------

    async def _gate_oracle(self, task: TaskDirectory) -> tuple[GateResult, ScoreReport | None, int]:
        started = time.perf_counter()
        assert self.runner is not None
        try:
            report = await self.runner(task, agent="oracle", seed=task.spec.determinism.seed)
        except Exception as exc:
            return (
                GateResult(
                    "oracle", GateStatus.FAILED, f"oracle run failed: {exc}", [], _ms(started)
                ),
                None,
                _ms(started),
            )
        elapsed = _ms(started)
        passed = report.reward == 1.0
        detail = (
            "oracle scores exactly 1.000"
            if passed
            else (
                f"oracle scored {report.reward:.3f}, not 1.000 — the task is unsolvable as "
                f"specified, or solve.sh is stale. {report.summary()}"
            )
        )
        gate = GateResult(
            "oracle",
            GateStatus.PASSED if passed else GateStatus.FAILED,
            detail,
            [],
            elapsed,
            {"reward": report.reward, "items": [i.model_dump() for i in report.failed_items]},
        )
        return gate, report, elapsed

    async def _gate_null(self, task: TaskDirectory) -> tuple[GateResult, ScoreReport | None]:
        started = time.perf_counter()
        assert self.runner is not None
        try:
            report = await self.runner(task, agent="null", seed=task.spec.determinism.seed)
        except Exception as exc:
            return GateResult(
                "null", GateStatus.FAILED, f"null run failed: {exc}", [], _ms(started)
            ), None
        passed = report.reward == 0.0
        detail = (
            "null agent scores exactly 0.000"
            if passed
            else (
                f"null agent scored {report.reward:.3f} without doing anything — the task "
                "leaks. The starting state already satisfies part of the grader."
            )
        )
        gate = GateResult(
            "null",
            GateStatus.PASSED if passed else GateStatus.FAILED,
            detail,
            [],
            _ms(started),
            {
                "reward": report.reward,
                "leaking_items": [i.id for i in report.items if i.passed],
            },
        )
        return gate, report

    async def _gate_hermetic(self, task: TaskDirectory) -> GateResult:
        """Same verdict with the network severed, twice.

        A task that behaves differently without a network has a hidden
        dependency on something outside the image, and that something will be
        down the night before your deadline.
        """
        started = time.perf_counter()
        assert self.runner is not None
        try:
            runs = await asyncio.gather(
                *(
                    self.runner(
                        task,
                        agent="oracle",
                        seed=task.spec.determinism.seed,
                        network=NetworkMode.NONE,
                    )
                    for _ in range(2)
                )
            )
        except Exception as exc:
            return GateResult(
                "hermetic", GateStatus.FAILED, f"hermeticity run failed: {exc}", [], _ms(started)
            )

        rewards = [r.reward for r in runs]
        passed = len(set(rewards)) == 1 and rewards[0] == 1.0
        detail = (
            "identical verdict with the network severed, twice"
            if passed
            else f"network-severed runs disagreed or failed: rewards={rewards}"
        )
        return GateResult(
            "hermetic",
            GateStatus.PASSED if passed else GateStatus.FAILED,
            detail,
            [],
            _ms(started),
            {"rewards": rewards},
        )

    async def _gate_determinism(self, task: TaskDirectory) -> GateResult:
        """Five oracle runs must produce a byte-identical report."""
        started = time.perf_counter()
        assert self.runner is not None
        try:
            runs = await asyncio.gather(
                *(
                    self.runner(task, agent="oracle", seed=task.spec.determinism.seed)
                    for _ in range(DETERMINISM_REPEATS)
                )
            )
        except Exception as exc:
            return GateResult(
                "determinism", GateStatus.FAILED, f"determinism run failed: {exc}", [], _ms(started)
            )

        # duration_sec legitimately differs run to run; everything else must not.
        fingerprints = {_report_fingerprint(r) for r in runs}
        passed = len(fingerprints) == 1
        detail = (
            f"{DETERMINISM_REPEATS} oracle runs produced an identical report"
            if passed
            else (
                f"{len(fingerprints)} distinct reports across {DETERMINISM_REPEATS} runs — "
                "something in the task is nondeterministic (unseeded RNG, wall-clock, "
                "dict ordering, or a race in the test)"
            )
        )
        return GateResult(
            "determinism",
            GateStatus.PASSED if passed else GateStatus.FAILED,
            detail,
            [],
            _ms(started),
            {"distinct_reports": len(fingerprints)},
        )

    def _gate_timing(self, task: TaskDirectory, oracle_ms: int) -> GateResult:
        """Oracle wall-clock < 0.4 x the agent budget.

        The agent needs room to explore, backtrack and be wrong twice. If the
        *known* solution takes most of the budget, timeouts will be measuring
        your host's load average, not the model.
        """
        budget_ms = task.spec.agent.timeout_sec * 1000
        limit = budget_ms * ORACLE_TIME_BUDGET_FRACTION
        passed = oracle_ms <= limit
        detail = (
            f"oracle {oracle_ms / 1000:.1f}s vs agent budget {task.spec.agent.timeout_sec}s "
            f"({oracle_ms / budget_ms:.0%})"
        )
        if not passed:
            detail += (
                f" — over the {ORACLE_TIME_BUDGET_FRACTION:.0%} ceiling. Raise "
                "agent.timeout_sec or make the task smaller."
            )
        return GateResult(
            "timing",
            GateStatus.PASSED if passed else GateStatus.FAILED,
            detail,
            [],
            0,
            {"oracle_ms": oracle_ms, "budget_ms": budget_ms},
        )

    async def _gate_cleanup(self, task: TaskDirectory) -> GateResult:
        """No residual containers, volumes, networks or images after teardown."""
        started = time.perf_counter()
        from autobench.executor import get_provider

        try:
            provider = get_provider()
            leaked = await provider.list_leaked_resources(task.id)
        except Exception as exc:  # pragma: no cover - provider-specific
            return GateResult(
                "cleanup",
                GateStatus.WARNING,
                f"could not inspect leaked resources: {exc}",
                [],
                _ms(started),
            )
        passed = not leaked
        detail = (
            "no residual containers, volumes or networks"
            if passed
            else f"leaked {len(leaked)} resource(s): {', '.join(leaked[:5])}"
        )
        return GateResult(
            "cleanup",
            GateStatus.PASSED if passed else GateStatus.FAILED,
            detail,
            [],
            _ms(started),
            {"leaked": leaked},
        )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _from_lint(gate: str, result: LintResult, started: float, *, ok_detail: str) -> GateResult:
    """Turn lint findings into a gate verdict.

    Warnings are surfaced but never block: an author who has a good reason for
    an unpinned apt install should be able to say so in review, not be stopped
    by a linter with no context.
    """
    errors = result.errors
    warnings = [f for f in result.findings if f.severity == "warning"]
    if errors:
        status = GateStatus.FAILED
        detail = f"{len(errors)} blocking finding(s): {errors[0].message}"
    elif warnings:
        status = GateStatus.WARNING
        detail = f"{ok_detail} ({len(warnings)} warning(s))"
    else:
        status = GateStatus.PASSED
        detail = ok_detail
    return GateResult(gate, status, detail, result.findings, _ms(started))


def _report_fingerprint(report: ScoreReport) -> str:
    """Everything that must be stable, and nothing that legitimately is not."""
    import json

    payload = {
        "reward": report.reward,
        "binary_pass": report.binary_pass,
        "items": sorted(
            (
                {"id": i.id, "passed": i.passed, "weight": i.weight, "detail": i.detail}
                for i in report.items
            ),
            key=lambda i: str(i["id"]),
        ),
    }
    return json.dumps(payload, sort_keys=True)


async def validate_task(
    task: TaskDirectory | Path | str,
    *,
    runner: CalibrationRunner | None = None,
    require_digest_pin: bool = True,
) -> ValidationReport:
    """Convenience wrapper — the form the CLI and the API both call."""
    return await TaskValidator(runner=runner, require_digest_pin=require_digest_pin).validate(task)
