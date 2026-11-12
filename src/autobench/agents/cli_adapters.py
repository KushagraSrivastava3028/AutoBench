"""Mode B adapters: a real agent CLI, running inside the container.

This is the mode that answers "how good is the product", not "how good is the
model". It is also the mode where a hostile or confused agent has both code
execution and a network path, so it is the one §12.2 exists for:

* The container joins a private bridge with exactly one reachable peer: the
  egress proxy sidecar.
* The proxy enforces an FQDN allowlist. Everything else is dropped and logged.
* **Provider credentials are injected into the proxy, never into the container.**
  The agent authenticates to the proxy with a per-trial token that dies with the
  trial, so a leaked transcript leaks a dead token rather than your API key.

Each adapter installs its CLI, points it at the proxy, runs it non-interactively
against the instruction, and captures the transcript. Nothing about the grading
changes: the verifier still runs afterwards, in a clean container, against the
final filesystem state.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from autobench.agents.base import AgentAdapter, AgentContext, AgentResult
from autobench.core.errors import AgentTimeout, ErrorClass, TrialError
from autobench.db.models import EventKind, ExecutionMode
from autobench.models.types import Usage

logger = logging.getLogger(__name__)

PROXY_HOST = "autobench-egress"
PROXY_PORT = 8080


class ContainerCLIAgent(AgentAdapter):
    """Shared machinery for "run a CLI inside the box" adapters."""

    mode = ExecutionMode.IN_CONTAINER
    #: Overridden per adapter.
    install_command: str = ""
    binary: str = ""

    def command_for(self, context: AgentContext) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def proxy_env(self, context: AgentContext) -> dict[str, str]:
        """Route everything through the sidecar, with a per-trial token.

        ``ANTHROPIC_BASE_URL`` and friends point at the proxy, and the key the
        CLI sees is the trial token — not a provider credential.
        """
        base = f"http://{PROXY_HOST}:{PROXY_PORT}"
        token = f"ab-trial-{context.trial_id}"
        return {
            "HTTP_PROXY": base,
            "HTTPS_PROXY": base,
            "http_proxy": base,
            "https_proxy": base,
            "ANTHROPIC_BASE_URL": f"{base}/anthropic",
            "OPENAI_BASE_URL": f"{base}/openai",
            "ANTHROPIC_API_KEY": token,
            "OPENAI_API_KEY": token,
            "AUTOBENCH_TRIAL_TOKEN": token,
        }

    async def setup(self, context: AgentContext) -> None:
        if not self.install_command:
            return
        result = await context.sandbox.exec(self.install_command, timeout=300)
        if not result.ok:
            raise TrialError(
                f"could not install {self.slug} in the container: {result.combined(1500)}",
                ErrorClass.INFRA_SETUP,
                stage="setup",
            )

    async def run(self, context: AgentContext) -> AgentResult:
        started = time.monotonic()
        limits = context.task.spec.agent
        command = self.command_for(context)

        await context.event(
            EventKind.TOOL_CALL,
            {"index": 1, "name": self.slug, "rendered": command, "arguments": {"command": command}},
        )

        result = await context.sandbox.exec(
            command, timeout=limits.timeout_sec, env=self.proxy_env(context)
        )

        await context.event(
            EventKind.TOOL_RESULT,
            {
                "index": 1,
                "name": self.slug,
                "output": result.combined(8000),
                "elapsed_ms": result.duration_ms,
            },
        )

        if result.timed_out:
            raise AgentTimeout(f"{self.slug} exceeded its {limits.timeout_sec}s budget")

        usage, cost, tool_calls = self.parse_transcript(result.stdout)
        return AgentResult(
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=cost,
            stop_reason="cli_exit",
            final_message=result.combined(2000),
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def parse_transcript(self, stdout: str) -> tuple[Usage, float, int]:
        """Pull usage out of a JSONL transcript when the CLI emits one.

        Best-effort by design: a CLI that reports nothing yields zeros and the
        run's cost is marked under-reported. Inventing a number here would be
        worse than admitting the gap.
        """
        usage = Usage()
        cost = 0.0
        tool_calls = 0
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                record: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw = record.get("usage") or {}
            usage = usage + Usage(
                input_tokens=int(raw.get("input_tokens", 0)),
                output_tokens=int(raw.get("output_tokens", 0)),
                cached_tokens=int(raw.get("cache_read_input_tokens", 0)),
            )
            cost += float(record.get("total_cost_usd") or record.get("cost_usd") or 0.0)
            if record.get("type") in {"tool_use", "tool_call"}:
                tool_calls += 1
        return usage, cost, tool_calls


class ClaudeCodeAgent(ContainerCLIAgent):
    slug = "claude-code"
    version = "1.0.0"
    egress_allowlist = ("api.anthropic.com",)
    install_command = "command -v claude || npm install -g @anthropic-ai/claude-code"
    binary = "claude"

    def command_for(self, context: AgentContext) -> str:
        model = context.model.model_id if context.model else "claude-opus-5"
        prompt = _shell_quote(context.instruction)
        return (
            f"cd {context.task.spec.environment.workdir} && "
            f"claude --print --output-format stream-json --verbose "
            f"--permission-mode bypassPermissions --model {model} {prompt}"
        )


class CodexCLIAgent(ContainerCLIAgent):
    slug = "codex-cli"
    version = "1.0.0"
    egress_allowlist = ("api.openai.com",)
    install_command = "command -v codex || npm install -g @openai/codex"
    binary = "codex"

    def command_for(self, context: AgentContext) -> str:
        model = context.model.model_id if context.model else "gpt-4o"
        prompt = _shell_quote(context.instruction)
        return (
            f"cd {context.task.spec.environment.workdir} && "
            f"codex exec --model {model} --json {prompt}"
        )


class OpenHandsAgent(ContainerCLIAgent):
    slug = "openhands"
    version = "1.0.0"
    egress_allowlist = ("api.anthropic.com", "api.openai.com")
    install_command = "command -v openhands || pip install --quiet openhands-ai"
    binary = "openhands"

    def command_for(self, context: AgentContext) -> str:
        model = context.model.qualified if context.model else "anthropic/claude-opus-5"
        prompt = _shell_quote(context.instruction)
        return (
            f"cd {context.task.spec.environment.workdir} && "
            f"python -m openhands.cli --task {prompt} --model {model} --no-interactive"
        )


def _shell_quote(text: str) -> str:
    import shlex

    return shlex.quote(text)
