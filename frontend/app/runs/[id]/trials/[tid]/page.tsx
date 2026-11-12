import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError, safe } from "@/lib/server-api";
import { PageHeader } from "@/components/ui/primitives";
import { TrialInspector } from "@/components/trial/TrialInspector";
import type { Diff, Trajectory } from "@/lib/types";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ tid: string }> }) {
  const { tid } = await params;
  return { title: `Trial ${tid.replace(/-/g, "").slice(0, 8)}` };
}

const EMPTY_DIFF: Diff = { counts: {}, truncated: false, files: [] };

export default async function TrialPage({
  params,
}: {
  params: Promise<{ id: string; tid: string }>;
}) {
  const { id, tid } = await params;

  let trial;
  try {
    trial = await api.trial(tid);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  // The diff and trajectory are supporting evidence: if either is missing the
  // page still has to render the verdict and the error, which is the part that
  // explains the red cell.
  const [trajectory, diff] = await Promise.all([
    safe(api.trajectory(tid), { trial_id: tid, total: 0, events: [] } as Trajectory),
    safe(api.diff(tid), EMPTY_DIFF),
  ]);

  return (
    <div className="space-y-3">
      <PageHeader
        title={trial.task_id}
        description={`Attempt k${trial.attempt_index} · trial ${tid.replace(/-/g, "").slice(0, 8)} · seed ${trial.seed_exact}`}
        actions={
          <Link href={`/runs/${id}`} className="btn btn-ghost">
            ← Back to run
          </Link>
        }
      />
      <TrialInspector trial={trial} trajectory={trajectory} diff={diff} />
    </div>
  );
}
