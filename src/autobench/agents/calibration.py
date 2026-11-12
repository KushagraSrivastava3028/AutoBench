"""The two agents that calibrate every task.

They are not baselines. They are the measuring sticks that tell you whether the
task itself is sound:

``oracle``
    Runs ``solution/solve.sh``. Must score exactly **1.000**. Anything less and
    the task is unsolvable as written, or the solution has gone stale against a
    test that moved.

``null``
    Does nothing at all. Must score exactly **0.000**. Anything more and the
    task leaks: the starting state already satisfies part of the grader, and
    every model's score on that task is inflated by the same amount.

The oracle is also the right agent for ``bench-harness``: it replays a
deterministic script, so every millisecond measured is harness overhead rather
than model sampling noise. That is what makes a speedup number defensible.
"""

from __future__ import annotations

import time

from autobench.agents.base import AgentAdapter, AgentContext, AgentResult
from autobench.core.errors import ErrorClass, TrialError
from autobench.db.models import EventKind, ExecutionMode

SOLUTION_MOUNT = "/tmp/autobench-solution"  # noqa: S108 - inside the sandbox


class OracleAgent(AgentAdapter):
    """Replays the task's own solution. Deterministic, no model, no tokens."""

    slug = "oracle"
    mode = ExecutionMode.HARNESS
    version = "1.0.0"
    needs_model = False

    async def run(self, context: AgentContext) -> AgentResult:
        started = time.monotonic()
        solution = context.task.solution_entrypoint()
        script = solution.read_text(encoding="utf-8")

        target = f"{SOLUTION_MOUNT}/solve.sh"
        await context.sandbox.write_file(target, script, mode=0o755)

        # Referenced through a variable rather than a literal path: the local
        # executor remaps AUTOBENCH_* location variables onto its workspace, so
        # the same command works with and without a container.
        command = 'bash "$AUTOBENCH_SOLUTION_DIR/solve.sh"'
        env = {"AUTOBENCH_SOLUTION_DIR": SOLUTION_MOUNT}

        await context.event(
            EventKind.TOOL_CALL,
            {
                "index": 1,
                "name": "bash",
                "rendered": command,
                "arguments": {"command": command},
            },
        )

        timeout = context.task.spec.agent.timeout_sec
        result = await context.sandbox.exec(command, timeout=timeout, env=env)

        await context.event(
            EventKind.TOOL_RESULT,
            {
                "index": 1,
                "name": "bash",
                "output": f"exit {result.exit_code}\n{result.combined()}",
                "elapsed_ms": result.duration_ms,
            },
        )

        if result.timed_out:
            raise TrialError(
                f"the oracle solution exceeded {timeout}s. Either solve.sh is stuck "
                "or agent.timeout_sec is too tight for this task.",
                ErrorClass.AGENT_TIMEOUT,
                stage="agent",
            )
        if result.exit_code != 0:
            # An oracle that errors is a broken task, not a failed agent — the
            # validator's `oracle` gate is what turns this into a refusal to publish.
            raise TrialError(
                f"solve.sh exited {result.exit_code}:\n{result.combined(2000)}",
                ErrorClass.AGENT_ERROR,
                stage="agent",
            )

        return AgentResult(
            tool_calls=1,
            stop_reason="oracle_complete",
            final_message=result.combined(500),
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class NullAgent(AgentAdapter):
    """Does nothing, on purpose. Any reward above zero is a leak in the task."""

    slug = "null"
    mode = ExecutionMode.HARNESS
    version = "1.0.0"
    needs_model = False

    async def run(self, context: AgentContext) -> AgentResult:
        await context.event(
            EventKind.STATUS,
            {"message": "null agent: taking no action, by design"},
        )
        return AgentResult(tool_calls=0, stop_reason="null_complete", duration_ms=0)
