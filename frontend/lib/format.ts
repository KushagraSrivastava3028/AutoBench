/** Formatting and the status vocabulary the whole UI shares. */

import type { Interval, MatrixCell, RunStatus, TrialStatus } from "./types";

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function points(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)} pts`;
}

export function usd(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  if (value > 0 && value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function count(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-US");
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  const seconds = ms / 1000;
  if (seconds < 1) return `${ms}ms`;
  if (seconds < 60) return `${seconds.toFixed(2)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function seconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return duration(value * 1000);
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const elapsed = Date.now() - new Date(iso).getTime();
  const minutes = Math.round(elapsed / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export function timestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-GB", { hour12: false });
}

/** `54.8% ±2.1%` — value in high-contrast, delta muted. Never a bare point. */
export function withInterval(interval: Interval | null | undefined, digits = 1): string {
  if (!interval) return "—";
  return `${pct(interval.point, digits)} ±${(interval.margin * 100).toFixed(digits)}`;
}

export function shortId(id: string | null | undefined, length = 8): string {
  if (!id) return "—";
  return id.replace(/-/g, "").slice(0, length);
}

export function tokens(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

// ---------------------------------------------------------------------------
// status vocabulary
// ---------------------------------------------------------------------------

export type Tone = "pass" | "fail-agent" | "fail-infra" | "running" | "warn" | "queued" | "neutral";

export const TONE_CLASS: Record<Tone, string> = {
  pass: "border-[rgb(16_185_129/0.3)] bg-[rgb(16_185_129/0.12)] text-pass-text",
  "fail-agent": "border-[rgb(244_63_94/0.3)] bg-[rgb(244_63_94/0.12)] text-fail-agent",
  "fail-infra": "border-[rgb(168_85_247/0.3)] bg-[rgb(168_85_247/0.12)] text-fail-infra-text",
  running: "border-[rgb(34_211_238/0.3)] bg-[rgb(34_211_238/0.12)] text-running",
  warn: "border-[rgb(245_158_11/0.3)] bg-[rgb(245_158_11/0.12)] text-warn",
  queued: "border-border-muted bg-raised text-ink-muted",
  neutral: "border-border-muted bg-raised text-ink-muted",
};

/**
 * Infrastructure failures are purple, agent failures are rose.
 *
 * They never share a colour. An operator glancing at a matrix has to be able
 * to tell "the model got it wrong" from "our Docker daemon fell over" without
 * reading a label, because the two demand completely different responses.
 */
export function isInfra(errorClass: string | null | undefined): boolean {
  return Boolean(errorClass && (errorClass.startsWith("infra_") || errorClass === "verifier_crash"));
}

export function runTone(status: RunStatus): Tone {
  switch (status) {
    case "completed":
      return "pass";
    case "running":
      return "running";
    case "failed":
      return "fail-agent";
    case "cancelled":
      return "warn";
    default:
      return "queued";
  }
}

export function trialTone(status: TrialStatus, errorClass?: string | null): Tone {
  if (isInfra(errorClass)) return "fail-infra";
  switch (status) {
    case "scored":
      return "pass";
    case "error":
      return "fail-infra";
    case "timeout":
      return "warn";
    case "cancelled":
      return "neutral";
    case "queued":
      return "queued";
    default:
      return "running";
  }
}

export type CellState = "pass" | "partial" | "fail" | "retry" | "queued" | "error" | "running";

export function cellState(cell: MatrixCell): CellState {
  if (isInfra(cell.error_class)) return cell.status === "queued" ? "retry" : "error";
  if (cell.status === "queued") return "queued";
  if (["leased", "setup", "agent", "verify"].includes(cell.status)) return "running";
  if (cell.binary_pass) return "pass";
  if ((cell.reward ?? 0) > 0) return "partial";
  return "fail";
}

export const CELL_CLASS: Record<CellState, string> = {
  pass: "bg-pass/10 text-pass-text",
  partial: "bg-warn/10 text-warn",
  fail: "bg-fail-agent/10 text-fail-agent",
  retry: "bg-fail-infra/10 text-fail-infra",
  error: "bg-fail-infra/15 text-fail-infra",
  running: "bg-running/10 text-accent pulse-dot",
  queued: "bg-raised text-ink-muted",
};

export const CELL_LABEL: Record<CellState, string> = {
  pass: "pass",
  partial: "partial credit",
  fail: "fail",
  retry: "infra retry",
  error: "infra error",
  running: "running",
  queued: "queued",
};

/** Human-readable, and honest about which side of the harness failed. */
export function describeError(errorClass: string | null | undefined): string {
  if (!errorClass) return "—";
  const map: Record<string, string> = {
    agent_timeout: "Agent ran out of time",
    agent_tool_cap: "Tool-call budget exhausted",
    agent_token_cap: "Token budget exhausted",
    agent_error: "Agent errored",
    agent_refused: "Model refused",
    infra_image: "Image build/pull failed",
    infra_docker: "Container lifecycle failed",
    infra_setup: "Setup script failed",
    infra_snapshot: "Snapshot/diff failed",
    infra_persist: "Persistence failed",
    infra_provider: "Model provider error",
    infra_abandoned: "Worker never reported back",
    verifier_crash: "Verifier crashed",
    cancelled: "Cancelled",
  };
  return map[errorClass] ?? errorClass;
}
