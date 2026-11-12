"use client";

/**
 * SSE with exponential-backoff reconnect and `Last-Event-ID` resume.
 *
 * A dropped connection must not lose cell updates: the server keeps a replay
 * buffer keyed on the event id, so reconnecting with the last id seen fills the
 * gap rather than leaving the matrix permanently stale. That matters because a
 * 250-trial run is exactly long enough for a laptop to sleep in the middle of it.
 */

import { useEffect, useRef, useState } from "react";
import type { RunEvent } from "./types";

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 500;

export type ConnectionState = "connecting" | "open" | "reconnecting" | "polling" | "closed";

export interface UseRunEventsOptions {
  /** Stop listening once the run reaches a terminal state. */
  enabled?: boolean;
  onEvent?: (event: RunEvent) => void;
}

export function useRunEvents(
  runId: string | null,
  { enabled = true, onEvent }: UseRunEventsOptions = {},
): { state: ConnectionState; lastEvent: RunEvent | null } {
  const [state, setState] = useState<ConnectionState>(enabled ? "connecting" : "closed");
  const [lastEvent, setLastEvent] = useState<RunEvent | null>(null);

  // Held in a ref so a changing callback identity does not tear down the stream.
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    if (!runId || !enabled) {
      setState("closed");
      return;
    }

    let source: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let lastId = 0;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      const url = `/api/runs/${runId}/events${lastId ? `?Last-Event-ID=${lastId}` : ""}`;
      source = new EventSource(url);

      source.onopen = () => {
        attempt = 0;
        setState("open");
      };

      const receive = (message: MessageEvent<string>) => {
        if (message.lastEventId) {
          const parsed = Number(message.lastEventId);
          if (!Number.isNaN(parsed)) lastId = parsed;
        }
        try {
          const payload = JSON.parse(message.data) as RunEvent;
          setLastEvent(payload);
          handler.current?.(payload);
        } catch {
          /* a heartbeat comment frame has no data */
        }
      };

      source.addEventListener("trial_update", receive as EventListener);
      source.addEventListener("run_progress", receive as EventListener);
      source.addEventListener("run_cancelled", receive as EventListener);
      source.onmessage = receive;

      source.onerror = () => {
        source?.close();
        if (disposed) return;
        setState(attempt >= 2 ? "polling" : "reconnecting");
        // Full jitter: many tabs on one run must not all reconnect together.
        const ceiling = Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
        attempt += 1;
        retryTimer = setTimeout(connect, Math.random() * ceiling);
      };
    };

    connect();

    return () => {
      disposed = true;
      if (retryTimer) clearTimeout(retryTimer);
      source?.close();
      setState("closed");
    };
  }, [runId, enabled]);

  return { state, lastEvent };
}

export const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "Connecting",
  open: "Connected",
  reconnecting: "Reconnecting",
  closed: "Closed",
  polling: "Polling every 5s",
};
