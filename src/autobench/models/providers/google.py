"""Google Gemini (generateContent)."""

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

_STOP_REASONS = {
    "STOP": StopReason.END_TURN,
    "MAX_TOKENS": StopReason.MAX_TOKENS,
    "SAFETY": StopReason.REFUSAL,
    "RECITATION": StopReason.REFUSAL,
}


class GoogleProvider(Provider):
    name = "google"
    default_base_url = "https://generativelanguage.googleapis.com"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._model_for_url = ""

    def endpoint(self) -> str:
        # Gemini puts the model in the path, so build_payload records it first.
        return f"/v1beta/models/{self._model_for_url}:generateContent"

    def build_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key or "", "content-type": "application/json"}

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        self._model_for_url = request.model

        contents: list[dict[str, Any]] = []
        for message in request.messages:
            if message.role is Role.SYSTEM:
                continue
            contents.append(_encode(message))

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
            },
        }
        system = request.system or next(
            (m.content for m in request.messages if m.role is Role.SYSTEM), None
        )
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if request.tools:
            payload["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in request.tools
                    ]
                }
            ]
        if request.stop_sequences:
            payload["generationConfig"]["stopSequences"] = request.stop_sequences
        return payload

    def parse_response(self, data: dict[str, Any], latency_ms: int) -> ChatResponse:
        candidates = data.get("candidates") or [{}]
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])
            elif "functionCall" in part:
                call = part["functionCall"]
                tool_calls.append(
                    ToolCall(
                        id=str(uuid.uuid4()),  # Gemini does not issue call ids
                        name=call.get("name", ""),
                        arguments=call.get("args") or {},
                    )
                )

        raw_usage = data.get("usageMetadata") or {}
        cached = int(raw_usage.get("cachedContentTokenCount", 0))
        usage = Usage(
            input_tokens=max(0, int(raw_usage.get("promptTokenCount", 0)) - cached),
            output_tokens=int(raw_usage.get("candidatesTokenCount", 0)),
            cached_tokens=cached,
        )
        stop = _STOP_REASONS.get(candidate.get("finishReason", ""), StopReason.END_TURN)
        if tool_calls:
            stop = StopReason.TOOL_USE
        return ChatResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            stop_reason=stop,
            model=data.get("modelVersion", ""),
            latency_ms=latency_ms,
        )


def _encode(message: ChatMessage) -> dict[str, Any]:
    if message.role is Role.TOOL:
        return {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": message.tool_call_id or "tool",
                        "response": {"output": message.content},
                    }
                }
            ],
        }
    if message.role is Role.ASSISTANT and message.tool_calls:
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        parts.extend(
            {"functionCall": {"name": call.name, "args": call.arguments}}
            for call in message.tool_calls
        )
        return {"role": "model", "parts": parts}
    role = "model" if message.role is Role.ASSISTANT else "user"
    return {"role": role, "parts": [{"text": message.content}]}
