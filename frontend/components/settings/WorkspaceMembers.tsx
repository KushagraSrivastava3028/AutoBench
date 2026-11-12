"use client";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, request } from "@/lib/api";
import type { WorkspaceMember } from "@/lib/types";
import { Panel, EmptyState } from "@/components/ui/primitives";
import { relativeTime } from "@/lib/format";

export function WorkspaceMembers() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ["members"], queryFn: () => request<WorkspaceMember[]>("/auth/members") });
  const mutation = useMutation({
    mutationFn: (member: WorkspaceMember) => request<WorkspaceMember>(`/auth/members/${member.id}`, { method: "PATCH", body: JSON.stringify({ role: member.role, active: member.active }) }),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ["members"] }); },
  });
  const [drafts, setDrafts] = useState<Record<string, { role: string; active: boolean }>>({});
  if (query.isError) return <p role="alert" className="info-banner warning">{query.error instanceof ApiError && query.error.status === 403 ? "Administrator access is required to manage workspace members." : "Members could not be loaded. Please try again."}</p>;
  return <Panel title="Workspace members" subtitle="Google Workspace accounts and permissions" bodyClassName="p-5">
    <p className="mb-5 text-sm text-ink-muted">Members join with Google sign-in and receive viewer access. Runners launch evaluations, authors also publish tasks and suites, and administrators manage access and workspace settings. Disabling a member immediately revokes their sessions.</p>
    {query.isPending && <p role="status">Loading members…</p>}
    {query.data?.length === 0 && <EmptyState title="No Google members yet" body="Configure Google Workspace sign-in, then sign in with the initial administrator account." />}
    <div className="space-y-3">{query.data?.map(member => {
      const draft = drafts[member.id] ?? member;
      const dirty = draft.role !== member.role || draft.active !== member.active;
      return <form key={member.id} className="flex flex-wrap items-end gap-4 rounded-xl border border-border-subtle p-4" onSubmit={e => { e.preventDefault(); mutation.mutate({ ...member, ...draft }); }}>
        <div className="min-w-0 flex-1"><strong className="block break-all text-sm">{member.email}</strong><span className="text-xs text-ink-muted">{member.display_name} · Last sign-in {relativeTime(member.last_login_at)}</span></div>
        <label className="text-xs font-medium">Role<select aria-label={`Role for ${member.email}`} className="input mt-2 block" value={draft.role} onChange={e => setDrafts({ ...drafts, [member.id]: { ...draft, role: e.target.value } })}>{["viewer", "runner", "author", "admin"].map(role => <option key={role}>{role}</option>)}</select></label>
        <label className="flex items-center gap-2 pb-3 text-sm"><input type="checkbox" checked={draft.active} onChange={e => setDrafts({ ...drafts, [member.id]: { ...draft, active: e.target.checked } })} />Active<span className="sr-only"> access for {member.email}</span></label>
        <button className="btn btn-primary" disabled={!dirty || mutation.isPending}>Save<span className="sr-only"> access for {member.email}</span></button>
      </form>;
    })}</div>
    {mutation.isError && <p role="alert" className="info-banner warning mt-4">{mutation.error instanceof ApiError ? mutation.error.detail : "Access could not be updated."}</p>}
    {mutation.isSuccess && <p role="status" className="mt-4 text-sm text-pass-text">Workspace access updated.</p>}
  </Panel>;
}
