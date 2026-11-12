import Link from "next/link";
import { NewSuiteButton } from "@/components/suites/NewSuiteButton";

import { api, safe } from "@/lib/server-api";
import { relativeTime } from "@/lib/format";
import { EmptyState, Mono, PageHeader, Panel } from "@/components/ui/primitives";
import type { Suite } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Suites" };

export default async function SuitesPage() {
  const suites = await safe(api.suites(), [] as Suite[]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Suites"
        actions={<NewSuiteButton />}
        description="Build reproducible benchmarks from pinned task versions."
      />

      <Panel>
        {suites.length === 0 ? (
          <EmptyState
            title="No suites"
            body="Register the bundled corpus to create the reference suite used for harness benchmarking and regression gating."
            command="autobench seed"
          />
        ) : (
          <table className="telemetry-table">
            <thead>
              <tr>
                <th>Slug</th>
                <th>Name</th>
                <th>Description</th>
                <th className="text-right">Tasks</th>
                <th className="text-right">Created</th>
              </tr>
            </thead>
            <tbody>
              {suites.map((suite) => (
                <tr key={suite.id}>
                  <td>
                    <Link href={`/suites/${suite.slug}`} className="mono text-accent hover:underline">
                      {suite.slug}
                    </Link>
                  </td>
                  <td className="text-ink">{suite.name}</td>
                  <td className="max-w-lg truncate text-[11px] text-ink-muted">
                    {suite.description ?? "—"}
                  </td>
                  <td className="mono tnum text-right">{suite.member_count}</td>
                  <td className="text-right text-[11px] text-ink-muted">
                    {relativeTime(suite.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
