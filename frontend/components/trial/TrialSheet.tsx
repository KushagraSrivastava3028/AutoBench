"use client";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Modal } from "@/components/ui/Modal";
import { Icon } from "@/components/ui/Icon";
import { TrialInspector } from "./TrialInspector";
export function TrialSheet({ trialId, runId, onClose }: { trialId: string; runId: string; onClose: () => void }) {
  const trial = useQuery({ queryKey: ["trial", trialId], queryFn: () => api.trial(trialId) });
  const trajectory = useQuery({ queryKey: ["trajectory", trialId], queryFn: () => api.trajectory(trialId) });
  const diff = useQuery({ queryKey: ["diff", trialId], queryFn: () => api.diff(trialId) });
  return <Modal label="Trial inspector" sheet onClose={onClose}><header className="trial-sheet-header"><div><div className="eyebrow">Trial inspector</div><h2>{trial.data?.task_id ?? trialId.slice(0, 8)}</h2></div><Link href={`/runs/${runId}/trials/${trialId}`} className="btn btn-ghost">Open full page <Icon name="arrow" size={14} /></Link><button className="icon-button" aria-label="Close trial inspector" onClick={onClose}><Icon name="close" /></button></header><div className="p-4">{trial.isPending && <div className="skeleton" />}{trial.isError && <p role="alert" className="info-banner warning">The trial couldn’t be loaded. Try opening its full page.</p>}{(trajectory.isError || diff.isError) && <p className="info-banner warning">Some artifacts are unavailable or have expired.</p>}{trial.data && <TrialInspector trial={trial.data} trajectory={trajectory.data ?? { trial_id: trialId, total: 0, events: [] }} diff={diff.data ?? { counts: {}, truncated: false, files: [] }} />}</div></Modal>;
}
