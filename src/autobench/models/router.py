"""One chat interface over every provider, with the budget attached.

The router is where three things that must not be separated meet: the provider
call, the shared rate limit, and the cost accounting. Putting them in one place
means there is no path that spends money or quota without recording it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from autobench.core.config import Settings, get_settings
from autobench.core.errors import ProviderError
from autobench.core.telemetry import gen_ai_attributes, record_content_event, span
from autobench.models.pricing import PRICING_VERSION, estimate_cost
from autobench.models.providers import PROVIDERS, Provider
from autobench.models.ratelimit import RateLimiter, build_limiter
from autobench.models.types import ChatRequest, ChatResponse, Usage

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ModelRef:
    """A model, resolved: which provider, which id, which credentials."""

    provider: str
    model_id: str
    slug: str = ""
    base_url: str | None = None
    api_key: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, reference: str, settings: Settings | None = None) -> ModelRef:
        """``anthropic/claude-opus-5``, or a bare id with a guessed provider.

        Guessing is a convenience for the CLI, never for a stored run: the
        database always records the resolved provider explicitly.
        """
        settings = settings or get_settings()
        if "/" in reference:
            provider, model_id = reference.split("/", 1)
        else:
            model_id = reference
            provider = _guess_provider(reference)
        return cls(
            provider=provider,
            model_id=model_id,
            slug=reference,
            api_key=settings.provider_key(provider),
            base_url=settings.openai_compat_base_url if provider == "openai_compat" else None,
        )

    @property
    def qualified(self) -> str:
        return f"{self.provider}/{self.model_id}"


def _guess_provider(model_id: str) -> str:
    lowered = model_id.lower()
    if lowered.startswith(("claude", "opus", "sonnet", "haiku", "fable")):
        return "anthropic"
    if lowered.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if lowered.startswith("gemini"):
        return "google"
    if lowered.startswith("mock"):
        return "mock"
    return "openai_compat"


@dataclass(slots=True)
class CallRecord:
    """What one model call cost, in every currency that matters."""

    usage: Usage
    cost_usd: float
    cost_known: bool
    latency_ms: int
    model: str
    provider: str


class ModelRouter:
    """Provider-agnostic chat, rate-limited and costed."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        limiter: RateLimiter | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.limiter = limiter or build_limiter(self.settings)
        self._providers: dict[str, Provider] = {}
        self.calls: list[CallRecord] = []

    def provider_for(self, ref: ModelRef) -> Provider:
        if ref.provider not in self._providers:
            factory = PROVIDERS.get(ref.provider)
            if factory is None:
                raise ProviderError(
                    f"unknown provider {ref.provider!r}; known: {', '.join(sorted(PROVIDERS))}"
                )
            limits = self.settings.provider
            self._providers[ref.provider] = factory(
                api_key=ref.api_key,
                base_url=ref.base_url,
                max_retries=limits.max_retries,
                backoff_base=limits.backoff_base_sec,
                backoff_max=limits.backoff_max_sec,
                **ref.extra,
            )
        return self._providers[ref.provider]

    async def chat(self, ref: ModelRef, request: ChatRequest) -> tuple[ChatResponse, CallRecord]:
        """Acquire budget, call, record. Always all three, in that order."""
        provider = self.provider_for(ref)
        if ref.api_key is None and ref.provider not in {"mock", "openai_compat"}:
            raise ProviderError(
                f"no API key configured for {ref.provider}. Set "
                f"AUTOBENCH_{ref.provider.upper()}_API_KEY."
            )

        estimated = request.estimated_input_tokens() + request.max_tokens
        await self.limiter.acquire(ref.provider, ref.model_id, estimated)

        try:
            with span(
                "gen_ai.chat",
                **gen_ai_attributes(
                    system=ref.provider,
                    model=ref.model_id,
                    temperature=request.temperature,
                ),
            ) as active:
                if request.system:
                    record_content_event(active, "gen_ai.system.message", request.system)
                response = await provider.chat(request)
                if active is not None:
                    for key, value in gen_ai_attributes(
                        system=ref.provider,
                        model=ref.model_id,
                        input_tokens=response.usage.input_tokens,
                        output_tokens=response.usage.output_tokens,
                        cached_tokens=response.usage.cached_tokens,
                    ).items():
                        active.set_attribute(key, value)
                record_content_event(active, "gen_ai.choice", response.text)
        finally:
            await self.limiter.release(ref.provider, ref.model_id, 0)

        cost, known = estimate_cost(
            ref.provider,
            ref.model_id,
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.usage.cached_tokens,
            response.usage.cache_write_tokens,
        )
        if not known:
            logger.warning(
                "%s is not in the pricing table (version %s); this run's cost is "
                "under-reported, not zero",
                ref.qualified,
                PRICING_VERSION,
            )

        record = CallRecord(
            usage=response.usage,
            cost_usd=cost,
            cost_known=known,
            latency_ms=response.latency_ms,
            model=ref.model_id,
            provider=ref.provider,
        )
        self.calls.append(record)
        return response, record

    @property
    def total_usage(self) -> Usage:
        total = Usage()
        for call in self.calls:
            total = total + call.usage
        return total

    @property
    def total_cost(self) -> float:
        return sum(call.cost_usd for call in self.calls)

    async def close(self) -> None:
        for provider in self._providers.values():
            await provider.close()
        self._providers.clear()
        await self.limiter.close()
