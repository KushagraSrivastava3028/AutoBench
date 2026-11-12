import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError } from "@/lib/server-api";
import { relativeTime } from "@/lib/format";
import {
  Chip,
  KeyValue,
  Mono,
  PageHeader,
  Panel,
  Sparkline,
} from "@/components/ui/primitives";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return { title: id };
}

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let task;
  try {
    task = await api.task(id);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const latest = task.latest_version;
  const spec = task.spec as Record<string, Record<string, unknown>>;
  const calibrated = latest?.oracle_reward === 1 && latest?.null_reward === 0;

  return (
    <div className="space-y-3">
      <PageHeader
        title={task.id}
        description={task.title}
        badge={
          <Chip tone={calibrated ? "pass" : latest?.published ? "warn" : "neutral"}>
            {calibrated ? "calibrated" : latest?.published ? "published" : "draft"}
          </Chip>
        }
        actions={
          <Link href="/tasks" className="btn btn-ghost">
            ← Catalogue
          </Link>
        }
      />

      <div className="grid gap-3 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <Panel title="Instruction" subtitle="what the agent is handed">
            <pre className="mono max-h-80 overflow-auto whitespace-pre-wrap p-3 text-[12px] leading-relaxed text-ink-muted">
              {task.instruction}
            </pre>
          </Panel>

          <Panel title="environment/Dockerfile" subtitle="the world the agent wakes up in">
            <pre className="mono max-h-72 overflow-auto whitespace-pre-wrap p-3 text-[11px] leading-relaxed text-ink-muted">
              {task.dockerfile || "not available on this host"}
            </pre>
          </Panel>

          <Panel title="Version history">
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>Content hash</th>
                  <th className="text-right">Oracle</th>
                  <th className="text-right">Null</th>
                  <th>State</th>
                  <th className="text-right">Registered</th>
                </tr>
              </thead>
              <tbody>
                {task.versions.map((version) => (
                  <tr key={version.id}>
                    <td className="mono">{version.semver}</td>
                    <td className="mono text-ink-inert">
                      {version.content_hash.replace("sha256:", "").slice(0, 16)}
                    </td>
                    <td className="mono tnum text-right">
                      <span
                        className={
                          version.oracle_reward === 1 ? "text-pass-text" : "text-ink-inert"
                        }
                      >
                        {version.oracle_reward?.toFixed(3) ?? "—"}
                      </span>
                    </td>
                    <td className="mono tnum text-right">
                      <span
                        className={version.null_reward === 0 ? "text-pass-text" : "text-fail-agent"}
                      >
                        {version.null_reward?.toFixed(3) ?? "—"}
                      </span>
                    </td>
                    <td>
                      <Chip tone={version.published ? "pass" : "neutral"}>
                        {version.published ? "published" : "draft"}
                      </Chip>
                    </td>
                    <td className="text-right text-[11px] text-ink-muted">
                      {relativeTime(version.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
        </div>

        <div className="space-y-3">
          <Panel title="Calibration" bodyClassName="p-3 space-y-2">
            <p className="text-[11px] leading-relaxed text-ink-muted">
              A task where the null agent scores above zero has a leak. One where the oracle scores
              below 1.0 has a bug. Neither may enter a suite, because both corrupt every number
              computed from it afterwards.
            </p>
            <div className="space-y-0.5">
              <KeyValue label="Oracle reward">
                <span className={latest?.oracle_reward === 1 ? "text-pass-text" : "text-fail-agent"}>
                  {latest?.oracle_reward?.toFixed(3) ?? "not run"}
                </span>
              </KeyValue>
              <KeyValue label="Null reward">
                <span className={latest?.null_reward === 0 ? "text-pass-text" : "text-fail-agent"}>
                  {latest?.null_reward?.toFixed(3) ?? "not run"}
                </span>
              </KeyValue>
              <KeyValue label="Validated">{relativeTime(latest?.validated_at)}</KeyValue>
            </div>
            {!calibrated && (
              <code className="mono block rounded-sm border border-border-muted bg-canvas px-2 py-1.5 text-[11px] text-accent">
                autobench validate tasks/{task.id} --deep
              </code>
            )}
          </Panel>

          <Panel title="Limits" bodyClassName="p-3 space-y-0.5">
            <KeyValue label="Agent timeout">{String(spec.agent?.timeout_sec ?? "—")}s</KeyValue>
            <KeyValue label="Max tool calls">{String(spec.agent?.max_tool_calls ?? "—")}</KeyValue>
            <KeyValue label="Max tokens">{String(spec.agent?.max_tokens ?? "—")}</KeyValue>
            <KeyValue label="Verifier timeout">
              {String(spec.verifier?.timeout_sec ?? "—")}s
            </KeyValue>
            <KeyValue label="Network">{String(spec.environment?.network ?? "none")}</KeyValue>
            <KeyValue label="CPUs">{String(spec.environment?.cpus ?? "—")}</KeyValue>
            <KeyValue label="Memory">{String(spec.environment?.memory ?? "—")}</KeyValue>
            <KeyValue label="Scoring">{String(spec.scoring?.mode ?? "binary")}</KeyValue>
            <KeyValue label="Deterministic">
              {String(spec.task?.deterministic ?? true)}
            </KeyValue>
          </Panel>

          <Panel title="Difficulty over time" bodyClassName="p-3">
            {task.history.length > 1 ? (
              <>
                <Sparkline values={task.history} width={260} height={48} />
                <p className="mt-2 text-[11px] text-ink-inert">
                  pass@1 across recent runs of this task.
                </p>
              </>
            ) : (
              <p className="text-[11px] text-ink-inert">
                Not enough history yet — run this task a few times.
              </p>
            )}
          </Panel>

          <Panel title="Metadata" bodyClassName="p-3 space-y-0.5">
            <KeyValue label="Author">{task.author ?? "—"}</KeyValue>
            <KeyValue label="Category">{task.category}</KeyValue>
            <KeyValue label="Difficulty">{task.difficulty}</KeyValue>
            <KeyValue label="Tags">{task.tags.join(", ") || "—"}</KeyValue>
            <KeyValue label="Content hash">
              <Mono className="text-[11px]">
                {latest?.content_hash.replace("sha256:", "").slice(0, 16) ?? "—"}
              </Mono>
            </KeyValue>
          </Panel>
        </div>
      </div>
    </div>
  );
}
