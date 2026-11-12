"""Provider adapters. One wire format each, one shape out."""

from autobench.models.providers.anthropic import AnthropicProvider
from autobench.models.providers.base import Provider
from autobench.models.providers.google import GoogleProvider
from autobench.models.providers.mock import MockProvider
from autobench.models.providers.openai import OpenAICompatProvider, OpenAIProvider

PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
    "openai_compat": OpenAICompatProvider,
    "mock": MockProvider,
}

__all__ = [
    "PROVIDERS",
    "AnthropicProvider",
    "GoogleProvider",
    "MockProvider",
    "OpenAICompatProvider",
    "OpenAIProvider",
    "Provider",
]
