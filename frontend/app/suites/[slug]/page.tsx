import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError, safe } from "@/lib/server-api";
import { pct } from "@/lib/format";
import { Chip, EmptyState, Mono, PageHeader, Panel, Sparkline } from "@/components/ui/primitives";
import type { LeaderboardRow } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return { title: slug };
}

export default async function SuitePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;

  let suite;
  try {
    suite = await api.suite(slug);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const board = await safe(api.leaderboard(slug), [] as LeaderboardRow[]);

  return (
    <div className="space-y-3">
      <PageHeader
        title={suite.name}
        description={suite.description ?? `${suite.member_count} pinned task versions.`}
        badge={<Chip tone="neutral">{suite.slug}</Chip>}
        actions={
          <Link href={`/leaderboard?suite=${suite.slug}`} className="btn btn-ghost">
            Leaderboard
          </Link>
        }
      />

      <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Panel title="Members" subtitle={`${suite.members.length} tasks`}>
          {suite.members.length === 0 ? (
            <EmptyState title="Empty suite" body="This suite pins no task versions." />
          ) : (
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Title</th>
                  <th>Difficulty</th>
                  <th className="text-right">Weight</th>
                </tr>
              </thead>
              <tbody>
                {suite.members.map((member) => (
                  <tr key={member.task_version_id}>
                    <td>
                      <Link href={`/tasks/${member.task_id}`} className="mono text-accent hover:underline">
                        {member.task_id}
                      </Link>
                    </td>
                    <td className="max-w-md truncate text-ink-muted">{member.title}</td>
                    <td>
                      <Chip tone="neutral">{member.difficulty}</Chip>
                    </td>
                    <td className="mono tnum text-right">{member.weight.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Best runs" bodyClassName="p-3 space-y-2">
          {board.length === 0 ? (
            <p className="text-[11px] leading-relaxed text-ink-inert">
              No completed runs on this suite yet.
            </p>
          ) : (
            board.slice(0, 6).map((row) => (
              <Link
                key={row.run_id}
                href={`/runs/${row.run_id}`}
                className="block rounded-sm border border-border-subtle bg-canvas p-2.5 hover:border-border-muted"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <Mono className="truncate text-[12px] text-ink">{row.model_slug}</Mono>
                  <Mono className="shrink-0 text-[13px] text-pass-text">
                    {pct(row.pass_at_1.point)}
                  </Mono>
                </div>
                <div className="mt-1 flex items-baseline justify-between text-[11px] text-ink-inert">
                  <span>{row.agent_slug} · k={row.k}</span>
                  <span>±{(row.pass_at_1.margin * 100).toFixed(1)}</span>
                </div>
              </Link>
            ))
          )}
        </Panel>
      </div>
    </div>
  );
}
