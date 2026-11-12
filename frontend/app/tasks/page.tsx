import Link from "next/link";

import { api, safe } from "@/lib/server-api";
import { pct } from "@/lib/format";
import {
  Chip,
  EmptyState,
  Mono,
  PageHeader,
  Panel,
  Sparkline,
} from "@/components/ui/primitives";
import type { Task } from "@/lib/types";

export const dynamic = "force-dynamic";
export const metadata = { title: "Tasks" };

const DIFFICULTY_TONE = {
  easy: "pass",
  medium: "running",
  hard: "warn",
  expert: "fail-agent",
} as const;

export default async function TasksPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string; difficulty?: string; tag?: string; q?: string }>;
}) {
  const filters = await searchParams;
  const tasks = await safe(api.tasks(filters), [] as Task[]);

  const categories = [...new Set(tasks.map((task) => task.category))].sort();

  return (
    <div className="space-y-4">
      <PageHeader
        title="Task catalogue"
        description="Reusable environments, verified scoring, and versioned task definitions."
        actions={
          <Link href="/tasks/new" className="btn btn-primary">
            Author a task
          </Link>
        }
      />

      <div className="flex flex-wrap items-center gap-1.5">
        <Link
          href="/tasks"
          className={
            !filters.category && !filters.difficulty
              ? "chip border-accent/40 bg-accent/15 text-accent"
              : "chip border-border-muted bg-raised text-ink-muted hover:text-ink"
          }
        >
          all
        </Link>
        {categories.map((category) => (
          <Link
            key={category}
            href={`/tasks?category=${category}`}
            className={
              filters.category === category
                ? "chip border-accent/40 bg-accent/15 text-accent"
                : "chip border-border-muted bg-raised text-ink-muted hover:text-ink"
            }
          >
            {category}
          </Link>
        ))}
        <span className="mx-1 h-4 w-px bg-border-muted" />
        {(["easy", "medium", "hard", "expert"] as const).map((difficulty) => (
          <Link
            key={difficulty}
            href={`/tasks?difficulty=${difficulty}`}
            className={
              filters.difficulty === difficulty
                ? "chip border-accent/40 bg-accent/15 text-accent"
                : "chip border-border-muted bg-raised text-ink-muted hover:text-ink"
            }
          >
            {difficulty}
          </Link>
        ))}
      </div>

      <Panel subtitle={`${tasks.length} tasks`}>
        {tasks.length === 0 ? (
          <EmptyState
            title="No tasks registered"
            body="A task is a directory: instruction.md, environment/Dockerfile, tests/ and solution/. Register the bundled corpus to see the shape of one."
            command="autobench seed"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="telemetry-table">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Category</th>
                  <th>Difficulty</th>
                  <th>Tags</th>
                  <th className="text-right">Oracle</th>
                  <th className="text-right">Null</th>
                  <th>Published</th>
                  <th className="text-right">pass@1 trend</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => {
                  const version = task.latest_version;
                  const calibrated = version?.oracle_reward === 1 && version?.null_reward === 0;
                  return (
                    <tr key={task.id}>
                      <td>
                        <Link
                          href={`/tasks/${task.id}`}
                          className="mono text-accent hover:text-accent-hover hover:underline"
                        >
                          {task.id}
                        </Link>
                        <div className="max-w-md truncate text-[11px] text-ink-inert">
                          {task.title}
                        </div>
                      </td>
                      <td className="text-ink-muted">{task.category}</td>
                      <td>
                        <Chip
                          tone={
                            DIFFICULTY_TONE[task.difficulty as keyof typeof DIFFICULTY_TONE] ??
                            "neutral"
                          }
                        >
                          {task.difficulty}
                        </Chip>
                      </td>
                      <td>
                        <div className="flex flex-wrap gap-1">
                          {task.tags.slice(0, 3).map((tag) => (
                            <span
                              key={tag}
                              className="mono rounded-xs border border-border-subtle px-1 text-[10px] text-ink-inert"
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="mono tnum text-right">
                        <span
                          className={
                            version?.oracle_reward === 1 ? "text-pass-text" : "text-ink-inert"
                          }
                        >
                          {version?.oracle_reward?.toFixed(3) ?? "—"}
                        </span>
                      </td>
                      <td className="mono tnum text-right">
                        <span
                          className={
                            version?.null_reward === 0 ? "text-pass-text" : "text-fail-agent"
                          }
                        >
                          {version?.null_reward?.toFixed(3) ?? "—"}
                        </span>
                      </td>
                      <td>
                        {version?.published ? (
                          <Chip tone={calibrated ? "pass" : "warn"}>
                            {calibrated ? "calibrated" : "published"}
                          </Chip>
                        ) : (
                          <Chip tone="neutral">draft</Chip>
                        )}
                      </td>
                      <td className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <Mono className="text-[11px] text-ink-muted">
                            {task.history.length
                              ? pct(task.history[task.history.length - 1], 0)
                              : "—"}
                          </Mono>
                          <Sparkline values={task.history} width={72} height={20} />
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
