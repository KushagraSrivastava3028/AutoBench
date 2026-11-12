"""Server-sent events: workers publish, browsers subscribe.

SSE rather than WebSocket because the traffic is one-directional — server to
browser — and SSE gets reconnection, ``Last-Event-ID`` resume and proxy
traversal for free, where a WebSocket makes each of those your problem.

Two transports behind one interface:

* **Redis pub/sub** when workers are separate processes, which they are in any
  real deployment. A worker publishes to ``autobench:events:<run_id>``; every
  API replica subscribed to that channel forwards to its connected browsers.
* **An in-process broker** for ``autobench run``, where the worker and the API
  are the same process and Redis would be ceremony.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from typing import Any, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from autobench.core.config import Settings

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "autobench:events"
#: Replayed to a client that reconnects with Last-Event-ID, so a dropped
#: connection does not silently lose cell updates.
REPLAY_BUFFER = 256
HEARTBEAT_SEC = 15


def channel_for(run_id: str) -> str:
    return f"{CHANNEL_PREFIX}:{run_id}"


class InProcessBroker:
    """Fan-out within one process. Also the replay buffer for both transports."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._history: dict[str, deque[tuple[int, dict[str, Any]]]] = defaultdict(
            lambda: deque(maxlen=REPLAY_BUFFER)
        )
        self._sequence: dict[str, int] = defaultdict(int)

    async def publish(self, run_id: str, payload: dict[str, Any]) -> int:
        self._sequence[run_id] += 1
        seq = self._sequence[run_id]
        enveloped = {**payload, "seq": seq}
        self._history[run_id].append((seq, enveloped))
        for queue in list(self._subscribers[run_id]):
            try:
                queue.put_nowait(enveloped)
            except asyncio.QueueFull:
                # A browser tab that is not draining must not stall the worker
                # that is producing results.
                logger.debug("dropping event for a slow subscriber on run %s", run_id)
        return seq

    def replay(self, run_id: str, after_seq: int) -> list[dict[str, Any]]:
        return [payload for seq, payload in self._history[run_id] if seq > after_seq]

    async def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1024)
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers[run_id].discard(queue)
        if not self._subscribers[run_id]:
            self._subscribers.pop(run_id, None)

    @property
    def subscriber_count(self) -> int:
        return sum(len(queues) for queues in self._subscribers.values())


_broker = InProcessBroker()
_redis: Any = None


def get_broker() -> InProcessBroker:
    return _broker


async def _redis_client(settings: Settings) -> Any:
    global _redis
    if _redis is None:
        import redis.asyncio as redis_async

        factory = cast(Callable[..., Any], redis_async.from_url)
        _redis = factory(settings.redis_url, decode_responses=True)
    return _redis


async def append_transition(session: AsyncSession, run_id: str, payload: dict[str, Any]) -> int:
    from autobench.db.models import RunTransition

    row = RunTransition(run_id=UUID(run_id), payload=payload)
    session.add(row)
    await session.flush()
    return row.id


async def publish(
    settings: Settings, run_id: str, payload: dict[str, Any], *, persist: bool = True
) -> None:
    """Called by workers. Local fan-out always; Redis as well when configured."""
    if persist:
        from autobench.db.session import session_scope

        async with session_scope(settings) as session:
            await append_transition(session, run_id, payload)
    await _broker.publish(run_id, payload)
    if settings.queue != "redis":
        return
    try:
        client = await _redis_client(settings)
        await client.publish(channel_for(run_id), json.dumps(payload, default=str))
    except Exception as exc:  # pragma: no cover - never fail a trial over telemetry
        logger.warning("could not publish run event to redis: %s", exc)


async def subscribe(
    settings: Settings, run_id: str, *, last_event_id: int = 0
) -> AsyncIterator[dict[str, Any]]:
    """Yield events for one run, replaying anything missed since ``last_event_id``."""
    from sqlalchemy import select

    from autobench.db.models import RunTransition
    from autobench.db.session import session_scope

    cursor = last_event_id
    heartbeat = time.monotonic()
    # The database log is authoritative across API/worker processes and API
    # restarts. No subscriber owns a Redis bridge or an in-memory resume cursor.
    while True:
        async with session_scope(settings) as session:
            rows = (
                await session.scalars(
                    select(RunTransition)
                    .where(
                        RunTransition.run_id == UUID(run_id),
                        RunTransition.id > cursor,
                    )
                    .order_by(RunTransition.id)
                    .limit(200)
                )
            ).all()
            frames = [{**row.payload, "seq": row.id} for row in rows]
        for frame in frames:
            cursor = frame["seq"]
            yield frame
            heartbeat = time.monotonic()
        if time.monotonic() - heartbeat >= HEARTBEAT_SEC:
            yield {"event": "heartbeat"}
            heartbeat = time.monotonic()
        await asyncio.sleep(0.5)


async def _bridge_redis(settings: Settings, run_id: str) -> None:
    """Forward this run's Redis channel into the local broker."""
    try:
        client = await _redis_client(settings)
        pubsub = client.pubsub()
        await pubsub.subscribe(channel_for(run_id))
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                await _broker.publish(run_id, json.loads(message["data"]))
            except (json.JSONDecodeError, TypeError):
                continue
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover
        logger.warning("redis event bridge for run %s stopped: %s", run_id, exc)


def format_sse(payload: dict[str, Any]) -> str:
    """One SSE frame. ``id:`` is what makes ``Last-Event-ID`` resume work."""
    if payload.get("event") == "heartbeat":
        return ": heartbeat\n\n"
    event = payload.get("event", "message")
    seq = payload.get("seq")
    body = json.dumps(payload, default=str)
    frame = f"event: {event}\n"
    if seq is not None:
        frame += f"id: {seq}\n"
    return frame + f"data: {body}\n\n"


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
