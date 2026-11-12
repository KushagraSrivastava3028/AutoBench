"""Agent adapters. Autobench does not implement an agent; it adapts to them."""

from __future__ import annotations

from typing import Any

from autobench.agents.base import AgentAdapter, AgentContext, AgentResult
from autobench.agents.calibration import NullAgent, OracleAgent
from autobench.agents.cli_adapters import ClaudeCodeAgent, CodexCLIAgent, OpenHandsAgent
from autobench.agents.react_harness import ReActHarnessAgent
from autobench.core.errors import NotFoundError

ADAPTERS: dict[str, type[AgentAdapter]] = {
    "react": ReActHarnessAgent,
    "oracle": OracleAgent,
    "null": NullAgent,
    "claude-code": ClaudeCodeAgent,
    "codex-cli": CodexCLIAgent,
    "openhands": OpenHandsAgent,
}

#: Agents that need no model and no credentials. Used by the validator and by
#: ``bench-harness``, where a model would only add variance.
CALIBRATION_AGENTS = frozenset({"oracle", "null"})


def build_agent(slug: str, config: dict[str, Any] | None = None) -> AgentAdapter:
    adapter = ADAPTERS.get(slug)
    if adapter is None:
        raise NotFoundError(f"unknown agent {slug!r}; known: {', '.join(sorted(ADAPTERS))}")
    return adapter(config)


def requires_model(slug: str) -> bool:
    """Whether this adapter needs a model reference to run."""
    adapter = ADAPTERS.get(slug)
    if adapter is None:
        return True
    return adapter.needs_model


__all__ = [
    "ADAPTERS",
    "CALIBRATION_AGENTS",
    "AgentAdapter",
    "AgentContext",
    "AgentResult",
    "ClaudeCodeAgent",
    "CodexCLIAgent",
    "NullAgent",
    "OpenHandsAgent",
    "OracleAgent",
    "ReActHarnessAgent",
    "build_agent",
    "requires_model",
]
