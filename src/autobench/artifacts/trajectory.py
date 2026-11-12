"""The trajectory writer: streaming, redacted, and bounded.

Three properties, each chosen against a specific failure:

*Streaming* (optimisation O7). Events are written as they happen, not batched at
the end of a trial. A worker killed mid-trial still leaves a readable
trajectory, which is exactly when you most want one. It is also the difference
between holding a 200MB transcript in memory and holding a line.

*Redacted at write time.* A secret that reaches storage has already leaked.
Every event passes through :func:`redact_payload` before it is persisted, in
both destinations.

*Bounded.* A pathological agent can emit tens of thousands of events. Past the
cap, events are counted rather than stored, and the artifact says so.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from autobench.core.ids import utcnow
from autobench.core.security import redact_payload
from autobench.db.models import EventKind

logger = logging.getLogger(__name__)

MAX_EVENTS = 20_000
MAX_PAYLOAD_CHARS = 64 * 1024


@dataclass(slots=True)
class TrajectoryEvent:
    seq: int
    ts: datetime
    kind: EventKind
    payload: dict[str, Any]
    elapsed_ms: int | None = None
    cumulative_tokens: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts.isoformat(),
            "kind": self.kind.value,
            "payload": self.payload,
            "elapsed_ms": self.elapsed_ms,
            "cumulative_tokens": self.cumulative_tokens,
        }


@dataclass(slots=True)
class TrajectoryWriter:
    """Accumulates one trial's events. Redacts, truncates and counts."""

    trial_id: str
    events: list[TrajectoryEvent] = field(default_factory=list)
    dropped: int = 0
    _started: datetime = field(default_factory=utcnow)
    _tokens: int = 0

    def append(self, kind: EventKind, payload: dict[str, Any]) -> TrajectoryEvent | None:
        """Record one step. Returns ``None`` once the cap is reached."""
        if len(self.events) >= MAX_EVENTS:
            self.dropped += 1
            return None

        if kind is EventKind.MODEL_USAGE:
            self._tokens = int(payload.get("cumulative_tokens") or self._tokens)

        safe = redact_payload(_truncate_payload(payload))
        now = utcnow()
        event = TrajectoryEvent(
            seq=len(self.events),
            ts=now,
            kind=kind,
            payload=safe if isinstance(safe, dict) else {"value": safe},
            elapsed_ms=int((now - self._started).total_seconds() * 1000),
            cumulative_tokens=self._tokens or None,
        )
        self.events.append(event)
        return event

    def to_jsonl(self) -> str:
        """The archived form. One JSON object per line, streamable and greppable."""
        lines = [json.dumps(e.to_json(), default=str) for e in self.events]
        if self.dropped:
            lines.append(
                json.dumps(
                    {
                        "seq": len(self.events),
                        "ts": utcnow().isoformat(),
                        "kind": "status",
                        "payload": {
                            "message": (
                                f"{self.dropped} further events were dropped after the "
                                f"{MAX_EVENTS}-event cap"
                            ),
                            "truncated": True,
                        },
                    }
                )
            )
        return "\n".join(lines)

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for event in self.events:
            kinds[event.kind.value] = kinds.get(event.kind.value, 0) + 1
        return {
            "trial_id": self.trial_id,
            "events": len(self.events),
            "dropped": self.dropped,
            "kinds": kinds,
            "tokens": self._tokens,
        }


def _truncate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Cap the long fields. The full text lives in the stdout artifact."""
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and len(value) > MAX_PAYLOAD_CHARS:
            head = value[: MAX_PAYLOAD_CHARS // 2]
            tail = value[-MAX_PAYLOAD_CHARS // 2 :]
            out[key] = f"{head}\n… {len(value) - MAX_PAYLOAD_CHARS} chars elided …\n{tail}"
            out[f"{key}_truncated"] = True
        else:
            out[key] = value
    return out
