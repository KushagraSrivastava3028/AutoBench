"""Provider-agnostic chat types.

One shape in, one shape out, whatever is behind it. The agent loop is written
against these types and has never heard of Anthropic's ``content`` blocks or
OpenAI's ``tool_calls`` array; translating between the two is each provider
adapter's whole job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

    def render(self) -> str:
        """The form shown in the trajectory timeline."""
        if self.name == "bash":
            return str(self.arguments.get("command", ""))
        return f"{self.name}({json.dumps(self.arguments, default=str)[:400]})"


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    #: Set on TOOL messages, linking the result back to its call.
    tool_call_id: str | None = None
    #: Extended thinking, where the provider exposes it.
    thinking: str | None = None

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(Role.SYSTEM, content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(Role.USER, content)

    @classmethod
    def tool_result(cls, call_id: str, content: str) -> ChatMessage:
        return cls(Role.TOOL, content, tool_call_id=call_id)


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    #: JSON Schema for the arguments.
    parameters: dict[str, Any]


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cached_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cached_tokens + other.cached_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    REFUSAL = "refusal"
    ERROR = "error"


@dataclass(slots=True)
class ChatResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: StopReason = StopReason.END_TURN
    thinking: str | None = None
    model: str = ""
    latency_ms: int = 0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def to_message(self) -> ChatMessage:
        return ChatMessage(
            Role.ASSISTANT,
            self.text,
            tool_calls=list(self.tool_calls),
            thinking=self.thinking,
        )


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] = field(default_factory=list)
    system: str | None = None
    temperature: float = 0.0
    max_tokens: int = 8192
    #: Providers that accept a seed get one. Most do not honour it strictly,
    #: which is precisely why k>=3 exists rather than a promise of determinism.
    seed: int | None = None
    stop_sequences: list[str] = field(default_factory=list)

    def estimated_input_tokens(self) -> int:
        """~4 chars per token. Good enough to pre-charge the rate limiter.

        The limiter reconciles against the real count on release, so this only
        needs to be the right order of magnitude.
        """
        chars = len(self.system or "")
        for message in self.messages:
            chars += len(message.content)
            chars += sum(len(call.render()) for call in message.tool_calls)
        return max(1, chars // 4)
