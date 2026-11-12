"""The agent contract.

An adapter's job is narrow: given a sandbox and an instruction, mutate the
sandbox. It does **not** score itself, it does not see the tests, and its prose
output is not graded. The only thing that matters is the state it leaves behind.

Two topologies, both first-class (PRD §4.3):

``harness`` (Mode A, default)
    The agent loop runs in the worker. The container has no network and no
    credentials. This measures the *model's* tool-use reasoning with the
    scaffold held constant — one fewer moving part, so higher reproducibility.

``in_container`` (Mode B)
    A real agent CLI runs inside the container, reaching its model through an
    egress proxy with an FQDN allowlist. This measures the *whole product*, and
    it is the only way to evaluate Claude Code, Codex CLI or OpenHands as they
    actually ship. The CLI version becomes another axis of variation, which is
    the price.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from autobench.db.models import EventKind, ExecutionMode
from autobench.executor.base import Sandbox
from autobench.models.router import ModelRef, ModelRouter
from autobench.models.types import Usage
from autobench.taskspec.loader import TaskDirectory

#: Called for every trajectory step. The pipeline persists these, redacted.
EventSink = Callable[[EventKind, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class AgentContext:
    """Everything an adapter is allowed to see. Note what is absent: the tests."""

    task: TaskDirectory
    sandbox: Sandbox
    instruction: str
    seed: int
    trial_id: str
    router: ModelRouter | None = None
    model: ModelRef | None = None
    emit: EventSink | None = None
    #: Checked between steps so cancellation lands within a few seconds.
    should_cancel: Callable[[], bool] = lambda: False

    async def event(self, kind: EventKind, payload: dict[str, Any]) -> None:
        if self.emit is not None:
            await self.emit(kind, payload)


@dataclass(slots=True)
class AgentResult:
    """What the adapter did, never what it claims to have achieved."""

    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0
    stop_reason: str = "end_turn"
    final_message: str = ""
    duration_ms: int = 0
    #: Set when the agent hit a cap. The pipeline turns this into reward 0.0
    #: and — importantly — does *not* retry.
    limit_hit: str | None = None


class AgentAdapter(ABC):
    """Base for every agent. Stateless across trials; one instance per trial."""

    slug: str = "base"
    mode: ExecutionMode = ExecutionMode.HARNESS
    version: str = "1.0.0"
    #: False for adapters that drive the sandbox without a model — the oracle,
    #: the null agent, and any deterministic replay adapter. Declared here
    #: rather than in a module-level set so a third-party adapter can opt out
    #: without editing the registry.
    needs_model: bool = True
    #: Mode B only. Empty means the proxy drops everything.
    egress_allowlist: tuple[str, ...] = ()

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Drive the sandbox toward the goal. Raise ``TrialError`` on a cap."""

    async def setup(self, context: AgentContext) -> None:
        """Hook for Mode B adapters to install a CLI, write config, inject a token."""
        return None

    def describe(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "mode": self.mode.value,
            "version": self.version,
            "egress_allowlist": list(self.egress_allowlist),
        }
