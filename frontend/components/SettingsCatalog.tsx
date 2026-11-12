import { api, safe } from "@/lib/server-api";
import { relativeTime, usd } from "@/lib/format";
import { Chip, EmptyState, KeyValue, Meter, Mono, PageHeader, Panel } from "@/components/ui/primitives";
import type { Agent, Model, Worker } from "@/lib/types";




export default async function SettingsPage() {
  const [agents, models, workers] = await Promise.all([
    safe(api.agents(), [] as Agent[]),
    safe(api.models(), [] as Model[]),
    safe(api.workers(), [] as Worker[]),
  ]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="Agents & models"
        description="Registered agent adapters, model endpoints, and their pricing."
      />

      <div className="grid gap-3 xl:grid-cols-2">
        <Panel title="Agents" subtitle={`${agents.length} registered`}>
          {agents.length === 0 ? (
            <EmptyState title="No agents" body="Seed the catalogue." command="autobench seed" />
          ) : (
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th>Slug</th>
                  <th>Mode</th>
                  <th>Version</th>
                  <th>Egress allowlist</th>
                </tr>
              </thead>
              <tbody>
                {agents.map((agent) => (
                  <tr key={agent.id}>
                    <td className="mono text-ink">{agent.slug}</td>
                    <td>
                      <Chip tone={agent.mode === "in_container" ? "warn" : "neutral"}>
                        {agent.mode}
                      </Chip>
                    </td>
                    <td className="mono text-ink-muted">{agent.version}</td>
                    <td className="mono text-[11px] text-ink-inert">
                      {agent.egress_allowlist.length
                        ? agent.egress_allowlist.join(", ")
                        : "— (no network)"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>

        <Panel title="Models" subtitle={`${models.length} enabled`}>
          {models.length === 0 ? (
            <EmptyState title="No models" body="Seed the catalogue." command="autobench seed" />
          ) : (
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th>Slug</th>
                  <th>Provider</th>
                  <th className="text-right">Context</th>
                  <th className="text-right">In / Mtok</th>
                  <th className="text-right">Out / Mtok</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.id}>
                    <td className="mono text-ink">{model.model_id}</td>
                    <td className="text-ink-muted">{model.provider}</td>
                    <td className="mono tnum text-right text-ink-muted">
                      {model.context_window
                        ? `${(model.context_window / 1000).toFixed(0)}k`
                        : "—"}
                    </td>
                    <td className="mono tnum text-right">{usd(model.input_price_per_mtok)}</td>
                    <td className="mono tnum text-right">{usd(model.output_price_per_mtok)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>

    </div>
  );
}
