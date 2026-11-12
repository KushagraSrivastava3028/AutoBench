"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api, request, ApiError } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";
import { Icon } from "@/components/ui/Icon";
import type { Suite } from "@/lib/types";
import { useAccess } from "@/lib/access";
export function NewSuiteButton() {
  const [open, setOpen] = useState(false);
  const access = useAccess("author");
  return <><button className="btn btn-primary" disabled={!access.allowed} title={access.reason} onClick={() => setOpen(true)}><Icon name="plus" size={15} />New suite</button>{open && <SuiteDialog onClose={() => setOpen(false)} />}</>;
}
function SuiteDialog({ onClose }: { onClose: () => void }) {
  const router = useRouter(); const [name, setName] = useState(""); const [selected, setSelected] = useState<string[]>([]); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const tasks = useQuery({ queryKey: ["tasks", "suite-builder"], queryFn: () => api.tasks() });
  const slug = name.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 64);
  const published = tasks.data?.filter(t => t.latest_version?.published) ?? [];
  return <Modal label="Create a suite" onClose={onClose}><form className="space-y-4 p-6" onSubmit={async e => { e.preventDefault(); setBusy(true); setError(""); try { const suite = await request<Suite>("/suites", { method: "POST", body: JSON.stringify({ name, slug, task_version_ids: selected }) }); onClose(); router.push(`/suites/${suite.slug}`); router.refresh(); } catch (err) { setError(err instanceof ApiError ? err.detail : "The suite could not be created."); } finally { setBusy(false); } }}><div className="flex items-center justify-between"><h2 className="text-xl font-semibold">Create an evaluation suite</h2><button className="icon-button" aria-label="Close" type="button" onClick={onClose}><Icon name="close" /></button></div><p className="text-sm text-ink-muted">Choose published task versions. They stay pinned so future comparisons remain reproducible.</p><label className="block text-sm font-medium">Suite name<input required maxLength={64} className="input mt-2 w-full" placeholder="Customer support benchmark" value={name} onChange={e => setName(e.target.value)} /></label><p className="mono text-xs text-ink-muted">{slug || "suite-slug"}</p><fieldset><legend className="mb-2 text-sm font-medium">Tasks · {selected.length} selected</legend><div className="max-h-64 space-y-1 overflow-auto rounded-lg border border-border-subtle p-2">{published.map(t => <label key={t.id} className="flex cursor-pointer items-center gap-3 rounded-md p-2 hover:bg-raised"><input type="checkbox" checked={selected.includes(t.latest_version!.id)} onChange={e => setSelected(v => e.target.checked ? [...v, t.latest_version!.id] : v.filter(id => id !== t.latest_version!.id))} /><span className="text-sm">{t.title}<span className="mono ml-2 text-[10px] text-ink-muted">v{t.latest_version!.semver}</span></span></label>)}{tasks.isPending && <p className="p-3 text-sm text-ink-muted">Loading tasks…</p>}{!tasks.isPending && !published.length && <p className="p-3 text-sm text-ink-muted">Publish tasks before creating a suite.</p>}</div></fieldset>{(error || tasks.isError) && <p role="alert" className="text-sm text-fail-agent">{error || "Tasks could not be loaded."}</p>}<button className="btn btn-primary" disabled={busy || !selected.length || !slug}>{busy ? "Creating…" : `Create suite · ${selected.length} tasks`}</button></form></Modal>;
}
