"""Request and response models.

These are the contract the frontend generates its types from
(``openapi-typescript`` against ``/openapi.json``), so field names here are
field names in the UI. Changing one is a breaking change; adding one is not.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autobench.db.models import EventKind, RunStatus, TrialStatus


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SeededApiModel(ApiModel):
    seed: int
    seed_exact: str = Field(
        default="", description="Exact decimal seed, safe for JavaScript clients."
    )

    @model_validator(mode="after")
    def exact_seed(self) -> SeededApiModel:
        self.seed_exact = str(self.seed)
        return self


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


class LintFindingOut(ApiModel):
    rule: str
    file: str
    line: int
    message: str
    remediation: str
    severity: str


class GateOut(ApiModel):
    gate: str
    status: Literal["passed", "failed", "skipped", "warning"]
    detail: str
    duration_ms: int = 0
    findings: list[LintFindingOut] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationOut(ApiModel):
    task_id: str
    content_hash: str
    ok: bool
    publishable: bool
    oracle_reward: float | None = None
    null_reward: float | None = None
    gates: list[GateOut]


class TaskVersionOut(ApiModel):
    id: UUID
    semver: str
    content_hash: str
    image_digest: str | None
    oracle_reward: float | None
    null_reward: float | None
    published: bool
    validated_at: datetime | None
    created_at: datetime


class TaskOut(ApiModel):
    id: str
    title: str
    category: str
    difficulty: str
    tags: list[str]
    author: str | None
    created_at: datetime
    latest_version: TaskVersionOut | None = None
    #: Rolling pass@1 across recent runs — the sparkline on /tasks.
    history: list[float] = Field(default_factory=list)


class TaskDetailOut(TaskOut):
    instruction: str = ""
    dockerfile: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)
    versions: list[TaskVersionOut] = Field(default_factory=list)


class TaskRegisterIn(ApiModel):
    path: str = Field(description="Server-side path to the task directory.")
    publish: bool = Field(default=False, description="Run the calibration gates and publish.")


# ---------------------------------------------------------------------------
# suites
# ---------------------------------------------------------------------------


class SuiteMemberOut(ApiModel):
    task_version_id: UUID
    task_id: str
    title: str
    difficulty: str
    weight: float = 1.0


class SuiteOut(ApiModel):
    id: UUID
    slug: str
    name: str
    description: str | None
    created_at: datetime
    member_count: int = 0


class SuiteDetailOut(SuiteOut):
    members: list[SuiteMemberOut] = Field(default_factory=list)


class SuiteCreateIn(ApiModel):
    slug: str
    name: str
    description: str | None = None
    #: Pins versions, never tasks. Empty means "every published task".
    task_version_ids: list[UUID] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# agents and models
# ---------------------------------------------------------------------------


class AgentOut(ApiModel):
    id: UUID
    slug: str
    name: str
    adapter: str
    mode: str
    version: str
    egress_allowlist: list[str] = Field(default_factory=list)


class ModelOut(ApiModel):
    id: UUID
    slug: str
    provider: str
    model_id: str
    display_name: str
    context_window: int | None
    input_price_per_mtok: float | None
    output_price_per_mtok: float | None
    enabled: bool


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


class RunCreateIn(ApiModel):
    suite: str = Field(description="Suite slug or UUID.")
    agent: str = Field(description="Agent slug.")
    model: str = Field(default="mock/mock-1", description="Model slug, or provider/model-id.")
    k: int = Field(default=1, ge=1, le=50)
    concurrency: int = Field(default=8, ge=1, le=64)
    seed: int | None = None
    label: str | None = None
    budget_usd: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description="Cancel the run if the spend exceeds this.",
    )
    config: dict[str, Any] = Field(default_factory=dict)


class IntervalOut(ApiModel):
    point: float
    low: float
    high: float
    margin: float
    method: str


class RunMetricsOut(ApiModel):
    k: int
    pass_at_1: IntervalOut
    pass_at_k: IntervalOut
    pass_caret_k: IntervalOut
    #: pass@k − pass^k. The number that matters for shipping.
    reliability_gap: float
    mean_reward: float
    total_trials: int
    total_passes: int
    infra_errors: int
    tasks_evaluated: int
    #: False for k < 3 — the UI marks the run "indicative, not reportable".
    reportable: bool


class RunOut(SeededApiModel):
    id: UUID
    suite_id: UUID
    suite_slug: str = ""
    agent_id: UUID
    agent_slug: str = ""
    model_id: UUID
    model_slug: str = ""
    k: int
    concurrency: int
    status: RunStatus
    seed: int
    label: str | None
    trials_total: int
    trials_done: int
    cost_usd: float
    budget_usd: float | None = None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


class RunDetailOut(RunOut):
    metrics: RunMetricsOut | None = None
    elapsed_sec: float | None = None
    eta_sec: float | None = None
    error_breakdown: dict[str, int] = Field(default_factory=dict)


class MatrixCellOut(ApiModel):
    """One cell of the task x attempt grid — the primary UI payload."""

    trial_id: UUID
    attempt: int
    status: TrialStatus
    reward: float | None = None
    binary_pass: bool | None = None
    error_class: str | None = None
    duration_ms: int | None = None
    #: Infra retries, shown as a separate counter so they never read as failures.
    attempt_count: int = 0
    cost_usd: float | None = None
    finished_at: datetime | None = None


class MatrixRowOut(ApiModel):
    task_id: str
    task_version_id: UUID
    title: str
    difficulty: str
    cells: list[MatrixCellOut]
    passes: int = 0
    attempts: int = 0


class MatrixOut(ApiModel):
    run_id: UUID
    k: int
    rows: list[MatrixRowOut]


# ---------------------------------------------------------------------------
# trials
# ---------------------------------------------------------------------------


class ScoreItemOut(ApiModel):
    id: str
    passed: bool
    weight: float
    detail: str | None = None


class ScoreOut(ApiModel):
    reward: float
    binary_pass: bool
    items: list[ScoreItemOut] = Field(default_factory=list)
    error: str | None = None
    tool_calls: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    setup_ms: int | None = None
    agent_ms: int | None = None
    verify_ms: int | None = None


class TrialOut(SeededApiModel):
    id: UUID
    run_id: UUID
    task_id: str
    task_version_id: UUID
    attempt_index: int
    status: TrialStatus
    seed: int
    attempt_count: int
    container_id: str | None
    error_class: str | None
    error_detail: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    score: ScoreOut | None = None


class TrialEventOut(ApiModel):
    seq: int
    ts: datetime
    kind: EventKind
    payload: dict[str, Any]
    elapsed_ms: int | None = None
    cumulative_tokens: int | None = None


class TrajectoryOut(ApiModel):
    trial_id: UUID
    total: int
    events: list[TrialEventOut]


class ArtifactOut(ApiModel):
    kind: str
    url: str
    size_bytes: int
    content_type: str


class ReplayOut(ApiModel):
    trial_id: UUID
    task_id: str
    content_hash: str
    commands: list[str]


class DiffFileOut(ApiModel):
    path: str
    change: str
    before: str | None = None
    after: str | None = None
    size_before: int = 0
    size_after: int = 0
    binary: bool = False


class DiffOut(ApiModel):
    counts: dict[str, int]
    truncated: bool
    files: list[DiffFileOut]


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


class FlipOut(ApiModel):
    task_id: str
    before: float
    after: float
    direction: str


class ComparisonOut(ApiModel):
    shared_tasks: int
    baseline_score: float
    candidate_score: float
    delta: IntervalOut
    mcnemar_p: float
    #: True when the intervals overlap. The UI must render this grey.
    inconclusive: bool
    headline: str
    flips: list[FlipOut]
    baseline_metrics: RunMetricsOut
    candidate_metrics: RunMetricsOut


class LeaderboardRowOut(ApiModel):
    run_id: UUID
    agent_slug: str
    model_slug: str
    label: str | None
    k: int
    pass_at_1: IntervalOut
    pass_caret_k: IntervalOut
    reliability_gap: float
    cost_usd: float
    cost_per_solve: float | None
    p95_latency_sec: float | None
    finished_at: datetime | None
    reportable: bool


class LaneOut(ApiModel):
    capacity: int
    in_use: int
    peak: int
    utilisation: float
    mean_wait_ms: float


class WorkerOut(ApiModel):
    id: UUID
    hostname: str
    pid: int
    version: str
    executor: str
    active_trials: int
    last_heartbeat: datetime
    stale: bool
    lanes: dict[str, LaneOut] = Field(default_factory=dict)
    bottleneck: str | None = None


class DashboardOut(ApiModel):
    """Everything the landing page needs, in one round trip."""

    runs_this_week: int
    runs_delta_pct: float
    trials_executed: int
    completion_pct: float
    overall_pass_at_1: IntervalOut | None
    overall_pass_caret_k: IntervalOut | None
    reliability_gap: float | None
    spend_this_week: float
    spend_per_trial: float
    p95_latency_sec: float | None
    throttle_rate: float
    workers_active: int
    workers_total: int
    queue_depth: int
    active_trials: int
    suites: list[SuiteHealthOut] = Field(default_factory=list)
    recent_runs: list[RunOut] = Field(default_factory=list)


class SuiteHealthOut(ApiModel):
    slug: str
    name: str
    task_count: int
    best_pass_at_1: float | None
    margin: float | None
    sota_label: str | None
    trend: list[float] = Field(default_factory=list)
    delta: float | None = None
    status: Literal["improving", "stable", "regression", "unknown"] = "unknown"


class BudgetOut(ApiModel):
    spent_usd: float
    weekly_limit_usd: float | None
    period_start: datetime
    period_end: datetime
    timezone: str


class TrendPointOut(ApiModel):
    run_id: str
    at: datetime
    pass_at_k: IntervalOut
    pass_caret_k: IntervalOut
    reportable: bool


class TrendOut(ApiModel):
    suite: str | None = None
    agent: str | None = None
    model: str | None = None
    k: int | None = None
    points: list[TrendPointOut]


class AlertOut(ApiModel):
    id: str
    tone: str
    title: str
    detail: str
    href: str


class OverviewOut(ApiModel):
    active_run: RunOut | None
    recent_runs: list[RunOut]
    running_count: int
    queued_count: int
    budget: BudgetOut
    trend: TrendOut
    alerts: list[AlertOut]
    workers_active: int
    executor: str
    auth_required: bool
    instance_name: str
    scopes: list[str]
    generated_at: datetime


class ProvenanceTaskOut(ApiModel):
    task_id: str
    version_id: UUID
    semver: str
    content_hash: str
    image_digest: str | None


class ProvenanceOut(SeededApiModel):
    run_id: UUID
    seed: int
    harness_version: str | None
    agent_version: str | None
    model: str
    pricing_version: str | None
    tasks: list[ProvenanceTaskOut]


class SessionOut(ApiModel):
    name: str
    scopes: list[str]


class ProviderStateOut(ApiModel):
    name: str
    configured: bool


class ConsoleConfigOut(ApiModel):
    environment: str
    executor: str
    auth_required: bool
    google_sso_enabled: bool
    queue: str
    database: str
    artifact_backend: str
    providers: list[ProviderStateOut]
    provider_limits: dict[str, float]
    scopes: list[str]


class ApiKeyOut(ApiModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    key: str


class AuditEntryOut(ApiModel):
    id: int
    at: datetime
    actor: str
    action: str
    target: str | None


class AuditPageOut(ApiModel):
    items: list[AuditEntryOut]
    next_cursor: int | None
