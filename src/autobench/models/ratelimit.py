"""A provider budget shared across every worker process.

This is the difference between parallelism and a retry storm. Without a
*distributed* limiter, scaling past roughly three workers converts throughput
into 429s: each worker independently believes it has the whole quota, they all
overshoot together, they all back off together, and the run gets slower as you
add workers. That failure is the classic one, and it is why optimisation O5
exists (PRD §8.3).

The implementation is a sliding window over both dimensions providers actually
bill — requests per minute and *tokens* per minute — evaluated in a single Lua
script so the check-and-consume is atomic. A read-then-write in Python would
race between workers and reintroduce exactly the overshoot it exists to stop.

``InMemoryTokenBucket`` is the same interface for single-process runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

logger = logging.getLogger(__name__)

#: Atomic: prune the window, check both dimensions, consume or report the wait.
#:
#: KEYS[1] requests zset, KEYS[2] tokens zset
#: ARGV: now_ms, window_ms, rpm, tpm, token_cost, member
_LUA_ACQUIRE = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local rpm = tonumber(ARGV[3])
local tpm = tonumber(ARGV[4])
local cost = tonumber(ARGV[5])
local member = ARGV[6]
local cutoff = now - window

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', cutoff)

local requests = redis.call('ZCARD', KEYS[1])

-- Token usage is stored as member='<ts>:<uuid>:<cost>'; sum the costs in window.
local used = 0
local entries = redis.call('ZRANGE', KEYS[2], cutoff, '+inf', 'BYSCORE')
for i = 1, #entries do
  local sep = string.find(entries[i], '|[^|]*$')
  if sep then
    used = used + tonumber(string.sub(entries[i], sep + 1)) or 0
  end
end

if requests + 1 > rpm or used + cost > tpm then
  -- Report how long until the oldest entry falls out of the window.
  local oldest_r = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local oldest_t = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
  local oldest = now
  if oldest_r[2] then oldest = math.min(oldest, tonumber(oldest_r[2])) end
  if oldest_t[2] then oldest = math.min(oldest, tonumber(oldest_t[2])) end
  local wait = (oldest + window) - now
  if wait < 1 then wait = 1 end
  return {0, wait}
end

redis.call('ZADD', KEYS[1], now, member)
redis.call('ZADD', KEYS[2], now, member .. '|' .. cost)
redis.call('PEXPIRE', KEYS[1], window + 1000)
redis.call('PEXPIRE', KEYS[2], window + 1000)
return {1, 0}
"""

WINDOW_MS = 60_000


@dataclass(slots=True)
class Lease:
    granted: bool
    wait_ms: int = 0


class RateLimiter(Protocol):
    async def acquire(self, provider: str, model: str, estimated_tokens: int) -> None: ...
    async def release(self, provider: str, model: str, actual_tokens: int) -> None: ...
    async def close(self) -> None: ...


class InMemoryTokenBucket:
    """Single-process limiter. Correct, but only for one worker."""

    def __init__(self, rpm: int, tpm: int, max_concurrent: int = 32) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._requests: list[float] = []
        self._tokens: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(max_concurrent)

    async def acquire(self, provider: str, model: str, estimated_tokens: int) -> None:
        await self._concurrency.acquire()
        try:
            while True:
                async with self._lock:
                    now = time.monotonic()
                    cutoff = now - 60.0
                    self._requests = [t for t in self._requests if t > cutoff]
                    self._tokens = [(t, n) for t, n in self._tokens if t > cutoff]
                    used = sum(n for _, n in self._tokens)
                    if len(self._requests) + 1 <= self.rpm and used + estimated_tokens <= self.tpm:
                        self._requests.append(now)
                        self._tokens.append((now, estimated_tokens))
                        return
                    oldest = min(
                        [t for t in self._requests] + [t for t, _ in self._tokens], default=now
                    )
                    wait = max(0.05, (oldest + 60.0) - now)
                await asyncio.sleep(min(wait, 5.0))
        except BaseException:
            self._concurrency.release()
            raise

    async def release(self, provider: str, model: str, actual_tokens: int) -> None:
        """Reconcile the estimate with reality, then free the concurrency slot."""
        async with self._lock:
            if self._tokens:
                timestamp, estimated = self._tokens[-1]
                self._tokens[-1] = (timestamp, actual_tokens or estimated)
        self._concurrency.release()

    async def close(self) -> None:
        return None


class RedisTokenBucket:
    """The real one: a sliding window shared by every worker, keyed per model.

    Keyed on ``(provider, model)`` rather than on provider alone because quotas
    are per model in practice — saturating a cheap model's TPM should not stall
    calls to an expensive one that has its own headroom.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        rpm: int,
        tpm: int,
        max_concurrent: int = 32,
        namespace: str = "autobench:rl",
    ) -> None:
        self.redis_url = redis_url
        self.rpm = rpm
        self.tpm = tpm
        self.namespace = namespace
        self._client: Any = None
        self._script: Any = None
        self._concurrency = asyncio.Semaphore(max_concurrent)
        self._throttled = 0

    @property
    def throttled_count(self) -> int:
        """Feeds ``autobench_provider_throttled_total``."""
        return self._throttled

    async def _connect(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis_async

            factory = cast(Callable[..., Any], redis_async.from_url)
            self._client = factory(self.redis_url, decode_responses=True)
            self._script = self._client.register_script(_LUA_ACQUIRE)
        return self._client

    def _keys(self, provider: str, model: str) -> tuple[str, str]:
        base = f"{self.namespace}:{provider}:{model}"
        return f"{base}:req", f"{base}:tok"

    async def acquire(self, provider: str, model: str, estimated_tokens: int) -> None:
        """Block until the call fits inside the shared budget.

        The wait comes from Redis rather than from a local guess, so every
        worker wakes at a different time instead of all retrying in lockstep —
        which is the thundering herd the limiter exists to prevent.
        """
        await self._connect()
        await self._concurrency.acquire()
        request_key, token_key = self._keys(provider, model)
        member = f"{time.time_ns()}:{id(self)}"

        try:
            while True:
                granted, wait_ms = await self._script(
                    keys=[request_key, token_key],
                    args=[
                        int(time.time() * 1000),
                        WINDOW_MS,
                        self.rpm,
                        self.tpm,
                        max(1, estimated_tokens),
                        member,
                    ],
                )
                if int(granted) == 1:
                    return
                self._throttled += 1
                # Jitter: identical sleeps across workers would just rebuild the herd.
                import random

                delay = min(int(wait_ms) / 1000.0, 10.0) * (0.5 + random.random())  # noqa: S311
                logger.debug("rate limit %s/%s: waiting %.2fs", provider, model, delay)
                await asyncio.sleep(max(0.05, delay))
        except BaseException:
            self._concurrency.release()
            raise

    async def release(self, provider: str, model: str, actual_tokens: int) -> None:
        self._concurrency.release()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def build_limiter(settings: Any) -> RateLimiter:
    """Redis when configured, in-memory otherwise. Same interface either way."""
    limits = settings.provider
    if settings.queue == "redis":
        return RedisTokenBucket(
            settings.redis_url,
            rpm=limits.requests_per_minute,
            tpm=limits.tokens_per_minute,
            max_concurrent=limits.max_concurrent,
        )
    return InMemoryTokenBucket(
        limits.requests_per_minute, limits.tokens_per_minute, limits.max_concurrent
    )
