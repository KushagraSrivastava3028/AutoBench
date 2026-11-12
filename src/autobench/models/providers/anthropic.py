"""Anthropic Messages API."""

from __future__ import annotations

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

API_VERSION = "2023-06-01"

_STOP_REASONS = {
    "end_turn": StopReason.END_TURN,
    "tool_use": StopReason.TOOL_USE,
    "max_tokens": StopReason.MAX_TOKENS,
    "stop_sequence": StopReason.STOP_SEQUENCE,
    "refusal": StopReason.REFUSAL,
}


class AnthropicProvider(Provider):
    name = "anthropic"
    default_base_url = "https://api.anthropic.com"

    def endpoint(self) -> str:
        return "/v1/messages"

    def build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key or "",
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "messages": [_encode(m) for m in request.messages if m.role is not Role.SYSTEM],
        }
        system = request.system or next(
            (m.content for m in request.messages if m.role is Role.SYSTEM), None
        )
        if system:
            # Cache the system prompt: it is byte-identical across every trial
            # of a suite, and it is the largest constant part of the context.
            payload["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        if request.stop_sequences:
            payload["stop_sequences"] = request.stop_sequences
        return payload

    def parse_response(self, data: dict[str, Any], latency_ms: int) -> ChatResponse:
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in data.get("content", []):
            kind = block.get("type")
            if kind == "text":
                text_parts.append(block.get("text", ""))
            elif kind == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif kind == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id") or str(uuid.uuid4()),
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )

        raw_usage = data.get("usage") or {}
        usage = Usage(
            input_tokens=int(raw_usage.get("input_tokens", 0)),
            output_tokens=int(raw_usage.get("output_tokens", 0)),
            cached_tokens=int(raw_usage.get("cache_read_input_tokens", 0)),
            cache_write_tokens=int(raw_usage.get("cache_creation_input_tokens", 0)),
        )
        return ChatResponse(
            text="\n".join(p for p in text_parts if p),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=_STOP_REASONS.get(data.get("stop_reason", ""), StopReason.END_TURN),
            thinking="\n".join(thinking_parts) or None,
            model=data.get("model", ""),
            latency_ms=latency_ms,
        )


def _encode(message: ChatMessage) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content,
                }
            ],
        }
    if message.role is Role.ASSISTANT and message.tool_calls:
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        content.extend(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
            for call in message.tool_calls
        )
        return {"role": "assistant", "content": content}
    return {"role": message.role.value, "content": message.content}
