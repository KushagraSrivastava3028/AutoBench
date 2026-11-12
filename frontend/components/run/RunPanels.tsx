"use client";

/**
 * The tabbed lower half of the run view: failures, cost, latency, workers.
 *
 * The failure table splits infra from agent explicitly and never sorts them
 * together. An operator triaging a red run needs to know within a second
 * whether to look at the model or at the Docker host.
 */

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import clsx from "clsx";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell as RechartsCell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, request } from "@/lib/api";
import { count, describeError, duration, isInfra, pct, usd } from "@/lib/format";
import { Chip, EmptyState, Meter, Mono, Panel } from "@/components/ui/primitives";
import type { Matrix, MatrixCell, RunDetail, Worker, Provenance as ProvenanceData } from "@/lib/types";

function Provenance({ runId }: { runId: string }) {
  const query = useQuery({ queryKey: ["provenance", runId], queryFn: () => request<ProvenanceData>(`/runs/${runId}/provenance`) });
  if (query.isError) return <p role="alert" className="info-banner warning">Provenance could not be loaded.</p>;
  if (!query.data) return <p className="quiet-empty">Loading provenance…</p>;
  const data = query.data;
  return <div className="space-y-5"><dl className="grid gap-4 text-xs sm:grid-cols-4">{[["Harness version", data.harness_version], ["Agent version", data.agent_version], ["Pricing version", data.pricing_version], ["Run seed", data.seed_exact]].map(([label, value]) => <div key={label}><dt className="text-ink-muted">{label}</dt><dd className="mono mt-2">{value ?? "Not recorded for this run"}</dd></div>)}</dl><div className="overflow-x-auto"><table className="telemetry-table"><thead><tr><th>Task</th><th>Version</th><th>Content hash</th><th>Image digest</th></tr></thead><tbody>{data.tasks.map(t => <tr key={t.version_id}><td><Link href={`/tasks/${t.task_id}`} className="tile-link">{t.task_id}</Link></td><td className="mono">{t.semver}</td><td className="mono" title={t.content_hash}>{t.content_hash.slice(0, 23)}…</td><td className="mono" title={t.image_digest ?? undefined}>{t.image_digest ? `${t.image_digest.slice(0, 23)}…` : "Not recorded"}</td></tr>)}</tbody></table></div></div>;
}

type Tab = "failures" | "cost" | "latency" | "workers" | "provenance";

const AXIS = { stroke: "var(--muted)", fontSize: 10, fontFamily: "JetBrains Mono" };
const GRID = "var(--line)";

export function RunPanels({ run, matrix }: { run: RunDetail; matrix: Matrix }) {
  const [tab, setTab] = useState<Tab>("failures");

  const { data: live } = useQuery({
    queryKey: ["matrix", run.id],
    queryFn: () => api.matrix(run.id),
    initialData: matrix,
  });

  const failures = useMemo(() => {
    const out: Array<{ task: string; cell: MatrixCell }> = [];
    for (const row of live?.rows ?? []) {
      for (const cell of row.cells) {
        const failed =
          cell.binary_pass === false || Boolean(cell.error_class) || cell.status === "error";
        if (failed && cell.status !== "queued") out.push({ task: row.task_id, cell });
      }
    }
    return out;
  }, [live?.rows]);

  const infra = failures.filter((f) => isInfra(f.cell.error_class));
  const agent = failures.filter((f) => !isInfra(f.cell.error_class));

  const tabs: Array<[Tab, string]> = [
    ["failures", `Failures (${failures.length})`],
    ["cost", "Cost"],
    ["latency", "Latency"],
    ["workers", "Workers"],
    ["provenance", "Provenance"],
  ];

  return (
    <Panel
      title={
        <div className="flex flex-wrap items-center gap-1" role="tablist" aria-label="Run details">
          {tabs.map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setTab(value)}
              role="tab"
              aria-selected={tab === value}
              className={clsx(
                "relative px-2.5 py-1 text-[12px] font-medium transition-colors",
                tab === value ? "text-ink" : "text-ink-muted hover:text-ink",
              )}
            >
              {label}
              {tab === value && (
                <span className="absolute inset-x-1 -top-2.5 h-0.5 rounded-full bg-accent" />
              )}
            </button>
          ))}
        </div>
      }
      bodyClassName="p-3"
    >
      {tab === "failures" && <Failures run={run} infra={infra} agent={agent} />}
      {tab === "cost" && <Cost run={run} matrix={live ?? matrix} />}
      {tab === "latency" && <Latency matrix={live ?? matrix} />}
      {tab === "workers" && <Workers />}
      {tab === "provenance" && <Provenance runId={run.id} />}
    </Panel>
  );
}

// ---------------------------------------------------------------------------

function Failures({
  run,
  infra,
  agent,
}: {
  run: RunDetail;
  infra: Array<{ task: string; cell: MatrixCell }>;
  agent: Array<{ task: string; cell: MatrixCell }>;
}) {
  if (infra.length === 0 && agent.length === 0) {
    return (
      <EmptyState
        title="No failures"
        body="Every scored trial passed. If this is a k=1 run, remember that one attempt per task is a sample, not a measurement."
      />
    );
  }

  return (
    <div className="space-y-4">
      {infra.length > 0 && (
        <section>
          <div className="mb-2 flex items-center gap-2">
            <Chip tone="fail-infra">Infrastructure · {infra.length}</Chip>
            <span className="text-[11px] leading-snug text-ink-muted">
              The harness failed, not the agent. These are retried and contribute nothing to the
              score.
            </span>
          </div>
          <FailureTable runId={run.id} rows={infra} />
        </section>
      )}

      {agent.length > 0 && (
        <section>
          <div className="mb-2 flex items-center gap-2">
            <Chip tone="fail-agent">Agent · {agent.length}</Chip>
            <span className="text-[11px] leading-snug text-ink-muted">
              The agent's result. Never retried, always scored 0.0.
            </span>
          </div>
          <FailureTable runId={run.id} rows={agent} />
        </section>
      )}
    </div>
  );
}

function FailureTable({
  runId,
  rows,
}: {
  runId: string;
  rows: Array<{ task: string; cell: MatrixCell }>;
}) {
  const grouped = useMemo(() => {
    const map = new Map<string, Array<{ task: string; cell: MatrixCell }>>();
    for (const row of rows) {
      const key = row.cell.error_class ?? "wrong_answer";
      map.set(key, [...(map.get(key) ?? []), row]);
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [rows]);

  return (
    <div className="space-y-3">
      {grouped.map(([errorClass, entries]) => (
        <div key={errorClass} className="rounded-sm border border-border-subtle">
          <div className="flex items-center justify-between border-b border-border-subtle bg-canvas px-2.5 py-1.5">
            <Mono className="text-[11px] text-ink">{describeError(errorClass)}</Mono>
            <Mono className="text-[11px] text-ink-muted">{entries.length}</Mono>
          </div>
          <table className="telemetry-table">
            <tbody>
              {entries.slice(0, 25).map(({ task, cell }) => (
                <tr key={cell.trial_id}>
                  <td className="w-64 truncate">{task}</td>
                  <td className="mono tnum w-16 text-ink-muted">k{cell.attempt}</td>
                  <td className="mono tnum w-20 text-ink-muted">
                    {cell.reward !== null ? cell.reward.toFixed(3) : "—"}
                  </td>
                  <td className="mono tnum w-24 text-ink-muted">{duration(cell.duration_ms)}</td>
                  <td className="text-right">
                    <Link
                      href={`/runs/${runId}/trials/${cell.trial_id}`}
                      className="mono text-accent hover:underline"
                    >
                      inspect →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {entries.length > 25 && (
            <div className="px-2.5 py-1.5 text-[11px] text-ink-inert">
              and {entries.length - 25} more
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function Cost({ run, matrix }: { run: RunDetail; matrix: Matrix }) {
  const series = useMemo(() => {
    let cumulative = 0;
    return matrix.rows.flatMap(row => row.cells.map(cell => ({ ...cell, task: row.task_id })))
      .filter(cell => cell.cost_usd !== null)
      .sort((a, b) => (a.finished_at ?? "").localeCompare(b.finished_at ?? ""))
      .map(cell => { cumulative += cell.cost_usd ?? 0; return { trial: `${cell.task}·k${cell.attempt}`, usd: Number(cumulative.toFixed(6)) }; });
  }, [matrix.rows, run.cost_usd, run.trials_done]);

  if (run.cost_usd === 0) {
    return (
      <EmptyState
        title="No spend recorded"
        body="This run used an offline or unpriced model. A model missing from the pricing table is reported as unpriced rather than as $0.00 — the second would be a more expensive kind of lie."
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Total" value={usd(run.cost_usd)} />
        <Stat
          label="Per trial"
          value={usd(run.trials_done ? run.cost_usd / run.trials_done : 0, 4)}
        />
        <Stat
          label="Per solve"
          value={
            run.metrics?.total_passes
              ? usd(run.cost_usd / run.metrics.total_passes, 4)
              : "—"
          }
        />
      </div>
      <div className="h-52">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis dataKey="trial" tick={AXIS} axisLine={{ stroke: GRID }} tickLine={false} hide />
            <YAxis tick={AXIS} axisLine={false} tickLine={false} width={52} />
            <Tooltip
              contentStyle={{
                background: "var(--surface)",
                border: "1px solid var(--line)",
                borderRadius: 4,
                fontFamily: "JetBrains Mono",
                fontSize: 11,
              }}
              formatter={(value: number) => [usd(value, 4), "cumulative"]}
            />
            <Area
              type="monotone"
              dataKey="usd"
              stroke="#38bdf8"
              fill="#38bdf8"
              fillOpacity={0.14}
              strokeWidth={1.5}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Latency({ matrix }: { matrix: Matrix }) {
  const { buckets, p50, p95 } = useMemo(() => {
    const durations = matrix.rows
      .flatMap((row) => row.cells)
      .map((cell) => cell.duration_ms)
      .filter((value): value is number => typeof value === "number" && value > 0)
      .sort((a, b) => a - b);

    if (durations.length === 0) return { buckets: [], p50: 0, p95: 0 };

    const max = durations[durations.length - 1]!;
    const width = Math.max(1, Math.ceil(max / 18));
    const counts = new Map<number, number>();
    for (const value of durations) {
      const bucket = Math.floor(value / width) * width;
      counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
    }
    return {
      buckets: [...counts.entries()]
        .sort((a, b) => a[0] - b[0])
        .map(([bucket, n]) => ({ bucket: (bucket / 1000).toFixed(1), n, raw: bucket })),
      p50: durations[Math.floor(durations.length * 0.5)] ?? 0,
      p95: durations[Math.min(durations.length - 1, Math.floor(durations.length * 0.95))] ?? 0,
    };
  }, [matrix.rows]);

  if (buckets.length === 0) {
    return <EmptyState title="No completed trials" body="Latency appears once trials finish." />;
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="p50" value={duration(p50)} />
        <Stat label="p95" value={duration(p95)} />
        <Stat label="Trials" value={count(matrix.rows.flatMap((r) => r.cells).length)} />
      </div>
      <div className="h-52">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={buckets} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke={GRID} vertical={false} />
            <XAxis
              dataKey="bucket"
              tick={AXIS}
              axisLine={{ stroke: GRID }}
              tickLine={false}
              label={{ value: "seconds", position: "insideBottomRight", fill: "#475569", fontSize: 10 }}
            />
            <YAxis tick={AXIS} axisLine={false} tickLine={false} width={32} />
            <Tooltip
              cursor={{ fill: "#131822" }}
              contentStyle={{
                background: "#131822",
                border: "1px solid #273147",
                borderRadius: 4,
                fontFamily: "JetBrains Mono",
                fontSize: 11,
              }}
            />
            <ReferenceLine
              x={(p95 / 1000).toFixed(1)}
              stroke="#f59e0b"
              strokeDasharray="3 3"
              label={{ value: "p95", fill: "#f59e0b", fontSize: 10 }}
            />
            <Bar dataKey="n" radius={[2, 2, 0, 0]}>
              {buckets.map((bucket) => (
                <RechartsCell
                  key={bucket.bucket}
                  fill={bucket.raw >= p95 ? "#f59e0b" : "#38bdf8"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Workers() {
  const { data = [] } = useQuery<Worker[]>({
    queryKey: ["workers"],
    queryFn: () => api.workers(),
    refetchInterval: 10_000,
  });

  if (data.length === 0) {
    return (
      <EmptyState
        title="No workers registered"
        body="Trials stay queued until a worker leases them. Start one, or use the single-command path which runs a worker in-process."
        command="autobench worker"
      />
    );
  }

  return (
    <div className="space-y-3">
      {data.map((worker) => (
        <div key={worker.id} className="rounded-sm border border-border-subtle p-3">
          <div className="mb-2.5 flex flex-wrap items-center gap-2">
            <Mono className="text-[12px] text-ink">{worker.hostname}</Mono>
            <span className="mono text-[11px] text-ink-inert">pid {worker.pid}</span>
            <Chip tone={worker.stale ? "warn" : "pass"} pulse={!worker.stale}>
              {worker.stale ? "stale" : "healthy"}
            </Chip>
            <span className="mono text-[11px] text-ink-muted">{worker.executor}</span>
            <Mono className="ml-auto text-[11px] text-ink-muted">
              {worker.active_trials} active
            </Mono>
          </div>

          <div className="grid gap-2.5 sm:grid-cols-3">
            {Object.entries(worker.lanes).map(([lane, stats]) => (
              <div key={lane}>
                <div className="mb-1 flex items-baseline justify-between">
                  <span className="text-[11px] text-ink-muted">{lane}</span>
                  <Mono className="text-[11px] text-ink">
                    {stats.in_use}/{stats.capacity}
                  </Mono>
                </div>
                <Meter value={stats.utilisation} tone={stats.utilisation > 0.85 ? "warn" : "accent"} />
                <div className="mt-1 text-[10px] text-ink-inert">
                  {pct(stats.utilisation, 0)} saturated · peak {stats.peak}
                </div>
              </div>
            ))}
          </div>

          {worker.bottleneck && (
            <p className="mt-2.5 text-[11px] leading-snug text-warn">
              Bottleneck: <span className="mono">{worker.bottleneck}</span>. This lane is saturated
              most of the time; raising the others will not make the run faster.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-sm border border-border-subtle bg-canvas p-2.5">
      <div className="text-[10px] uppercase tracking-[0.04em] text-ink-inert">{label}</div>
      <Mono className="text-[15px] text-ink">{value}</Mono>
    </div>
  );
}
