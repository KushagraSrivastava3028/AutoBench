"use client";
import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Icon } from "@/components/ui/Icon";
import { Modal } from "@/components/ui/Modal";
export function CommandPalette({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState("");
  const runs = useQuery({ queryKey: ["runs", "search"], queryFn: () => api.runs({ limit: 100 }) });
  const tasks = useQuery({ queryKey: ["tasks", "search"], queryFn: () => api.tasks() });
  const navigation = [["Overview", "/"], ["Runs", "/runs"], ["Compare", "/compare"], ["Leaderboard", "/leaderboard"], ["Tasks", "/tasks"], ["Suites", "/suites"], ["Agents & models", "/agents"], ["Budget settings", "/settings/budgets"], ["Operations", "/ops"]];
  const results = [...navigation.map(([label, href]) => ({ label: label!, href: href!, kind: "Page" })), ...(runs.data ?? []).map(r => ({ label: `${r.label ?? r.suite_slug} · ${r.id}`, href: `/runs/${r.id}`, kind: "Run" })), ...(tasks.data ?? []).map(t => ({ label: `${t.title} · ${t.id}`, href: `/tasks/${t.id}`, kind: "Task" }))].filter(r => r.label.toLowerCase().includes(query.toLowerCase())).slice(0, 15);
  return <Modal label="Search AutoBench" onClose={onClose}><div className="command-search"><Icon name="search" /><input autoFocus aria-label="Search runs, tasks and pages" placeholder="Search runs, tasks, or jump to a page…" value={query} onChange={e => setQuery(e.target.value)} /><button className="icon-button" onClick={onClose} aria-label="Close search"><Icon name="close" /></button></div><div className="command-results">{results.map(r => <Link key={r.href} href={r.href} onClick={onClose}><span>{r.label}</span><small>{r.kind}</small><Icon name="arrow" size={15} /></Link>)}{!results.length && <p className="quiet-empty">No matches. Try a run ID or task name.</p>}{(runs.isError || tasks.isError) && <p className="quiet-empty">Some search results could not be loaded.</p>}</div><div className="command-footer">Use Tab to select · Enter to open · Esc to close</div></Modal>;
}
