import Link from "next/link";

import { api, safe, ApiError } from "@/lib/server-api";
import { pct, points, relativeTime } from "@/lib/format";
import { Chip, EmptyState, Mono, PageHeader, Panel } from "@/components/ui/primitives";
import type { Comparison, RunSummary } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Compare" };

export default async function ComparePage({
  searchParams,
}: {
  searchParams: Promise<{ a?: string; b?: string }>;
}) {
  const { a, b } = await searchParams;
  const runs = await safe(api.runs({ status: "completed", limit: 50 }), [] as RunSummary[]);
  let comparison: Comparison | null = null;
  let comparisonError = "";
  if (a && b) {
    try { comparison = await api.compare(a, b); }
    catch (error) { comparisonError = error instanceof ApiError ? error.detail : "The comparison could not be loaded. Please try again."; }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Compare runs"
        description="Compare evaluations on shared tasks, with confidence intervals and a clear list of changes."
      />

      <Panel title="Select two runs" bodyClassName="p-3">
        {runs.length < 2 ? (
          <EmptyState
            title="Need at least two completed runs"
            body="Comparison is paired: the same tasks in both runs, so the question asked is 'did these tasks get better', not 'are these two populations different'."
            command="autobench run --suite core-reference --agent oracle --k 3"
          />
        ) : (
          <form className="flex flex-wrap items-end gap-3" method="get">
            <label className="space-y-1">
              <span className="label">Baseline (A)</span>
              <select name="a" defaultValue={a ?? ""} className="input min-w-64">
                <option value="">choose…</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.label ?? run.id.slice(0, 8)} · {run.model_slug} · k={run.k}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1">
              <span className="label">Candidate (B)</span>
              <select name="b" defaultValue={b ?? ""} className="input min-w-64">
                <option value="">choose…</option>
                {runs.map((run) => (
                  <option key={run.id} value={run.id}>
                    {run.label ?? run.id.slice(0, 8)} · {run.model_slug} · k={run.k}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit" className="btn btn-primary">
              Compare
            </button>
          </form>
        )}
      </Panel>

      {comparison && <Result comparison={comparison} aId={a!} bId={b!} />}
      {comparisonError && <div className="info-banner warning" role="alert">{comparisonError}</div>}
    </div>
  );
}

function Result({
  comparison,
  aId,
  bId,
}: {
  comparison: Comparison;
  aId: string;
  bId: string;
}) {
  // The editorial rule, enforced in one place: overlapping intervals are grey.
  const tone = comparison.inconclusive
    ? "neutral"
    : comparison.delta.point > 0
      ? "pass"
      : "fail-agent";
  const deltaClass = comparison.inconclusive
    ? "text-ink-muted"
    : comparison.delta.point > 0
      ? "text-pass-text"
      : "text-fail-agent";

  return (
    <>
      <Panel bodyClassName="p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div>
            <div className="label">Baseline</div>
            <Mono className="text-xl font-semibold text-ink">
              {pct(comparison.baseline_score)}
            </Mono>
            <div className="mt-1 text-[11px] text-ink-muted">95% CI {pct(comparison.baseline_metrics.pass_at_1.low)}–{pct(comparison.baseline_metrics.pass_at_1.high)}</div>
          </div>
          <span className="text-ink-inert">→</span>
          <div>
            <div className="label">Candidate</div>
            <Mono className="text-xl font-semibold text-ink">
              {pct(comparison.candidate_score)}
            </Mono>
            <div className="mt-1 text-[11px] text-ink-muted">95% CI {pct(comparison.candidate_metrics.pass_at_1.low)}–{pct(comparison.candidate_metrics.pass_at_1.high)}</div>
          </div>
          <div>
            <div className="label">Delta</div>
            <Mono className={`text-xl font-semibold ${deltaClass}`}>
              {comparison.delta.point > 0 ? "+" : ""}
              {points(comparison.delta.point)}
            </Mono>
            <Mono className="ml-2 text-[11px] text-ink-inert">
              [{points(comparison.delta.low)}, {points(comparison.delta.high)}]
            </Mono>
          </div>
          <div>
            <div className="label">McNemar p</div>
            <Mono className="text-xl font-semibold text-ink">
              {comparison.mcnemar_p.toFixed(3)}
            </Mono>
          </div>
          <Chip tone={tone}>{comparison.inconclusive ? "inconclusive" : "significant"}</Chip>
        </div>

        <p className="mono mt-3 text-[12px] text-ink-muted">{comparison.headline}</p>

        {comparison.inconclusive && (
          <p className="mt-2 rounded-sm border border-border-muted bg-raised px-2.5 py-2 text-[12px] leading-relaxed text-ink-muted">
            The 95% interval on the difference straddles zero, so these two runs are not
            distinguishable at this number of trials. That is a statement about the sample size,
            not about the models — raise k to decide.
          </p>
        )}
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <ComparisonMetrics label="Baseline" metrics={comparison.baseline_metrics} />
        <ComparisonMetrics label="Candidate" metrics={comparison.candidate_metrics} />
      </div>
      <Panel
        title="Task flips"
        subtitle={`${comparison.shared_tasks} shared tasks`}
        actions={
          <div className="flex gap-2">
            <Link href={`/runs/${aId}`} className="text-[11px] text-accent hover:underline">
              run A →
            </Link>
            <Link href={`/runs/${bId}`} className="text-[11px] text-accent hover:underline">
              run B →
            </Link>
          </div>
        }
      >
        {comparison.flips.length === 0 ? (
          <EmptyState
            title="No tasks flipped"
            body="Every shared task scored identically in both runs. For a deterministic agent that is the expected result and a good sign about the harness."
          />
        ) : (
          <table className="telemetry-table">
            <thead>
              <tr>
                <th>Task</th>
                <th className="text-right">Baseline</th>
                <th className="text-right">Candidate</th>
                <th className="text-right">Δ</th>
                <th>Direction</th>
              </tr>
            </thead>
            <tbody>
              {comparison.flips.map((flip) => (
                <tr key={flip.task_id}>
                  <td>
                    <Link href={`/tasks/${flip.task_id}`} className="mono text-accent hover:underline">
                      {flip.task_id}
                    </Link>
                  </td>
                  <td className="mono tnum text-right text-ink-muted">{pct(flip.before, 0)}</td>
                  <td className="mono tnum text-right text-ink">{pct(flip.after, 0)}</td>
                  <td
                    className={`mono tnum text-right ${
                      comparison.inconclusive ? "text-ink-muted" : flip.direction === "improved" ? "text-pass-text" : "text-fail-agent"
                    }`}
                  >
                    {flip.after > flip.before ? "+" : ""}
                    {points(flip.after - flip.before, 0)}
                  </td>
                  <td>
                    <Chip tone={comparison.inconclusive ? "neutral" : flip.direction === "improved" ? "pass" : "fail-agent"}>
                      {flip.direction}
                    </Chip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </>
  );
}

function ComparisonMetrics({ label, metrics }: { label: string; metrics: Comparison["baseline_metrics"] }) {
  const bands = [[`pass@${metrics.k}`, metrics.pass_at_k], [`pass^${metrics.k}`, metrics.pass_caret_k]] as const;
  return <Panel title={`${label} · capability and reliability`} bodyClassName="p-4">
    <dl className="grid grid-cols-2 gap-4">{bands.map(([name, band]) => <div key={name}>
      <dt className="label">{name}</dt><dd className="mono mt-2 text-xl font-semibold">{pct(band.point)}</dd>
      <dd className="mt-1 text-[11px] text-ink-muted">95% CI {pct(band.low)}–{pct(band.high)}</dd>
    </div>)}</dl>
    <p className="mt-3 text-xs text-ink-muted">Reliability gap {points(metrics.reliability_gap)}{!metrics.reportable && " · Indicative, not reportable"}</p>
  </Panel>;
}
