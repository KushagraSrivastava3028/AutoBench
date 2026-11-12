import Link from "next/link";

import { api, safe } from "@/lib/server-api";
import { pct, points, relativeTime, seconds, usd } from "@/lib/format";
import { Chip, EmptyState, Mono, PageHeader, Panel } from "@/components/ui/primitives";
import type { LeaderboardRow, Suite } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Leaderboard" };

export default async function LeaderboardPage({
  searchParams,
}: {
  searchParams: Promise<{ suite?: string }>;
}) {
  const { suite } = await searchParams;
  const suites = await safe(api.suites(), [] as Suite[]);
  const selected = suite ?? suites[0]?.slug;
  const rows = selected ? await safe(api.leaderboard(selected), [] as LeaderboardRow[]) : [];

  // Anything whose interval overlaps the leader's is rendered neutral, not
  // green. "Best" without an error bar is a ranking of noise.
  const leader = rows[0];

  return (
    <div className="space-y-4">
      <PageHeader
        title="Leaderboard"
        description="Best run per agent and model, with 95% intervals. Rows whose interval overlaps the leader's are shown neutral — they are not distinguishable from it at this number of trials."
      />

      <div className="flex flex-wrap items-center gap-1.5">
        {suites.map((entry) => (
          <Link
            key={entry.id}
            href={`/leaderboard?suite=${entry.slug}`}
            className={
              selected === entry.slug
                ? "chip border-accent/40 bg-accent/15 text-accent"
                : "chip border-border-muted bg-raised text-ink-muted hover:text-ink"
            }
          >
            {entry.slug}
          </Link>
        ))}
      </div>

      <Panel subtitle={selected}>
        {rows.length === 0 ? (
          <EmptyState
            title="No completed runs on this suite"
            body="The leaderboard is built from completed runs only. A run still in flight has no final number, and showing a partial one invites reading it as a result."
            command={`autobench run --suite ${selected ?? "core-reference"} --k 3`}
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th className="w-8">#</th>
                  <th>Agent / model</th>
                  <th className="text-right">k</th>
                  <th className="text-right">pass@1 [95% CI]</th>
                  <th className="text-right">pass^k</th>
                  <th className="text-right">Reliability gap</th>
                  <th className="text-right">Cost / solve</th>
                  <th className="text-right">p95</th>
                  <th className="text-right">Finished</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => {
                  const ambiguous =
                    Boolean(leader) &&
                    index > 0 &&
                    row.pass_at_1.high >= leader!.pass_at_1.low;
                  return (
                    <tr key={row.run_id}>
                      <td className="mono tnum text-ink-inert">{index + 1}</td>
                      <td>
                        <Link
                          href={`/runs/${row.run_id}`}
                          className="mono text-accent hover:underline"
                        >
                          {row.model_slug}
                        </Link>
                        <div className="text-[11px] text-ink-inert">
                          {row.agent_slug}
                          {row.label ? ` · ${row.label}` : ""}
                        </div>
                      </td>
                      <td className="mono tnum text-right">
                        {row.k}
                        {!row.reportable && (
                          <span className="ml-1 text-warn" title="k<3: indicative, not reportable">
                            *
                          </span>
                        )}
                      </td>
                      <td className="text-right">
                        <Mono
                          className={
                            index === 0 && !ambiguous
                              ? "text-pass-text"
                              : ambiguous
                                ? "text-ink-muted"
                                : "text-ink"
                          }
                        >
                          {pct(row.pass_at_1.point)}
                        </Mono>
                        <Mono className="ml-1.5 text-[11px] text-ink-inert">
                          ±{(row.pass_at_1.margin * 100).toFixed(1)}
                        </Mono>
                      </td>
                      <td className="mono tnum text-right text-ink-muted">
                        {pct(row.pass_caret_k.point)}
                        <span className="cell-sub">95% CI {pct(row.pass_caret_k.low)}–{pct(row.pass_caret_k.high)}</span>
                      </td>
                      <td className="mono tnum text-right text-warn">
                        {points(row.reliability_gap)}
                      </td>
                      <td className="mono tnum text-right">{usd(row.cost_per_solve, 4)}</td>
                      <td className="mono tnum text-right text-ink-muted">
                        {seconds(row.p95_latency_sec)}
                      </td>
                      <td className="text-right text-[11px] text-ink-muted">
                        {relativeTime(row.finished_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {rows.some((row) => !row.reportable) && (
        <p className="text-[11px] text-ink-inert">
          <span className="text-warn">*</span> k&lt;3: indicative, not reportable. One attempt per
          task is a sample of a stochastic process, not a measurement of it.
        </p>
      )}
    </div>
  );
}
