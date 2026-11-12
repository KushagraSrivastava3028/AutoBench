"""Provider adapter base: retries, error classification, one shared HTTP client.

Retry policy in one line: **429 and 5xx are infrastructure, 4xx is not.** A
malformed request retried five times is five identical failures and a wasted
minute; a rate limit retried with backoff is the system working as designed.
"""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx

from autobench.core.errors import ProviderError
from autobench.models.types import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class Provider(ABC):
    """One model API. Subclasses translate request and response shapes."""

    name: str = "base"
    default_base_url: str = ""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float = 300.0,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or self.default_base_url).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self._client: httpx.AsyncClient | None = None

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=15.0),
                # Connections are reused across every trial on this worker;
                # a fresh TLS handshake per model call is pure latency.
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- subclass contract -------------------------------------------------

    @abstractmethod
    def build_payload(self, request: ChatRequest) -> dict[str, Any]: ...

    @abstractmethod
    def build_headers(self) -> dict[str, str]: ...

    @abstractmethod
    def endpoint(self) -> str: ...

    @abstractmethod
    def parse_response(self, data: dict[str, Any], latency_ms: int) -> ChatResponse: ...

    # --- the one public method ---------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        import time

        payload = self.build_payload(request)
        headers = self.build_headers()
        url = f"{self.base_url}{self.endpoint()}"
        client = await self.client()

        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.perf_counter()
            try:
                response = await client.post(url, json=payload, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise ProviderError(
                        f"{self.name}: transport failure after {attempt + 1} attempts: {exc}"
                    ) from exc
                await self._sleep(attempt, None)
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)

            if response.status_code < 400:
                try:
                    return self.parse_response(response.json(), latency_ms)
                except (ValueError, KeyError) as exc:
                    raise ProviderError(
                        f"{self.name}: could not parse a 200 response: {exc}",
                        status_code=response.status_code,
                    ) from exc

            detail = _extract_error(response)
            if response.status_code not in RETRYABLE_STATUS or attempt >= self.max_retries:
                raise ProviderError(
                    f"{self.name}: HTTP {response.status_code}: {detail}",
                    status_code=response.status_code,
                )
            logger.warning(
                "%s: HTTP %s, retry %d/%d",
                self.name,
                response.status_code,
                attempt + 1,
                self.max_retries,
            )
            await self._sleep(attempt, response)

        raise ProviderError(f"{self.name}: exhausted retries: {last_error}")

    async def _sleep(self, attempt: int, response: httpx.Response | None) -> None:
        """Honour ``Retry-After`` when the provider sends one; jitter otherwise.

        Full jitter rather than fixed backoff: every worker that got a 429 got
        it at the same moment, and identical backoffs would have them all return
        at the same moment too.
        """
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    await asyncio.sleep(min(float(retry_after), self.backoff_max))
                    return
                except ValueError:
                    pass
        ceiling = min(self.backoff_base * (2**attempt), self.backoff_max)
        await asyncio.sleep(random.uniform(0, ceiling))  # noqa: S311 - jitter, not crypto


def _extract_error(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:400]
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)[:400]
        if error:
            return str(error)[:400]
        if "message" in data:
            return str(data["message"])[:400]
    return str(data)[:400]
