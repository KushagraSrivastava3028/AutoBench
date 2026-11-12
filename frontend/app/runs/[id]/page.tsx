import { notFound } from "next/navigation";

import { api, ApiError } from "@/lib/server-api";
import { RunHeader } from "@/components/run/RunHeader";
import { RunPanels } from "@/components/run/RunPanels";
import { TrialMatrix } from "@/components/run/TrialMatrix";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return { title: `Run ${id.replace(/-/g, "").slice(0, 8)}` };
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;

  let run;
  let matrix;
  try {
    [run, matrix] = await Promise.all([api.run(id), api.matrix(id)]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const live = run.status === "running" || run.status === "queued";

  return (
    <div className="space-y-3">
      <RunHeader initial={run} />
      <TrialMatrix runId={id} initial={matrix} live={live} />
      <RunPanels run={run} matrix={matrix} />
    </div>
  );
}
