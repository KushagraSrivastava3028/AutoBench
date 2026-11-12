"""A deterministic, offline model.

This is not a toy. It is load-bearing for three things:

* **``bench-harness``** measures harness overhead. A real model's sampling
  variance is larger than the effect being measured, so the benchmark would be
  measuring the weather. The mock returns in a fixed simulated latency.
* **CI** runs the full pipeline, the scheduler and the e2e suite on every PR
  without an API key and without spending money.
* **Statistical tests** need a task with a *known* pass rate to check that a
  95% interval covers the truth about 95% of the time.

Behaviour is a pure function of the seed and the message history, so the same
trial replays identically.
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from typing import Any

from autobench.models.providers.base import Provider
from autobench.models.types import (
    ChatRequest,
    ChatResponse,
    Role,
    StopReason,
    ToolCall,
    Usage,
)


class MockProvider(Provider):
    """Deterministic. Solves a configurable fraction of tasks."""

    name = "mock"
    default_base_url = "mock://local"

    def __init__(
        self,
        *args: Any,
        solve_rate: float = 0.5,
        latency_ms: int = 120,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.solve_rate = solve_rate
        self.latency_ms = latency_ms

    # The base class's HTTP machinery is bypassed entirely.
    def endpoint(self) -> str:  # pragma: no cover
        return ""

    def build_headers(self) -> dict[str, str]:  # pragma: no cover
        return {}

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:  # pragma: no cover
        return {}

    def parse_response(
        self, data: dict[str, Any], latency_ms: int
    ) -> ChatResponse:  # pragma: no cover
        raise NotImplementedError

    async def chat(self, request: ChatRequest) -> ChatResponse:
        # Simulated latency, so concurrency speedup is measurable without the
        # noise a real provider contributes.
        await asyncio.sleep(self.latency_ms / 1000.0)

        history = "\n".join(f"{m.role.value}:{m.content}" for m in request.messages)
        digest = hashlib.blake2b(
            f"{request.seed}:{request.model}:{history}".encode(), digest_size=8
        ).digest()
        rng = random.Random(int.from_bytes(digest, "big"))  # noqa: S311 - simulation

        turns = sum(1 for m in request.messages if m.role is Role.ASSISTANT)
        usage = Usage(
            input_tokens=request.estimated_input_tokens(),
            output_tokens=rng.randint(40, 200),
        )

        # Turn 0: attempt the task. Turn 1+: stop.
        if turns == 0 and request.tools:
            solves = rng.random() < self.solve_rate
            command = self._command_for(request, solves)
            return ChatResponse(
                text="Inspecting the workspace and applying the fix.",
                tool_calls=[
                    ToolCall(id=f"mock-{turns}", name="bash", arguments={"command": command})
                ],
                usage=usage,
                stop_reason=StopReason.TOOL_USE,
                model=request.model,
                latency_ms=self.latency_ms,
            )
        return ChatResponse(
            text="Done.",
            usage=usage,
            stop_reason=StopReason.END_TURN,
            model=request.model,
            latency_ms=self.latency_ms,
        )

    def _command_for(self, request: ChatRequest, solves: bool) -> str:
        """Solve the built-in fixtures; otherwise poke around plausibly."""
        instruction = "\n".join(m.content for m in request.messages if m.role is Role.USER)
        if not solves:
            return "ls -la && echo 'inspecting'"
        # Relative paths: the working directory is the task's workdir under
        # every executor, so these run unchanged with or without a container.
        if "done.txt" in instruction:
            return "printf 'ok' > done.txt"
        if "greeting.txt" in instruction:
            return "printf 'Hello, Autobench!' > greeting.txt"
        if "alpha.txt" in instruction:
            return "printf '1' > alpha.txt && printf '2' > beta.txt && printf '3' > gamma.txt"
        if "coin.txt" in instruction:
            return "printf 'heads' > coin.txt"
        if "survived.txt" in instruction:
            return "printf 'ok' > survived.txt"
        return "ls -la"
