"use client";

/**
 * The run header and metric cards.
 *
 * pass@k and pass^k are rendered adjacent, always, with the difference labelled
 * "reliability gap". A UI that shows only pass@k tells you the model *can*;
 * this one also tells you whether it *will*. That is the product opinion, and
 * it lives here because this is the first thing anyone reads.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import clsx from "clsx";

import { api } from "@/lib/api";
import { useAccess } from "@/lib/access";
import { count, duration, pct, points, runTone, seconds, usd } from "@/lib/format";
import { useRunEvents, CONNECTION_LABEL } from "@/lib/sse";
import { Chip, Meter, Mono, Panel, StatTile } from "@/components/ui/primitives";
import type { RunDetail } from "@/lib/types";

export function RunHeader({ initial }: { initial: RunDetail }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const access = useAccess("run");

  const { data: run = initial } = useQuery({
    queryKey: ["run", initial.id],
    queryFn: () => api.run(initial.id),
    initialData: initial,
    refetchInterval: (query) => ["running", "queued"].includes(query.state.data?.status ?? initial.status) ? 5_000 : false,
  });

  const live = run.status === "running" || run.status === "queued";

  const { state } = useRunEvents(run.id, {
    enabled: live,
    onEvent: () => {
      // Recompute the aggregates server-side rather than in the browser: the
      // bootstrap that produces these intervals is not something to reimplement
      // in TypeScript and hope stays in sync.
      void queryClient.invalidateQueries({ queryKey: ["run", run.id] });
    },
  });

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(run.id),
    // Optimistic: flip immediately, reconcile on the next SSE frame.
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: ["run", run.id] });
      const previous = queryClient.getQueryData<RunDetail>(["run", run.id]);
      queryClient.setQueryData<RunDetail>(["run", run.id], (current) =>
        current ? { ...current, status: "cancelled" } : current,
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(["run", run.id], context.previous);
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: ["run", run.id] }),
  });

  const retry = useMutation({
    mutationFn: () => api.retryFailed(run.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", run.id] });
      void queryClient.invalidateQueries({ queryKey: ["matrix", run.id] });
      router.refresh();
    },
  });

  const metrics = run.metrics;
  const progress = run.trials_total ? run.trials_done / run.trials_total : 0;
  const infraErrors = Object.entries(run.error_breakdown)
    .filter(([key]) => key.startsWith("infra_") || key === "verifier_crash")
    .reduce((total, [, value]) => total + value, 0);

  return (
    <div className="space-y-3">
      {infraErrors / Math.max(run.trials_total, 1) > 0.02 && <div role="alert" className="rounded-xl border border-fail-infra/30 bg-fail-infra/10 p-4 text-sm text-fail-infra"><strong>Invalid — harness errors</strong><p className="mt-1">Infrastructure errors exceeded 2% of trials. This run is excluded from the leaderboard and benchmark trend.</p></div>}
      {(cancel.isError || retry.isError) && <p role="alert" className="info-banner warning">{(cancel.error ?? retry.error)?.message ?? "The action failed. Try again."}</p>}
      <Panel bodyClassName="p-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border-subtle px-3.5 py-2.5">
          <Mono className="text-[14px] font-semibold text-ink">{run.suite_slug}</Mono>
          <span className="text-ink-inert">·</span>
          <span className="text-[13px] text-ink-muted">{run.agent_slug}</span>
          <span className="text-ink-inert">·</span>
          <Mono className="text-[13px] text-accent">{run.model_slug}</Mono>
          <span className="text-ink-inert">·</span>
          <Mono className="text-[13px] text-ink-muted">k={run.k}</Mono>

          <Chip tone={runTone(run.status)} pulse={run.status === "running"}>
            {run.status}
          </Chip>

          {live && (
            <span
              className={clsx(
                "mono text-[11px]",
                state === "open"
                  ? "text-pass-text"
                  : state === "reconnecting"
                    ? "text-warn"
                    : "text-ink-inert",
              )}
              title="Server-sent events connection"
            >
              SSE {CONNECTION_LABEL[state]}
            </span>
          )}

          <div className="ml-auto flex items-center gap-2">
            {infraErrors > 0 && !live && (
              <button
                type="button"
                onClick={() => retry.mutate()}
                className="btn btn-ghost"
                disabled={!access.allowed || retry.isPending}
                title="Re-queues infra_* failures only. Agent failures are the agent's result and are never re-rolled."
              >
                Retry {infraErrors} infra
              </button>
            )}
            {live && (
              <button
                type="button"
                onClick={() => cancel.mutate()}
                className="btn btn-danger"
                disabled={!access.allowed || cancel.isPending}
                title={access.reason}
              >
                Cancel
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3.5 py-2">
          <Mono className="text-[12px] text-ink">
            {count(run.trials_done)}/{count(run.trials_total)} trials
          </Mono>
          <span className="text-ink-inert">·</span>
          <Mono className="text-[12px] text-ink-muted">
            {seconds(run.elapsed_sec)} elapsed
          </Mono>
          {run.eta_sec !== null && run.eta_sec > 0 && (
            <>
              <span className="text-ink-inert">·</span>
              <Mono className="text-[12px] text-ink-muted">~{seconds(run.eta_sec)} remaining</Mono>
            </>
          )}
          <span className="text-ink-inert">·</span>
          <Mono className="text-[12px] text-ink">{usd(run.cost_usd)}</Mono>
          {run.budget_usd && (
            <Mono className="text-[11px] text-ink-inert">/ {usd(run.budget_usd)} cap</Mono>
          )}
          {run.label && (
            <span className="mono ml-auto rounded-xs border border-border-muted px-1.5 py-0.5 text-[11px] text-ink-muted">
              {run.label}
            </span>
          )}
        </div>

        <div className="px-3.5 pb-3">
          <Meter value={progress} tone={run.status === "cancelled" ? "warn" : "accent"} />
        </div>
      </Panel>

      {metrics && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="pass@1"
              value={pct(metrics.pass_at_1.point)}
              interval={metrics.pass_at_1}
              hint="Task-clustered 95% interval for repeated trials; Wilson interval when k=1."
            />
            <StatTile
              label={`pass@${metrics.k}`}
              value={pct(metrics.pass_at_k.point)}
              interval={metrics.pass_at_k}
              hint="Capability ceiling: can it, ever? BCa bootstrap over tasks."
            />
            <StatTile
              label={`pass^${metrics.k}`}
              value={pct(metrics.pass_caret_k.point)}
              interval={metrics.pass_caret_k}
              accent="warn"
              hint="Reliability: will it, every time? This is the number to ship against."
            />
            <StatTile
              label="Reliability gap"
              value={points(metrics.reliability_gap)}
              sub="← the number that matters"
              accent="warn"
              hint="pass@k minus pass^k. A large gap means the model can solve the task but cannot be relied on to."
            />
          </div>

          {!metrics.reportable && (
            <div className="flex items-start gap-2 rounded-sm border border-warn/30 bg-warn/10 px-3 py-2 text-[12px] leading-relaxed text-warn">
              <span className="mt-px shrink-0">⚠</span>
              <span>
                <strong className="font-semibold">Indicative, not reportable.</strong>{" "}
                {metrics.k < 3 ? "Use at least 3 attempts per task before quoting these numbers." : "Some tasks do not yet have enough scored attempts. Wait for completion or resolve infrastructure errors."}
              </span>
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Mini label="Mean reward" value={metrics.mean_reward.toFixed(3)} note="partial credit" />
            <Mini
              label="Trials scored"
              value={`${count(metrics.total_passes)}/${count(metrics.total_trials)}`}
              note={`${metrics.tasks_evaluated} tasks`}
            />
            <Mini
              label="Infra errors"
              value={count(metrics.infra_errors)}
              note="excluded from the score"
              tone={metrics.infra_errors > 0 ? "warn" : undefined}
            />
            <Mini
              label="Cost / solve"
              value={metrics.total_passes ? usd(run.cost_usd / metrics.total_passes, 4) : "—"}
              note="efficiency"
            />
          </div>
        </>
      )}
    </div>
  );
}

function Mini({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: "warn";
}) {
  return (
    <div className="panel flex items-baseline justify-between px-3 py-2">
      <div>
        <div className="text-[10px] uppercase tracking-[0.04em] text-ink-inert">{label}</div>
        <Mono className={clsx("text-[14px]", tone === "warn" ? "text-warn" : "text-ink")}>
          {value}
        </Mono>
      </div>
      <span className="text-[10px] text-ink-inert">{note}</span>
    </div>
  );
}
