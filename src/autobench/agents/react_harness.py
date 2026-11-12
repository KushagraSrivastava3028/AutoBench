"""The built-in Mode A agent: a ReAct loop that lives in the worker process.

The loop is intentionally plain. A clever scaffold would improve scores and
ruin the measurement — the point of Mode A is to hold the scaffold constant so
that a score difference between two runs is attributable to the model. Anything
that makes this loop smarter makes it a worse instrument.

Three caps, and what each means when hit:

    timeout       wall-clock exceeded        -> agent_timeout,   reward 0.0
    max_tool_calls step budget exhausted     -> agent_tool_cap,  reward 0.0
    max_tokens    context budget exhausted   -> agent_token_cap, reward 0.0

None of them is retried. They are the agent's result, not the harness's.
"""

from __future__ import annotations

import logging
import time

from autobench.agents.base import AgentAdapter, AgentContext, AgentResult
from autobench.agents.tools import DEFAULT_TOOLS, dispatch
from autobench.core.errors import AgentTimeout, TokenCapExceeded, ToolCapExceeded, TrialCancelled
from autobench.core.telemetry import gen_ai_attributes, span
from autobench.db.models import EventKind, ExecutionMode
from autobench.models.types import ChatMessage, ChatRequest, StopReason, Usage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are an autonomous software engineer working inside a Linux container.

Rules of this environment:
- You interact with the container only through the provided tools.
- There is no network access. Everything you need is already installed.
- Your work is graded on the **final state of the filesystem**, not on anything
  you say. Describing a fix is worth nothing; applying it is worth everything.
- Verify your own work before you finish. Run the code, check the file, confirm
  the change took effect.
- When the task is complete, reply with a short summary and stop calling tools.

Working directory: {workdir}
"""


class ReActHarnessAgent(AgentAdapter):
    slug = "react"
    mode = ExecutionMode.HARNESS
    version = "1.0.0"

    async def run(self, context: AgentContext) -> AgentResult:
        if context.router is None or context.model is None:
            raise ValueError("the ReAct harness needs a router and a model")

        limits = context.task.spec.agent
        started = time.monotonic()
        deadline = started + limits.timeout_sec

        system = SYSTEM_PROMPT.format(workdir=context.task.spec.environment.workdir)
        messages: list[ChatMessage] = [ChatMessage.user(context.instruction)]
        await context.event(
            EventKind.PROMPT,
            {
                "system": system,
                "instruction": context.instruction,
                "model": context.model.qualified,
            },
        )

        usage = Usage()
        cost = 0.0
        tool_calls = 0
        final_message = ""
        stop_reason = StopReason.END_TURN.value

        with span(
            "gen_ai.invoke_agent",
            **gen_ai_attributes(
                system=context.model.provider,
                model=context.model.model_id,
                operation="invoke_agent",
                agent_name=self.slug,
            ),
        ):
            while True:
                if context.should_cancel():
                    raise TrialCancelled()

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AgentTimeout(
                        f"agent exceeded its {limits.timeout_sec}s budget after "
                        f"{tool_calls} tool calls"
                    )
                if usage.total >= limits.max_tokens:
                    raise TokenCapExceeded(
                        f"agent used {usage.total} tokens, over the {limits.max_tokens} cap"
                    )

                request = ChatRequest(
                    model=context.model.model_id,
                    messages=list(messages),
                    tools=DEFAULT_TOOLS,
                    system=system,
                    temperature=limits.temperature,
                    max_tokens=limits.max_output_tokens,
                    seed=context.seed,
                )
                response, record = await context.router.chat(context.model, request)
                usage = usage + response.usage
                cost += record.cost_usd

                await context.event(
                    EventKind.MODEL_USAGE,
                    {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "cached_tokens": response.usage.cached_tokens,
                        "cost_usd": round(record.cost_usd, 6),
                        "latency_ms": response.latency_ms,
                        "cumulative_tokens": usage.total,
                    },
                )
                if response.thinking:
                    await context.event(EventKind.THINKING, {"text": response.thinking})

                messages.append(response.to_message())

                if not response.wants_tools:
                    final_message = response.text
                    stop_reason = response.stop_reason.value
                    break

                for call in response.tool_calls:
                    if tool_calls >= limits.max_tool_calls:
                        raise ToolCapExceeded(
                            f"agent made {tool_calls} tool calls, hitting the "
                            f"{limits.max_tool_calls} cap"
                        )
                    tool_calls += 1

                    await context.event(
                        EventKind.TOOL_CALL,
                        {
                            "index": tool_calls,
                            "id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                            "rendered": call.render(),
                        },
                    )

                    with span(
                        "gen_ai.execute_tool",
                        **gen_ai_attributes(
                            system=context.model.provider,
                            model=context.model.model_id,
                            operation="execute_tool",
                            tool_name=call.name,
                        ),
                    ):
                        # The tool never gets longer than the trial has left,
                        # so a single `sleep 9999` cannot outlive the budget.
                        output = await dispatch(context.sandbox, call.name, call.arguments)

                    await context.event(
                        EventKind.TOOL_RESULT,
                        {
                            "index": tool_calls,
                            "id": call.id,
                            "name": call.name,
                            "output": output,
                            "elapsed_ms": int((time.monotonic() - started) * 1000),
                        },
                    )
                    messages.append(ChatMessage.tool_result(call.id, output))

                messages = _compact(messages, limits.max_tokens)

        return AgentResult(
            tool_calls=tool_calls,
            usage=usage,
            cost_usd=cost,
            stop_reason=stop_reason,
            final_message=final_message,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def _compact(messages: list[ChatMessage], max_tokens: int) -> list[ChatMessage]:
    """Drop the middle of a long transcript, keeping the ends.

    The first message is the task and the last few are what just happened;
    everything between is usually `ls` output. Dropping the middle beats hitting
    the context limit and failing a trial for a reason that is the harness's
    fault rather than the model's.
    """
    approx_tokens = sum(len(m.content) for m in messages) // 4
    if approx_tokens < max_tokens * 0.7 or len(messages) < 12:
        return messages
    head, tail = messages[:3], messages[-6:]
    elided = len(messages) - len(head) - len(tail)
    marker = ChatMessage.user(
        f"[{elided} earlier messages elided to stay within the context budget. "
        "Re-read any file you need rather than relying on memory of it.]"
    )
    return [*head, marker, *tail]
