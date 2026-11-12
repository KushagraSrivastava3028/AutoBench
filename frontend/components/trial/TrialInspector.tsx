"use client";

/**
 * The trial inspector: three resizable panes plus a replay bar.
 *
 *   left        trajectory timeline — every event, collapsible
 *   right top   filesystem diff     — usually where the answer is
 *   right btm   verdict             — the score report, item by item
 *   footer      replay              — copy-paste commands to reproduce locally
 *
 * Replayability is what turns a red cell into a fixed bug. It is a v1
 * requirement rather than a nice-to-have, which is why the commands are on
 * screen rather than behind a menu.
 */

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useRef, useState, type CSSProperties } from "react";
import clsx from "clsx";

import { api } from "@/lib/api";
import {
  describeError,
  duration,
  isInfra,
  pct,
  timestamp,
  tokens,
  trialTone,
  usd,
} from "@/lib/format";
import { Chip, EmptyState, KeyValue, Mono, Panel } from "@/components/ui/primitives";
import type { Diff, Trajectory, Trial, TrialEvent } from "@/lib/types";

export function TrialInspector({
  trial,
  trajectory,
  diff,
}: {
  trial: Trial;
  trajectory: Trajectory;
  diff: Diff;
}) {
  return (
    <div className="space-y-3">
      <Summary trial={trial} />
      <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Timeline trial={trial} initial={trajectory} />
        <div className="flex min-w-0 flex-col gap-3">
          <DiffViewer diff={diff} />
          <Verdict trial={trial} />
        </div>
      </div>
      <ReplayBar trialId={trial.id} />
    </div>
  );
}

// ---------------------------------------------------------------------------

function Summary({ trial }: { trial: Trial }) {
  const infra = isInfra(trial.error_class);
  return (
    <Panel bodyClassName="grid gap-x-6 gap-y-1 p-3 sm:grid-cols-2 lg:grid-cols-4">
      <div className="space-y-0.5">
        <KeyValue label="Task">{trial.task_id}</KeyValue>
        <KeyValue label="Attempt">k{trial.attempt_index}</KeyValue>
        <KeyValue label="Seed">{trial.seed_exact}</KeyValue>
      </div>
      <div className="space-y-0.5">
        <KeyValue label="Status">
          <Chip tone={trialTone(trial.status, trial.error_class)}>{trial.status}</Chip>
        </KeyValue>
        <KeyValue label="Reward">
          {trial.score ? trial.score.reward.toFixed(3) : "—"}
        </KeyValue>
        <KeyValue label="Duration">{duration(trial.duration_ms)}</KeyValue>
      </div>
      <div className="space-y-0.5">
        <KeyValue label="Setup">{duration(trial.score?.setup_ms)}</KeyValue>
        <KeyValue label="Agent">{duration(trial.score?.agent_ms)}</KeyValue>
        <KeyValue label="Verify">{duration(trial.score?.verify_ms)}</KeyValue>
      </div>
      <div className="space-y-0.5">
        <KeyValue label="Tool calls">{trial.score?.tool_calls ?? "—"}</KeyValue>
        <KeyValue label="Tokens">
          {tokens((trial.score?.input_tokens ?? 0) + (trial.score?.output_tokens ?? 0))}
        </KeyValue>
        <KeyValue label="Cost">{usd(trial.score?.cost_usd, 4)}</KeyValue>
      </div>

      {trial.error_class && (
        <div
          className={clsx(
            "col-span-full mt-1 rounded-sm border px-2.5 py-2 text-[11px] leading-relaxed",
            infra
              ? "border-fail-infra/30 bg-fail-infra/10 text-fail-infra-text"
              : "border-fail-agent/30 bg-fail-agent/10 text-fail-agent",
          )}
        >
          <strong className="font-semibold">{describeError(trial.error_class)}</strong>
          {infra ? (
            <span className="ml-1.5 opacity-80">
              — an infrastructure failure. This trial was retried and contributes nothing to the
              score.
            </span>
          ) : (
            <span className="ml-1.5 opacity-80">
              — the agent's result. Not retried, scored 0.0.
            </span>
          )}
          {trial.error_detail && (
            <pre className="mono mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] opacity-90">
              {trial.error_detail}
            </pre>
          )}
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------

const KIND_TONE: Record<string, string> = {
  prompt: "text-accent",
  thinking: "text-ink-inert",
  tool_call: "text-running",
  tool_result: "text-ink-muted",
  model_usage: "text-ink-inert",
  status: "text-ink-muted",
  error: "text-fail-agent",
  verdict: "text-pass-text",
};

function Timeline({ trial, initial }: { trial: Trial; initial: Trajectory }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set(initial.events.filter(e => e.kind === "tool_call").map(e => e.seq)));
  const scrollRef = useRef<HTMLDivElement>(null);
  const query = useInfiniteQuery({
    queryKey: ["trajectory-pages", trial.id],
    queryFn: ({ pageParam }) => api.trajectory(trial.id, pageParam),
    initialPageParam: 0,
    initialData: { pages: [initial], pageParams: [0] },
    staleTime: 0,
    getNextPageParam: (last, pages) => { const loaded = pages.reduce((sum, p) => sum + p.events.length, 0); return loaded < last.total ? loaded : undefined; },
  });
  const data = { total: query.data?.pages[0]?.total ?? 0, events: query.data?.pages.flatMap(p => p.events) ?? [] };
  const virtualizer = useVirtualizer({ count: data.events.length, getScrollElement: () => scrollRef.current, estimateSize: () => 42, overscan: 8 });

  const toggle = (seq: number) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(seq)) next.delete(seq);
      else next.add(seq);
      return next;
    });

  return (
    <Panel
      title="Trajectory"
      subtitle={`${data.total} events`}
      actions={
        <button
          type="button"
          onClick={() =>
            setExpanded((current) =>
              current.size > 0 ? new Set() : new Set(data.events.map((e) => e.seq)),
            )
          }
          className="text-[11px] text-accent hover:text-accent-hover"
        >
          {expanded.size > 0 ? "Collapse all" : "Expand all"}
        </button>
      }
      bodyClassName="min-h-0 overflow-y-auto"
    >
      {data.events.length === 0 ? (
        <EmptyState
          title="No trajectory recorded"
          body="There are no retained events for this trial. It may not have started, or its artifacts may no longer be available."
        />
      ) : (
        <div ref={scrollRef} style={{ height: Math.min(520, Math.max(100, data.events.length * 42)), overflow: "auto" }}>
        <ol style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
          {virtualizer.getVirtualItems().map(item => { const event = data.events[item.index]!; return (
            <TimelineRow
              key={event.seq}
              event={event}
              open={expanded.has(event.seq)}
              onToggle={() => toggle(event.seq)}
              rowRef={virtualizer.measureElement}
              index={item.index}
              style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${item.start}px)` }}
            />
          ); })}
        </ol>
        </div>
      )}
      {query.hasNextPage && <button className="btn btn-ghost m-3" disabled={query.isFetchingNextPage} onClick={() => void query.fetchNextPage()}>{query.isFetchingNextPage ? "Loading…" : `Load more · ${data.events.length} of ${data.total} events`}</button>}
      {query.isError && <p role="alert" className="p-3 text-xs text-fail-agent">More trajectory events could not be loaded.</p>}
    </Panel>
  );
}

function TimelineRow({
  event,
  open,
  onToggle,
  style,
  rowRef,
  index,
}: {
  event: TrialEvent;
  open: boolean;
  onToggle: () => void;
  style?: CSSProperties;
  rowRef?: (element: HTMLLIElement | null) => void;
  index?: number;
}) {
  const payload = event.payload as Record<string, unknown>;
  const headline = summarise(event);
  const body = detail(event);

  return (
    <li ref={rowRef} data-index={index} style={style} className="px-3 py-1.5 hover:bg-raised">
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full items-baseline gap-2 text-left">
        <Mono className="w-9 shrink-0 text-[10px] text-ink-inert">#{event.seq}</Mono>
        <span
          className={clsx(
            "mono w-20 shrink-0 text-[10px] uppercase tracking-[0.04em]",
            KIND_TONE[event.kind] ?? "text-ink-muted",
          )}
        >
          {event.kind.replace("_", " ")}
        </span>
        <span className="mono min-w-0 flex-1 truncate text-[12px] text-ink">{headline}</span>
        <Mono className="w-16 shrink-0 text-right text-[10px] text-ink-inert">
          {event.elapsed_ms !== null ? duration(event.elapsed_ms) : timestamp(event.ts)}
        </Mono>
        {event.cumulative_tokens !== null && (
          <Mono className="w-14 shrink-0 text-right text-[10px] text-ink-inert">
            {tokens(event.cumulative_tokens)}
          </Mono>
        )}
      </button>

      {open && body && (
        <pre className="mono mt-1.5 max-h-72 overflow-auto rounded-sm border border-border-subtle bg-canvas p-2.5 text-[11px] leading-relaxed whitespace-pre-wrap text-ink-muted">
          {body}
        </pre>
      )}
      {open && event.kind === "model_usage" && (
        <div className="mt-1.5 flex flex-wrap gap-3 text-[11px] text-ink-inert">
          <span>in {tokens(Number(payload.input_tokens ?? 0))}</span>
          <span>out {tokens(Number(payload.output_tokens ?? 0))}</span>
          <span>cached {tokens(Number(payload.cached_tokens ?? 0))}</span>
          <span>{usd(Number(payload.cost_usd ?? 0), 4)}</span>
          <span>{duration(Number(payload.latency_ms ?? 0))}</span>
        </div>
      )}
    </li>
  );
}

function summarise(event: TrialEvent): string {
  const payload = event.payload as Record<string, unknown>;
  switch (event.kind) {
    case "tool_call":
      return String(payload.rendered ?? payload.name ?? "tool call");
    case "tool_result": {
      const output = String(payload.output ?? "");
      return output.split("\n")[0]?.slice(0, 160) ?? "(no output)";
    }
    case "prompt":
      return String(payload.instruction ?? "").slice(0, 160);
    case "thinking":
      return String(payload.text ?? "").slice(0, 160);
    case "model_usage":
      return `${tokens(Number(payload.input_tokens ?? 0))} in / ${tokens(
        Number(payload.output_tokens ?? 0),
      )} out`;
    case "verdict":
      return `reward ${Number(payload.reward ?? 0).toFixed(3)} · ${
        payload.binary_pass ? "PASS" : "FAIL"
      }`;
    case "error":
      return String(payload.detail ?? payload.error_class ?? "error");
    default:
      return String(payload.message ?? JSON.stringify(payload).slice(0, 160));
  }
}

function detail(event: TrialEvent): string | null {
  const payload = event.payload as Record<string, unknown>;
  if (event.kind === "tool_result") return String(payload.output ?? "");
  if (event.kind === "tool_call") return JSON.stringify(payload.arguments ?? {}, null, 2);
  if (event.kind === "prompt")
    return `${String(payload.system ?? "")}\n\n---\n\n${String(payload.instruction ?? "")}`;
  if (event.kind === "thinking") return String(payload.text ?? "");
  if (event.kind === "error") return String(payload.detail ?? "");
  if (event.kind === "verdict") return JSON.stringify(payload.items ?? [], null, 2);
  return JSON.stringify(payload, null, 2);
}

// ---------------------------------------------------------------------------

function DiffViewer({ diff }: { diff: Diff }) {
  const [selected, setSelected] = useState(0);
  const file = diff.files[selected];

  if (diff.files.length === 0) {
    return (
      <Panel title="Filesystem diff">
        <EmptyState
          title="The agent changed nothing"
          body="Setup and freeze snapshots are identical. For a failing trial this is the answer: the agent never modified the world it was asked to modify."
        />
      </Panel>
    );
  }

  return (
    <Panel
      title="Filesystem diff"
      subtitle={`+${diff.counts.added ?? 0} ~${diff.counts.modified ?? 0} -${diff.counts.deleted ?? 0}`}
      actions={
        diff.truncated ? <Chip tone="warn">truncated</Chip> : null
      }
      bodyClassName="flex min-h-0 flex-col"
    >
      <div className="flex gap-1 overflow-x-auto border-b border-border-subtle px-2 py-1.5 scrollbar-none">
        {diff.files.map((entry, index) => (
          <button
            key={entry.path}
            type="button"
            onClick={() => setSelected(index)}
            className={clsx(
              "mono shrink-0 rounded-xs px-1.5 py-0.5 text-[11px]",
              index === selected
                ? "bg-accent text-canvas"
                : "text-ink-muted hover:bg-raised hover:text-ink",
            )}
            title={entry.path}
          >
            <span
              className={clsx(
                "mr-1",
                entry.change === "added"
                  ? "text-pass-text"
                  : entry.change === "deleted"
                    ? "text-fail-agent"
                    : "text-warn",
                index === selected && "text-canvas",
              )}
            >
              {entry.change === "added" ? "+" : entry.change === "deleted" ? "−" : "~"}
            </span>
            {entry.path.split("/").pop()}
          </button>
        ))}
      </div>

      {file && (
        <div className="min-h-0 overflow-auto" style={{ maxHeight: 280 }}>
          {file.binary ? (
            <div className="px-3 py-6 text-center text-[12px] text-ink-muted">
              Binary or over the capture cap ({file.size_after} bytes). Fetch the full artifact to
              inspect it.
            </div>
          ) : (
            <UnifiedDiff before={file.before ?? ""} after={file.after ?? ""} />
          )}
        </div>
      )}
    </Panel>
  );
}

/**
 * A minimal line-level diff.
 *
 * Deliberately not a real LCS: this renders the common prefix and suffix and
 * marks the middle as changed, which is accurate for the edits agents actually
 * make (rewrite a function, add a file) and costs nothing to compute in render.
 */
function UnifiedDiff({ before, after }: { before: string; after: string }) {
  const beforeLines = before.split("\n");
  const afterLines = after.split("\n");

  let head = 0;
  while (head < beforeLines.length && head < afterLines.length && beforeLines[head] === afterLines[head]) {
    head += 1;
  }
  let tail = 0;
  while (
    tail < beforeLines.length - head &&
    tail < afterLines.length - head &&
    beforeLines[beforeLines.length - 1 - tail] === afterLines[afterLines.length - 1 - tail]
  ) {
    tail += 1;
  }

  const context = 3;
  const rows: Array<{ kind: "ctx" | "del" | "add"; text: string; n: number }> = [];

  beforeLines.slice(Math.max(0, head - context), head).forEach((text, i) =>
    rows.push({ kind: "ctx", text, n: Math.max(0, head - context) + i + 1 }),
  );
  beforeLines.slice(head, beforeLines.length - tail).forEach((text, i) =>
    rows.push({ kind: "del", text, n: head + i + 1 }),
  );
  afterLines.slice(head, afterLines.length - tail).forEach((text, i) =>
    rows.push({ kind: "add", text, n: head + i + 1 }),
  );
  beforeLines
    .slice(beforeLines.length - tail, beforeLines.length - tail + context)
    .forEach((text, i) => rows.push({ kind: "ctx", text, n: beforeLines.length - tail + i + 1 }));

  return (
    <table className="mono w-full text-[11px] leading-relaxed">
      <tbody>
        {rows.map((row, index) => (
          <tr
            key={`${row.kind}-${row.n}-${index}`}
            className={
              row.kind === "add"
                ? "bg-pass/10"
                : row.kind === "del"
                  ? "bg-fail-agent/10"
                  : undefined
            }
          >
            <td className="w-10 select-none border-r border-border-subtle px-2 text-right text-ink-inert">
              {row.n}
            </td>
            <td
              className={clsx(
                "w-4 select-none px-1 text-center",
                row.kind === "add"
                  ? "text-pass-text"
                  : row.kind === "del"
                    ? "text-fail-agent"
                    : "text-ink-inert",
              )}
            >
              {row.kind === "add" ? "+" : row.kind === "del" ? "−" : " "}
            </td>
            <td className="whitespace-pre-wrap px-2 text-ink-muted">{row.text || " "}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------

function Verdict({ trial }: { trial: Trial }) {
  const score = trial.score;
  if (!score) {
    return (
      <Panel title="Verdict">
        <EmptyState
          title="Not scored"
          body="This trial produced no measurement. Infrastructure failures never write a score row — that is the retry rule expressed as a database constraint rather than a convention."
        />
      </Panel>
    );
  }

  return (
    <Panel
      title="Verdict"
      actions={
        <Chip tone={score.binary_pass ? "pass" : "fail-agent"}>
          {score.binary_pass ? "pass" : "fail"} · {score.reward.toFixed(3)}
        </Chip>
      }
      bodyClassName="p-3 space-y-2"
    >
      {score.error && (
        <p className="rounded-sm border border-warn/30 bg-warn/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-warn">
          {score.error}
        </p>
      )}

      {score.items.length === 0 ? (
        <p className="text-[12px] text-ink-muted">
          Scored on the verifier's exit code alone — no item breakdown was reported.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {score.items.map((item) => (
            <li
              key={item.id}
              className={clsx(
                "rounded-sm border px-2.5 py-1.5",
                item.passed
                  ? "border-border-subtle bg-canvas"
                  : "border-fail-agent/30 bg-fail-agent/5",
              )}
            >
              <div className="flex items-baseline justify-between gap-2">
                <Mono
                  className={clsx("text-[12px]", item.passed ? "text-ink" : "text-fail-agent")}
                >
                  {item.passed ? "✓" : "✗"} {item.id}
                </Mono>
                <Mono className="text-[11px] text-ink-inert">w={item.weight.toFixed(2)}</Mono>
              </div>
              {/* Failed items expanded by default: that is what you came for. */}
              {item.detail && (!item.passed || item.detail.length < 80) && (
                <p className="mono mt-1 text-[11px] leading-relaxed text-ink-muted">
                  {item.detail}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------------------

function ReplayBar({ trialId }: { trialId: string }) {
  const [copied, setCopied] = useState(false);
  const { data, isError } = useQuery({
    queryKey: ["replay", trialId],
    queryFn: () => api.replay(trialId),
  });

  if (isError) {
    return (
      <Panel title="Replay">
        <div className="px-3 py-4 text-[12px] text-ink-muted">
          Replay needs the task directory checked out on the machine serving the API.
        </div>
      </Panel>
    );
  }

  const commands = data?.commands ?? [];

  return (
    <Panel
      title="Replay locally"
      subtitle={data?.content_hash.slice(0, 23)}
      actions={
        <button
          type="button"
          className="btn btn-ghost"
          disabled={!commands.length}
          onClick={async () => {
            try { await navigator.clipboard.writeText(commands.join("\n")); setCopied(true); }
            catch { setCopied(false); }
            setTimeout(() => setCopied(false), 1500);
          }}
        >
          {copied ? "Copied" : "Copy all"}
        </button>
      }
      bodyClassName="p-3"
    >
      <pre className="mono overflow-x-auto rounded-sm border border-border-subtle bg-canvas p-2.5 text-[11px] leading-relaxed text-ink-muted">
        {commands.map((command) => (
          <div key={command} className={command.startsWith("#") ? "text-ink-inert" : "text-ink"}>
            {!command.startsWith("#") && <span className="mr-1.5 text-ink-inert">$</span>}
            {command}
          </div>
        ))}
      </pre>
    </Panel>
  );
}
