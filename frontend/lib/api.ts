/**
 * API client.
 *
 * One `request` helper, RFC 9457 aware, usable from both server and client
 * components. Server components hit the API directly; client components go
 * through the Next rewrite at `/api` so the browser makes same-origin requests
 * and SSE needs no CORS negotiation.
 */

import type {
  Agent,
  Comparison,
  Dashboard,
  Diff,
  LeaderboardRow,
  Matrix,
  Model,
  Replay,
  RunDetail,
  RunSummary,
  Suite,
  SuiteDetail,
  Task,
  TaskDetail,
  Trajectory,
  Trial,
  Worker,
  Overview,
  Budget,
} from "./types";

const SERVER_BASE = process.env.AUTOBENCH_API_URL ?? "http://localhost:8000";
const isServer = typeof window === "undefined";

/** An API error carrying the problem+json body, so callers can show `detail`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly title: string,
    readonly detail: string,
    readonly problem?: Record<string, unknown>,
  ) {
    super(`${status} ${title}: ${detail}`);
    this.name = "ApiError";
  }
}

function url(path: string): string {
  const clean = path.startsWith("/") ? path : `/${path}`;
  return isServer ? `${SERVER_BASE}/v1${clean}` : `/api${clean}`;
}

export interface RequestOptions extends RequestInit {
  /** Server components render fresh by default; opt in to caching explicitly. */
  revalidate?: number;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { revalidate, ...init } = options;

  const response = await fetch(url(path), {
    ...init,
    signal: init.signal ?? AbortSignal.timeout(15_000),
    headers: {
      "content-type": "application/json",
      ...init.headers,
    },
    ...(isServer
      ? { next: revalidate !== undefined ? { revalidate } : { revalidate: 0 } }
      : { cache: "no-store" }),
  });

  if (!response.ok) {
    let problem: Record<string, unknown> = {};
    try {
      problem = await response.json();
    } catch {
      /* a proxy or gateway error has no JSON body */
    }
    throw new ApiError(
      response.status,
      String(problem.title ?? response.statusText),
      String(problem.detail ?? "The request failed."),
      problem,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
}

/**
 * Server-side reads that must not take a page down.
 *
 * A dashboard that 500s because one panel's query failed is worse than a
 * dashboard with one empty panel, so pages use this and render their designed
 * empty state instead.
 */
export async function safe<T>(promise: Promise<T>, fallback: T): Promise<T> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return fallback;
    throw error;
  }
}

const baseRequest = request;
export function createApi(headers?: () => Promise<Record<string, string>>) {
  const call = baseRequest;
  async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return call<T>(path, { ...options, headers: { ...(await headers?.()), ...options.headers } });
  }
  function post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined });
  }
  return {
  overview: (days = 7, suite = "") => request<Overview>(`/console/overview${query({ days, suite })}`),
  budget: () => request<Budget>("/console/budget"),
  updateBudget: (limit: number | null) => request<Budget>("/console/budget", { method: "PUT", body: JSON.stringify({ weekly_limit_usd: limit }) }),
  dashboard: () => request<Dashboard>("/stats/dashboard"),
  workers: () => request<Worker[]>("/stats/workers"),

  runs: (params?: Record<string, string | number | undefined>) =>
    request<RunSummary[]>(`/runs${query(params)}`),
  run: (id: string) => request<RunDetail>(`/runs/${id}`),
  matrix: (id: string) => request<Matrix>(`/runs/${id}/matrix`),
  cancelRun: (id: string) => post<RunSummary>(`/runs/${id}/cancel`),
  retryFailed: (id: string) => post<RunSummary>(`/runs/${id}/retry-failed`),
  launchRun: (body: {
    suite: string;
    agent: string;
    model: string;
    k: number;
    concurrency: number;
    label?: string;
    budget_usd?: number;
  }) => post<RunSummary>("/runs", body),

  trial: (id: string) => request<Trial>(`/trials/${id}`),
  trajectory: (id: string, offset = 0, limit = 400) =>
    request<Trajectory>(`/trials/${id}/trajectory?offset=${offset}&limit=${limit}`),
  diff: (id: string) => request<Diff>(`/trials/${id}/diff`),
  replay: (id: string) => request<Replay>(`/trials/${id}/replay`),

  tasks: (params?: Record<string, string | number | undefined>) =>
    request<Task[]>(`/tasks${query(params)}`),
  task: (id: string) => request<TaskDetail>(`/tasks/${id}`),

  suites: () => request<Suite[]>("/suites"),
  suite: (ref: string) => request<SuiteDetail>(`/suites/${ref}`),

  agents: () => request<Agent[]>("/agents"),
  models: () => request<Model[]>("/models"),

  compare: (a: string, b: string) => request<Comparison>(`/stats/compare?a=${a}&b=${b}`),
  leaderboard: (suite: string) =>
    request<LeaderboardRow[]>(`/stats/leaderboard?suite=${encodeURIComponent(suite)}`),
  };
}
export const api = createApi();

function query(params?: Record<string, string | number | undefined>): string {
  if (!params) return "";
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== "");
  if (entries.length === 0) return "";
  return `?${entries.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&")}`;
}
