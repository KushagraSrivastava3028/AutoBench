"use client";

/**
 * The trial matrix: tasks (rows) × attempts (columns).
 *
 * Virtualised from day one. A 500-task run at k=10 is 5,000 cells and has to
 * stay at 60fps while SSE patches individual cells underneath — so rows render
 * through `@tanstack/react-virtual` and a cell update writes straight into the
 * TanStack Query cache rather than refetching the grid.
 *
 * Rows are ordered worst-first. The rows a human opens this screen to look at
 * are the failing ones.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import clsx from "clsx";

import { api } from "@/lib/api";
import {
  CELL_CLASS,
  CELL_LABEL,
  cellState,
  describeError,
  duration,
  isInfra,
  pct,
} from "@/lib/format";
import { useRunEvents } from "@/lib/sse";
import { EmptyState, MatrixLegend, Mono, Panel } from "@/components/ui/primitives";
import type { Matrix, MatrixCell, MatrixRow, RunEvent } from "@/lib/types";
import { TrialSheet } from "@/components/trial/TrialSheet";

const ROW_HEIGHT = 44;

export function TrialMatrix({
  runId,
  initial,
  live,
}: {
  runId: string;
  initial: Matrix;
  live: boolean;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"all" | "failing" | "infra">("all");
  const [selectedTrial, setSelectedTrial] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const { data } = useQuery({
    queryKey: ["matrix", runId],
    queryFn: () => api.matrix(runId),
    initialData: initial,
    // SSE drives updates; the interval is a slow safety net for a dropped
    // stream that has not yet reconnected.
    refetchInterval: live ? 5_000 : false,
  });

  useRunEvents(runId, {
    enabled: live,
    onEvent: (event: RunEvent) => {
      if (event.event !== "trial_update" || !event.trial_id) return;
      // Patch the one cell. Refetching 5,000 cells because one changed is how
      // a live view becomes a stuttering one.
      queryClient.setQueryData<Matrix>(["matrix", runId], (current) => {
        if (!current) return current;
        return {
          ...current,
          rows: current.rows.map((row) => ({
            ...row,
            cells: row.cells.map((cell) =>
              cell.trial_id === event.trial_id
                ? {
                    ...cell,
                    status: event.status ?? cell.status,
                    reward: event.reward ?? cell.reward,
                    binary_pass: event.binary_pass ?? cell.binary_pass,
                    error_class: event.error_class ?? cell.error_class,
                    duration_ms: event.duration_ms ?? cell.duration_ms,
                  }
                : cell,
            ),
          })),
        };
      });
    },
  });

  const rows = useMemo(() => {
    const all = (data?.rows ?? []).filter(row => `${row.task_id} ${row.title}`.toLowerCase().includes(search.toLowerCase()));
    if (filter === "failing") return all.filter((row) => row.passes < row.attempts || row.attempts === 0);
    if (filter === "infra") {
      return all.filter((row) => row.cells.some((cell) => isInfra(cell.error_class)));
    }
    return all;
  }, [data?.rows, filter, search]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });

  if (!data || data.rows.length === 0) {
    return (
      <Panel title="Trial matrix">
        <EmptyState
          title="No trials planned"
          body="This run expanded to zero trials, which means its suite has no published members. Publish the tasks, then relaunch."
          command="autobench validate tasks/ --deep"
        />
      </Panel>
    );
  }

  return (
    <><Panel
      title="Trial matrix"
      subtitle={`${rows.length} tasks × ${data.k} attempts`}
      actions={
        <div className="flex items-center gap-2">
          <MatrixLegend />
          <div className="ml-2 flex rounded-sm border border-border-muted">
            {(["all", "failing", "infra"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setFilter(value)}
                className={clsx(
                  "px-2 py-1 text-[11px] first:rounded-l-sm last:rounded-r-sm",
                  filter === value
                    ? "bg-accent text-canvas"
                    : "text-ink-muted hover:bg-raised hover:text-ink",
                )}
              >
                {value}
              </button>
            ))}
          </div>
        </div>
      }
      bodyClassName="flex min-h-0 flex-col"
    >
      <div className="px-4 pb-3"><input className="input w-full max-w-sm" aria-label="Search matrix tasks" placeholder="Search tasks…" value={search} onChange={e => setSearch(e.target.value)} /></div>
      <div className="matrix-scroll"><div style={{ minWidth: Math.max(580, 360 + data.k * 38) }}>
      <div className="flex items-center gap-2 border-b border-border-subtle px-3 py-1.5 text-[10px] uppercase tracking-[0.04em] text-ink-inert">
        <span className="w-56 shrink-0 xl:w-72">task</span>
        <span className="flex-1">attempts k0 … k{data.k - 1}</span>
        <span className="w-14 shrink-0 text-right">pass</span>
        <span className="w-16 shrink-0 text-right">rate</span>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto" style={{ maxHeight: 460 }}>
        {rows.length === 0 ? (
          <div className="px-3 py-10 text-center text-[12px] text-ink-muted">
            No rows match this filter.
          </div>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const row = rows[item.index];
              if (!row) return null;
              return (
                <div
                  key={row.task_id}
                  className="absolute inset-x-0 flex items-center gap-2 border-b border-border-subtle px-3 hover:bg-raised"
                  style={{ height: item.size, transform: `translateY(${item.start}px)` }}
                >
                  <button
                    type="button"
                    onClick={() => router.push(`/tasks/${row.task_id}`)}
                    className="w-56 shrink-0 truncate text-left text-[12px] text-ink hover:text-accent xl:w-72"
                    title={row.title}
                  >
                    {row.task_id}
                  </button>

                  <div className="flex flex-1 flex-wrap items-center gap-1">
                    {row.cells.map((cell) => (
                      <Cell key={cell.trial_id} runId={runId} row={row} cell={cell} onPreview={() => setSelectedTrial(cell.trial_id)} />
                    ))}
                  </div>

                  <Mono className="w-14 shrink-0 text-right text-[12px] text-ink-muted">
                    {row.passes}/{row.attempts || data.k}
                  </Mono>
                  <Mono
                    className={clsx(
                      "w-16 shrink-0 text-right text-[12px]",
                      row.attempts === 0
                        ? "text-ink-inert"
                        : row.passes === row.attempts
                          ? "text-pass-text"
                          : row.passes === 0
                            ? "text-fail-agent"
                            : "text-warn",
                    )}
                  >
                    {row.attempts ? pct(row.passes / row.attempts, 0) : "—"}
                  </Mono>
                </div>
              );
            })}
          </div>
        )}
      </div>
      </div></div>
    </Panel>{selectedTrial && <TrialSheet trialId={selectedTrial} runId={runId} onClose={() => setSelectedTrial(null)} />}</>
  );
}

function Cell({ runId, row, cell, onPreview }: { runId: string; row: MatrixRow; cell: MatrixCell; onPreview: () => void }) {
  const state = cellState(cell);
  const router = useRouter();

  const title = [
    `${row.task_id} · attempt ${cell.attempt}`,
    CELL_LABEL[state],
    cell.reward !== null ? `reward ${cell.reward.toFixed(3)}` : null,
    cell.error_class ? describeError(cell.error_class) : null,
    cell.duration_ms ? duration(cell.duration_ms) : null,
    cell.attempt_count > 0 ? `${cell.attempt_count} infra retries` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onClick={onPreview}
      onKeyDown={event => {
        if (event.key === "Enter") { event.preventDefault(); router.push(`/runs/${runId}/trials/${cell.trial_id}`); }
        if (["j", "k", "ArrowRight", "ArrowLeft"].includes(event.key)) {
          event.preventDefault();
          const next = ["j", "ArrowRight"].includes(event.key) ? event.currentTarget.nextElementSibling : event.currentTarget.previousElementSibling;
          (next as HTMLElement | null)?.focus();
        }
      }}
      className={clsx(
        "matrix-cell inline-flex h-8 w-8 items-center justify-center rounded-md text-sm font-semibold hover:ring-1 hover:ring-accent",
        CELL_CLASS[state],
      )}
    >{{ pass: "✓", fail: "✗", partial: "◐", retry: "⟳", error: "⊘", running: "◌", queued: "·" }[state]}</button>
  );
}
