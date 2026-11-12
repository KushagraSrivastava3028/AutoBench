"""OpenAI Chat Completions, and anything that speaks the same dialect.

``OpenAICompatProvider`` is the same wire format pointed at a different base
URL, which is what vLLM, Ollama, LM Studio, Together and most self-hosted
gateways serve. One adapter, four deployment stories.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from autobench.models.providers.base import Provider
from autobench.models.types import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    Role,
    StopReason,
    ToolCall,
    Usage,
)

_STOP_REASONS = {
    "stop": StopReason.END_TURN,
    "tool_calls": StopReason.TOOL_USE,
    "function_call": StopReason.TOOL_USE,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


class OpenAIProvider(Provider):
    name = "openai"
    default_base_url = "https://api.openai.com"

    def endpoint(self) -> str:
        return "/v1/chat/completions"

    def build_headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {self.api_key or ''}",
            "content-type": "application/json",
        }

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        messages = [_encode(m) for m in request.messages]
        if request.system and not any(m["role"] == "system" for m in messages):
            messages.insert(0, {"role": "system", "content": request.system})

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "temperature": request.temperature,
            "max_completion_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
            payload["tool_choice"] = "auto"
        if request.seed is not None:
            payload["seed"] = request.seed
        if request.stop_sequences:
            payload["stop"] = request.stop_sequences
        return payload

    def parse_response(self, data: dict[str, Any], latency_ms: int) -> ChatResponse:
        choices = data.get("choices") or [{}]
        choice = choices[0]
        message = choice.get("message") or {}

        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                # A model that emits invalid JSON arguments has made a tool
                # call the harness cannot execute. Surface it as a call with
                # the raw text so the trajectory shows what actually happened,
                # rather than dropping it and leaving an unexplained gap.
                arguments = {"__unparsed__": raw_args}
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or str(uuid.uuid4()),
                    name=function.get("name", ""),
                    arguments=arguments if isinstance(arguments, dict) else {"value": arguments},
                )
            )

        raw_usage = data.get("usage") or {}
        details = raw_usage.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens", 0))
        usage = Usage(
            # Cached tokens are reported inside prompt_tokens; subtract them so
            # the pricing table can bill the two buckets at their own rates.
            input_tokens=max(0, int(raw_usage.get("prompt_tokens", 0)) - cached),
            output_tokens=int(raw_usage.get("completion_tokens", 0)),
            cached_tokens=cached,
        )
        return ChatResponse(
            text=message.get("content") or "",
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=_STOP_REASONS.get(choice.get("finish_reason", ""), StopReason.END_TURN),
            model=data.get("model", ""),
            latency_ms=latency_ms,
        )


class OpenAICompatProvider(OpenAIProvider):
    """vLLM, Ollama, LM Studio, Together — same wire format, your base URL."""

    name = "openai_compat"
    default_base_url = "http://localhost:8000"


def _encode(message: ChatMessage) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }
    if message.role is Role.ASSISTANT and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments),
                    },
                }
                for call in message.tool_calls
            ],
        }
    return {"role": message.role.value, "content": message.content}
