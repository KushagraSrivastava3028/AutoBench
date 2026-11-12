"use client";

/**
 * The authoring wizard.
 *
 * Upload a task directory, get the gate report back with the exact failing file
 * and line. The static gates run in under a second, so an author sees a leaking
 * instruction or an output-grading test while they are still holding the
 * context — rather than in a CI failure forty minutes later.
 */

import { useState } from "react";
import Link from "next/link";
import { useAccess } from "@/lib/access";
import clsx from "clsx";

import { ApiError } from "@/lib/api";
import { Chip, Mono, PageHeader, Panel } from "@/components/ui/primitives";
import type { Validation, TaskVersion } from "@/lib/types";

const GATE_DOC: Record<string, string> = {
  schema: "task.toml parses, required keys present, semver valid",
  image: "base image pinned by digest; no unpinned package pulls at build time",
  security: "no Docker socket, no privileged, no host network, no escape capabilities",
  leak: "instruction.md does not quote the tests (8-gram overlap < 15%)",
  tolerance: "every numeric assertion declares a tolerance",
  "state-based": "tests assert on container state, not on the agent's output",
  oracle: "solution/solve.sh scores exactly 1.000",
  null: "the null agent scores exactly 0.000",
  hermetic: "identical verdict with the network severed, twice",
  determinism: "five oracle runs produce an identical report",
  timing: "oracle wall-clock under 40% of the agent budget",
  cleanup: "no residual containers, volumes or networks after teardown",
};

export default function AuthorPage() {
  const [report, setReport] = useState<Validation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [step, setStep] = useState(0);
  const [saved, setSaved] = useState<TaskVersion | null>(null);
  const access = useAccess("author");

  if (!access.allowed) return <div className="space-y-4"><PageHeader title="Create a task" /><Panel title={access.loading ? "Loading workspace permissions" : "Task authoring unavailable"} bodyClassName="p-5"><p className="mb-4 text-sm text-ink-muted" role="status">{access.reason}</p><Link href="/tasks" className="btn btn-ghost">Back to tasks</Link></Panel></div>;

  const upload = async (file: File, deep = false) => {
    setFile(file);
    setSaved(null);
    setBusy(true);
    setError(null);
    setReport(null);
    try {
      if (file.size > 32 * 1024 * 1024) throw new Error("Task archives must be at most 32 MiB.");
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`/api/tasks/validate${deep ? "?deep=true" : ""}`, { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) {
        throw new ApiError(
          response.status,
          String(payload.title ?? "Validation failed"),
          String(payload.detail ?? "The archive could not be read."),
        );
      }
      setReport(payload as Validation);
      setStep(deep && payload.publishable ? 3 : payload.ok ? 2 : 1);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.detail : String(caught));
    } finally {
      setBusy(false);
    }
  };

  const saveTask = async (publish: boolean) => {
    if (!file) return;
    setBusy(true); setError(null);
    try {
      const body = new FormData(); body.append("file", file);
      const response = await fetch(`/api/tasks/import?publish=${publish}`, { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "The task could not be saved.");
      setSaved(data as TaskVersion); setStep(3);
    } catch (err) { setError(err instanceof Error ? err.message : "The task could not be saved."); }
    finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      <PageHeader
        title="Create a task"
        description="Upload your environment, check its quality, and publish a reproducible benchmark task."
      />
      <ol className="wizard-steps" aria-label="Task creation progress">{["Upload", "Validate", "Calibrate", "Publish"].map((label, i) => <li key={label} aria-current={i === step ? "step" : undefined}><span>{i < step ? "✓" : i + 1}</span>{label}</li>)}</ol>

      <Panel title="Upload" bodyClassName="p-4">
        <label
          className={clsx(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border-muted bg-canvas px-6 py-10 text-center transition-colors hover:border-accent",
            busy && "pointer-events-none opacity-60",
          )}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            const file = event.dataTransfer.files[0];
            if (file) void upload(file);
          }}
        >
          <input
            type="file"
            accept=".tar,.gz,.tgz,.tar.gz"
            className="sr-only"
            aria-label="Upload a task archive"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void upload(file);
            }}
          />
          <span className="text-[13px] font-semibold text-ink">
            {busy ? "Running quality checks…" : file ? file.name : "Drop a .tar.gz, or click to choose"}
          </span>
          <span className="mono text-[11px] leading-relaxed text-ink-inert">
            task.toml · instruction.md · environment/Dockerfile · tests/ · solution/
          </span>
        </label>

        <div className="mt-3 rounded-sm border border-border-subtle bg-canvas p-2.5">
          <div className="label mb-1">Or from the terminal</div>
          <code className="mono text-[11px] text-accent">
            <span className="mr-1.5 text-ink-inert">$</span>
            autobench validate tasks/my-task --deep
          </code>
        </div>

        {error && (
          <p className="mono mt-3 rounded-sm border border-fail-agent/30 bg-fail-agent/10 p-2.5 text-[11px] text-fail-agent">
            {error}
          </p>
        )}
      </Panel>

      {report?.ok && !saved && <Panel title={step >= 3 ? "Ready to publish" : "Calibration & publication"} bodyClassName="p-5"><p className="text-sm leading-relaxed text-ink-muted">Static checks passed. Dynamic validation runs the oracle, null agent, and reproducibility checks in an isolated executor. Every required gate must pass before publication.</p><div className="mt-4 flex flex-wrap gap-2"><button className="btn btn-ghost" disabled={busy} onClick={() => void saveTask(false)}>Save as draft</button><button className="btn btn-primary" disabled={busy || !file} onClick={() => file && void upload(file, true)}>Run calibration checks</button><button className="btn btn-primary" disabled={busy || !report.publishable} onClick={() => void saveTask(true)}>Publish task</button></div></Panel>}
      {saved && <Panel title={saved.published ? "Task published" : "Draft saved"} bodyClassName="p-5"><p className="mb-3 text-sm text-ink-muted">Version {saved.semver} is saved with its content hash. {saved.published ? "Add this pinned version to an evaluation suite." : "Complete dynamic validation before publication."}</p><Link href={`/tasks/${report?.task_id}`} className="btn btn-primary">Open task</Link></Panel>}
      {report && (
        <Panel
          title={`Gate report — ${report.task_id}`}
          subtitle={report.content_hash.replace("sha256:", "").slice(0, 16)}
          actions={
            <Chip tone={report.publishable ? "pass" : report.ok ? "warn" : "fail-agent"}>
              {report.publishable ? "publishable" : report.ok ? "lints clean" : "blocked"}
            </Chip>
          }
          bodyClassName="p-3 space-y-2"
        >
          {!report.publishable && report.ok && (
            <p className="rounded-sm border border-warn/30 bg-warn/10 px-2.5 py-2 text-[12px] leading-relaxed text-warn">
              The static gates pass, but the calibration gates were skipped — a skipped gate is not
              a passed gate. Publishing claims the oracle scores 1.0 and the null agent 0.0 on this
              exact content hash, and you cannot make that claim from a static lint. Run{" "}
              <code className="mono">autobench validate --deep</code>.
            </p>
          )}

          {report.gates.map((gate) => (
            <div key={gate.gate} className="rounded-sm border border-border-subtle">
              <div className="flex items-center justify-between gap-3 px-2.5 py-1.5">
                <div className="flex min-w-0 items-center gap-2">
                  <Chip
                    tone={
                      gate.status === "passed"
                        ? "pass"
                        : gate.status === "failed"
                          ? "fail-agent"
                          : gate.status === "warning"
                            ? "warn"
                            : "neutral"
                    }
                  >
                    {gate.status}
                  </Chip>
                  <Mono className="text-[12px] text-ink">{gate.gate}</Mono>
                  <span className="truncate text-[11px] text-ink-muted">{gate.detail}</span>
                </div>
                <Mono className="shrink-0 text-[10px] text-ink-inert">{gate.duration_ms}ms</Mono>
              </div>

              {gate.findings.length > 0 && (
                <ul className="divide-y divide-border-subtle border-t border-border-subtle">
                  {gate.findings.map((finding, index) => (
                    <li key={`${finding.file}-${finding.line}-${index}`} className="px-2.5 py-2">
                      <Mono
                        className={clsx(
                          "text-[11px]",
                          finding.severity === "error" ? "text-fail-agent" : "text-warn",
                        )}
                      >
                        {finding.file}:{finding.line}
                      </Mono>
                      <p className="mt-0.5 text-[12px] leading-relaxed text-ink">
                        {finding.message}
                      </p>
                      <p className="mt-0.5 text-[11px] leading-relaxed text-ink-muted">
                        {finding.remediation}
                      </p>
                    </li>
                  ))}
                </ul>
              )}

              {gate.status === "skipped" && (
                <p className="border-t border-border-subtle px-2.5 py-1.5 text-[11px] text-ink-inert">
                  {GATE_DOC[gate.gate]}
                </p>
              )}
            </div>
          ))}
        </Panel>
      )}

      <Panel title="The grading contract" bodyClassName="p-4">
        <ol className="space-y-2.5 text-[12px] leading-relaxed text-ink-muted">
          <li>
            <strong className="text-ink">Tests are invisible during the agent phase.</strong> They
            live on the host and are mounted read-only into a separate verifier container only
            after the agent's container is frozen. The agent cannot read them, cannot chmod them,
            and cannot overfit to them.
          </li>
          <li>
            <strong className="text-ink">Grade the world, not the words.</strong> Assert on the
            final container state. If the task says "send the email", check the mock SMTP log — not
            what the agent said it did.
          </li>
          <li>
            <strong className="text-ink">Tolerances are declared.</strong> Numeric comparison uses
            an explicit <code className="mono">rel_tol</code>. Penalising a correctly-rounded{" "}
            <code className="mono">96.12</code> because the grader wanted{" "}
            <code className="mono">96.124991</code> is a lint error here, not a model result.
          </li>
          <li>
            <strong className="text-ink">The oracle scores exactly 1.0 and the null agent exactly 0.0.</strong>{" "}
            A task where the null agent scores above zero has a leak; one where the oracle scores
            below one has a bug. Neither may enter a suite.
          </li>
        </ol>
      </Panel>
    </div>
  );
}
