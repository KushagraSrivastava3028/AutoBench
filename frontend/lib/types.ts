/** Response aliases generated from FastAPI's OpenAPI contract. */
import type { components } from "@/types/api";
type Schema = components["schemas"];
// FastAPI emits defaults in responses, so optional schema defaults are present.
type Present<T> = T extends (infer U)[] ? Present<U>[] : T extends object ? { [P in keyof T]-?: Present<T[P]> } : T;
type Api<T extends keyof Schema> = Present<Schema[T]>;
export type Interval = Api<"IntervalOut">;
export type Budget = Api<"BudgetOut">;
export type Overview = Api<"OverviewOut">;
export type RunStatus = Api<"RunStatus">;
export type TrialStatus = Api<"TrialStatus">;
export type RunSummary = Api<"RunOut">;
export type RunMetrics = Api<"RunMetricsOut">;
export type RunDetail = Api<"RunDetailOut">;
export type MatrixCell = Api<"MatrixCellOut">;
export type MatrixRow = Api<"MatrixRowOut">;
export type Matrix = Api<"MatrixOut">;
export type ScoreItem = Api<"ScoreItemOut">;
export type Score = Api<"ScoreOut">;
export type Trial = Api<"TrialOut">;
export type EventKind = Api<"EventKind">;
export type TrialEvent = Api<"TrialEventOut">;
export type Trajectory = Api<"TrajectoryOut">;
export type DiffFile = Api<"DiffFileOut">;
export type Diff = Api<"DiffOut">;
export type Replay = Api<"ReplayOut">;
export type TaskVersion = Api<"TaskVersionOut">;
export type Task = Api<"TaskOut">;
export type TaskDetail = Api<"TaskDetailOut">;
export type SuiteMember = Api<"SuiteMemberOut">;
export type Suite = Api<"SuiteOut">;
export type SuiteDetail = Api<"SuiteDetailOut">;
export type SuiteHealth = Api<"SuiteHealthOut">;
export type Dashboard = Api<"DashboardOut">;
export type Lane = Api<"LaneOut">;
export type Worker = Api<"WorkerOut">;
export type Flip = Api<"FlipOut">;
export type Comparison = Api<"ComparisonOut">;
export type LeaderboardRow = Api<"LeaderboardRowOut">;
export type Agent = Api<"AgentOut">;
export type Model = Api<"ModelOut">;
export type GateFinding = Api<"LintFindingOut">;
export type Gate = Api<"GateOut">;
export type Validation = Api<"ValidationOut">;
export type Provenance = Api<"ProvenanceOut">;
export type ConsoleConfig = Api<"ConsoleConfigOut">;
export type ApiKey = Api<"ApiKeyOut">;
export type AuditPage = Api<"AuditPageOut">;
export type Session = Api<"SessionOut">;
export type WorkspaceMember = Api<"MemberOut">;

/** Durable SSE envelope; payload is a projection of the run/trial response. */
export interface RunEvent {
  event: "trial_update" | "run_progress" | "run_cancelled" | "heartbeat";
  seq?: number; trial_id?: string; status?: TrialStatus; reward?: number;
  binary_pass?: boolean; error_class?: string | null; duration_ms?: number;
  cost_usd?: number; run_id?: string;
}
