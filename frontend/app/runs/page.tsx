import Link from "next/link";
import { NewRunButton } from "@/components/run/NewRunButton";

import { api, safe } from "@/lib/server-api";
import { count, relativeTime, runTone, usd } from "@/lib/format";
import { Chip, EmptyState, Meter, Mono, PageHeader, Panel } from "@/components/ui/primitives";
import type { RunSummary } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Runs" };

export default async function RunsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; suite?: string }>;
}) {
  const filters = await searchParams;
  const runs = await safe(
    api.runs({ status: filters.status, suite: filters.suite, limit: 100 }),
    [] as RunSummary[],
  );

  const statuses = ["all", "running", "completed", "failed", "cancelled", "queued"];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Runs"
        actions={<NewRunButton />}
        description="Launch evaluations, follow live progress, and revisit every result."
      />

      <div className="flex flex-wrap items-center gap-1.5">
        {statuses.map((status) => {
          const active = (filters.status ?? "all") === status;
          return (
            <Link
              key={status}
              href={status === "all" ? "/runs" : `/runs?status=${status}`}
              className={
                active
                  ? "chip border-accent/40 bg-accent/15 text-accent"
                  : "chip border-border-muted bg-raised text-ink-muted hover:text-ink"
              }
            >
              {status}
            </Link>
          );
        })}
      </div>

      <Panel>
        {runs.length === 0 ? (
          <EmptyState
            title="No runs match"
            body="Launch a run from the button in the top bar, or from the terminal. Start with the oracle agent — it replays each task's own solution, proving the harness works before a model is involved."
            command="autobench run --suite core-reference --agent oracle --k 3"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Suite</th>
                  <th>Agent</th>
                  <th>Model</th>
                  <th className="text-right">k</th>
                  <th className="text-right">Conc.</th>
                  <th>Status</th>
                  <th className="text-right">Progress</th>
                  <th className="text-right">Cost</th>
                  <th className="text-right">Created</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link
                        href={`/runs/${run.id}`}
                        className="mono text-accent hover:text-accent-hover hover:underline"
                      >
                        {run.id.replace(/-/g, "").slice(0, 8)}
                      </Link>
                      {run.label && (
                        <div className="truncate text-[11px] text-ink-inert">{run.label}</div>
                      )}
                    </td>
                    <td className="mono text-ink-muted">{run.suite_slug}</td>
                    <td className="text-ink-muted">{run.agent_slug}</td>
                    <td className="mono text-ink">{run.model_slug}</td>
                    <td className="mono tnum text-right">{run.k}</td>
                    <td className="mono tnum text-right text-ink-muted">{run.concurrency}</td>
                    <td>
                      <Chip tone={runTone(run.status)} pulse={run.status === "running"}>
                        {run.status}
                      </Chip>
                    </td>
                    <td className="text-right">
                      <Mono className="text-[12px]">
                        {count(run.trials_done)}/{count(run.trials_total)}
                      </Mono>
                      <div className="mt-1 w-20 justify-self-end">
                        <Meter value={run.trials_total ? run.trials_done / run.trials_total : 0} />
                      </div>
                    </td>
                    <td className="mono tnum text-right">{usd(run.cost_usd)}</td>
                    <td className="text-right text-[11px] text-ink-muted">
                      {relativeTime(run.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
