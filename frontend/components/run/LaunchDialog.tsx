"use client";

/**
 * The launch dialog.
 *
 * Two opinions are baked in rather than left to the user to remember:
 *
 * - k defaults to 3, and k<3 is labelled "indicative, not reportable" inline.
 *   A single attempt per task is one sample of a stochastic process, and the
 *   place to say so is before the run, not in the write-up afterwards.
 * - A budget cap is offered up front, because a runaway loop discovered on the
 *   invoice is discovered too late.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { cloneElement, useEffect, useId, useState, type ReactElement } from "react";
import clsx from "clsx";

import { api, ApiError } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";
import { useAccess } from "@/lib/access";

export function LaunchDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const access = useAccess("run");

  const [suite, setSuite] = useState("");
  const [agent, setAgent] = useState("react");
  const [model, setModel] = useState("mock/mock-1");
  const [k, setK] = useState(3);
  const [concurrency, setConcurrency] = useState(8);
  const [label, setLabel] = useState("");
  const [budget, setBudget] = useState("");

  const suites = useQuery({ queryKey: ["suites"], queryFn: () => api.suites() });
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api.agents() });
  const models = useQuery({ queryKey: ["models"], queryFn: () => api.models() });

  useEffect(() => {
    if (!suite && suites.data?.length) setSuite(suites.data[0]!.slug);
  }, [suite, suites.data]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const launch = useMutation({
    mutationFn: () =>
      api.launchRun({
        suite,
        agent,
        model,
        k,
        concurrency,
        label: label || undefined,
        budget_usd: budget ? Number(budget) : undefined,
      }),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      onClose();
      router.push(`/runs/${run.id}`);
    },
  });

  const selectedSuite = suites.data?.find((entry) => entry.slug === suite);
  const trials = (selectedSuite?.member_count ?? 0) * k;

  return (
    <Modal label="Launch a new run" onClose={onClose}>
      <div>
        <header className="panel-header">
          <h2 className="text-[14px] font-semibold text-ink">Launch new run</h2>
          <button type="button" onClick={onClose} className="text-ink-muted hover:text-ink" aria-label="Close">
            ✕
          </button>
        </header>

        <form
          className="space-y-3 p-4"
          onSubmit={(event) => {
            event.preventDefault();
            launch.mutate();
          }}
        >
          <Field label="Suite">
            <select className="input w-full" value={suite} onChange={(e) => setSuite(e.target.value)}>
              {suites.data?.length ? (
                suites.data.map((entry) => (
                  <option key={entry.id} value={entry.slug}>
                    {entry.slug} — {entry.member_count} tasks
                  </option>
                ))
              ) : (
                <option value="">no suites — run `autobench seed`</option>
              )}
            </select>
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Agent">
              <select className="input w-full" value={agent} onChange={(e) => setAgent(e.target.value)}>
                {agents.data?.map((entry) => (
                  <option key={entry.id} value={entry.slug}>
                    {entry.slug} ({entry.mode})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Model">
              <select className="input w-full" value={model} onChange={(e) => setModel(e.target.value)}>
                {models.data?.map((entry) => (
                  <option key={entry.id} value={entry.slug}>
                    {entry.slug}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="k — attempts per task"
              hint={
                k < 3
                  ? "indicative, not reportable"
                  : "repetition is the only way to measure model variance"
              }
              hintTone={k < 3 ? "warn" : "muted"}
            >
              <input
                type="number"
                min={1}
                max={20}
                className="input w-full"
                value={k}
                onChange={(e) => setK(Math.max(1, Number(e.target.value)))}
              />
            </Field>
            <Field label="Concurrency" hint="lanes are split per bottleneck">
              <input
                type="number"
                min={1}
                max={64}
                className="input w-full"
                value={concurrency}
                onChange={(e) => setConcurrency(Math.max(1, Number(e.target.value)))}
              />
            </Field>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Label (optional)">
              <input
                className="input w-full"
                placeholder="opus-5 baseline"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </Field>
            <Field label="Budget cap USD (optional)" hint="cancels the run if exceeded">
              <input
                className="input w-full"
                placeholder="25.00"
                type="number"
                min="0.01"
                step="0.01"
                inputMode="decimal"
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              />
            </Field>
          </div>

          <div className="hairline flex items-center justify-between gap-3 pt-3">
            <span className="mono text-[11px] text-ink-muted">
              {selectedSuite ? (
                <>
                  {selectedSuite.member_count} tasks × k={k} ={" "}
                  <span className="text-ink">{trials} trials</span>
                </>
              ) : (
                "select a suite"
              )}
            </span>
            <div className="flex gap-2">
              <button type="button" onClick={onClose} className="btn btn-ghost">
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={!access.allowed || !suite || !agent || !model || !trials || suites.isPending || agents.isPending || models.isPending || launch.isPending}
              >
                {launch.isPending ? "Queueing…" : "Launch"}
              </button>
            </div>
          </div>

          {access.reason && <p className="text-sm text-ink-muted" role="status">{access.reason}</p>}
          {(suites.isError || agents.isError || models.isError) && <p role="alert" className="text-sm text-fail-agent">Couldn’t load the run options. Close this dialog and try again.</p>}
          {launch.isError && (
            <p className="mono rounded-sm border border-fail-agent/30 bg-fail-agent/10 p-2 text-[11px] text-fail-agent">
              {launch.error instanceof ApiError ? launch.error.detail : String(launch.error)}
            </p>
          )}
        </form>
      </div>
    </Modal>
  );
}

function Field({
  label,
  hint,
  hintTone = "muted",
  children,
}: {
  label: string;
  hint?: string;
  hintTone?: "muted" | "warn";
  children: ReactElement<{ id?: string; "aria-describedby"?: string }>;
}) {
  const id = useId();
  return (
    <div className="block space-y-1">
      <label className="label" htmlFor={id}>{label}</label>
      {cloneElement(children, { id, "aria-describedby": hint ? `${id}-hint` : undefined })}
      {hint && (
        <span
          id={`${id}-hint`}
          className={clsx(
            "block text-[10px]",
            hintTone === "warn" ? "text-warn" : "text-ink-inert",
          )}
        >
          {hint}
        </span>
      )}
    </div>
  );
}
