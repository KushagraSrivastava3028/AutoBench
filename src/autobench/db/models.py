"""The warehouse schema (PRD §6.3).

Shape of the thing, top to bottom:

    task -> task_version -> suite_member -> suite -> run -> trial -> score
                                                            trial -> trial_event
                                                            trial -> artifact

``task_version`` is content-addressed: its ``content_hash`` is a digest of the
canonicalised task directory. Editing a task produces a new version rather than
mutating the old one, which is what makes a historical run's numbers mean
anything six months later.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from autobench.core.ids import new_uuid, utcnow
from autobench.db.types import AutoBigInt, JSONBType, Money, Reward, StringArray, UTCDateTime


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONBType, list[str]: StringArray}


class ConsoleSetting(Base):
    __tablename__ = "console_setting"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONBType)


class RunTransition(Base):
    __tablename__ = "run_transition"
    __table_args__ = (Index("ix_run_transition_run_id_id", "run_id", "id"),)
    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("run.id", ondelete="CASCADE"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# ---------------------------------------------------------------------------
# enumerations — stored as TEXT so a new value never needs a migration
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class TrialStatus(str, Enum):
    QUEUED = "queued"
    LEASED = "leased"
    SETUP = "setup"
    AGENT = "agent"
    VERIFY = "verify"
    SCORED = "scored"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {
            TrialStatus.SCORED,
            TrialStatus.ERROR,
            TrialStatus.TIMEOUT,
            TrialStatus.CANCELLED,
        }

    @property
    def in_flight(self) -> bool:
        return self in {
            TrialStatus.LEASED,
            TrialStatus.SETUP,
            TrialStatus.AGENT,
            TrialStatus.VERIFY,
        }


class EventKind(str, Enum):
    PROMPT = "prompt"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    MODEL_USAGE = "model_usage"
    STATUS = "status"
    ERROR = "error"
    VERDICT = "verdict"


class ArtifactKind(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"
    TRAJECTORY = "trajectory"
    DIFF = "diff"
    REPORT = "report"
    SETUP_LOG = "setup_log"
    VERIFIER_LOG = "verifier_log"


class ExecutionMode(str, Enum):
    HARNESS = "harness"
    IN_CONTAINER = "in_container"


# ---------------------------------------------------------------------------
# mixins
# ---------------------------------------------------------------------------


class _Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, server_default=func.now(), default=utcnow
    )


# ---------------------------------------------------------------------------
# catalogue
# ---------------------------------------------------------------------------


class Task(Base, _Timestamped):
    __tablename__ = "task"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    tags: Mapped[list[str]] = mapped_column(StringArray, nullable=False, default=list)
    author: Mapped[str | None] = mapped_column(String(128))

    versions: Mapped[list[TaskVersion]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskVersion.created_at.desc()",
    )


class TaskVersion(Base, _Timestamped):
    """One immutable snapshot of a task directory.

    ``oracle_reward`` must be exactly 1.000 and ``null_reward`` exactly 0.000
    before ``published`` may be set. A task where the null agent scores above
    zero has a leak; one where the oracle scores below one has a bug.
    """

    __tablename__ = "task_version"
    __table_args__ = (
        UniqueConstraint("task_id", "content_hash", name="uq_task_version_content"),
        Index("ix_task_version_published", "task_id", "published"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    task_id: Mapped[str] = mapped_column(
        ForeignKey("task.id", ondelete="CASCADE"), nullable=False, index=True
    )
    semver: Mapped[str] = mapped_column(String(32), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    image_digest: Mapped[str | None] = mapped_column(String(80))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    source_path: Mapped[str | None] = mapped_column(Text)

    oracle_reward: Mapped[float | None] = mapped_column(Reward)
    null_reward: Mapped[float | None] = mapped_column(Reward)
    validation_report: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    task: Mapped[Task] = relationship(back_populates="versions")

    @property
    def calibrated(self) -> bool:
        return self.oracle_reward == 1.0 and self.null_reward == 0.0


class Suite(Base, _Timestamped):
    __tablename__ = "suite"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    members: Mapped[list[SuiteMember]] = relationship(
        back_populates="suite", cascade="all, delete-orphan"
    )


class SuiteMember(Base):
    """A suite pins *versions*, never tasks.

    Pinning the task would let a suite's meaning drift under an author's edit,
    and every historical comparison against it would silently become invalid.
    """

    __tablename__ = "suite_member"
    __table_args__ = (UniqueConstraint("suite_id", "task_version_id", name="uq_suite_member"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    suite_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suite.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_version.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    weight: Mapped[float] = mapped_column(Reward, nullable=False, default=1.0)

    suite: Mapped[Suite] = relationship(back_populates="members")
    task_version: Mapped[TaskVersion] = relationship()


class Agent(Base, _Timestamped):
    """An agent definition: which adapter, which topology, which limits."""

    __tablename__ = "agent"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExecutionMode.HARNESS.value
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0.0")
    config: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    #: Mode B only. Empty means the egress proxy drops everything.
    egress_allowlist: Mapped[list[str]] = mapped_column(StringArray, nullable=False, default=list)


class Model(Base, _Timestamped):
    __tablename__ = "model"
    __table_args__ = (UniqueConstraint("provider", "model_id", name="uq_model_provider_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_id: Mapped[str] = mapped_column(String(160), nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    context_window: Mapped[int | None] = mapped_column(Integer)
    input_price_per_mtok: Mapped[float | None] = mapped_column(Money(10, 4))
    output_price_per_mtok: Mapped[float | None] = mapped_column(Money(10, 4))
    cached_price_per_mtok: Mapped[float | None] = mapped_column(Money(10, 4))
    pricing_version: Mapped[str | None] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


class Run(Base, _Timestamped):
    __tablename__ = "run"
    __table_args__ = (Index("ix_run_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    suite_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suite.id"), nullable=False, index=True)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent.id"), nullable=False, index=True)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model.id"), nullable=False, index=True)

    k: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    concurrency: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=8)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=RunStatus.QUEUED.value)
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    label: Mapped[str | None] = mapped_column(Text)

    trials_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trials_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Money(12, 4), nullable=False, default=0)
    #: Cancels the run when exceeded. ``None`` means no cap — deliberate, not default.
    budget_usd: Mapped[float | None] = mapped_column(Money(12, 4))

    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str | None] = mapped_column(String(128))

    suite: Mapped[Suite] = relationship()
    agent: Mapped[Agent] = relationship()
    model: Mapped[Model] = relationship()
    trials: Mapped[list[Trial]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def reportable(self) -> bool:
        """k=1 runs are indicative, not reportable. The UI says so out loud."""
        return self.k >= 3


class Trial(Base):
    __tablename__ = "trial"
    __table_args__ = (
        UniqueConstraint("run_id", "task_version_id", "attempt_index", name="uq_trial_attempt"),
        Index("ix_trial_run_status", "run_id", "status"),
        # Partial index: the reaper only ever scans leased rows.
        Index(
            "ix_trial_lease",
            "lease_expires_at",
            postgresql_where=text("status = 'leased'"),
        ),
        Index("ix_trial_queue_order", "run_id", "priority", "queued_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("task_version.id"), nullable=False, index=True
    )
    attempt_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TrialStatus.QUEUED.value, index=True
    )

    worker_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("worker.id", ondelete="SET NULL")
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    #: Infra retries only. Agent failures never increment this.
    attempt_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    #: LPT scheduling: higher runs first. See worker/planner.py.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_duration_ms: Mapped[int | None] = mapped_column(Integer)
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    container_id: Mapped[str | None] = mapped_column(String(80))
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(String(48), index=True)
    error_detail: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="trials")
    task_version: Mapped[TaskVersion] = relationship()
    score: Mapped[Score | None] = relationship(
        back_populates="trial", cascade="all, delete-orphan", uselist=False, passive_deletes=True
    )
    events: Mapped[list[TrialEvent]] = relationship(
        back_populates="trial", cascade="all, delete-orphan", passive_deletes=True
    )
    artifacts: Mapped[list[Artifact]] = relationship(
        back_populates="trial", cascade="all, delete-orphan", passive_deletes=True
    )


class Score(Base):
    __tablename__ = "score"

    trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trial.id", ondelete="CASCADE"), primary_key=True
    )
    reward: Mapped[float] = mapped_column(Reward, nullable=False)
    #: pass@k is computed on this, never on mean reward.
    binary_pass: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    items: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    verifier_exit: Mapped[int | None] = mapped_column(SmallInteger)

    tool_calls: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Money())

    setup_ms: Mapped[int | None] = mapped_column(Integer)
    agent_ms: Mapped[int | None] = mapped_column(Integer)
    verify_ms: Mapped[int | None] = mapped_column(Integer)

    #: Set when a rubric scorer contributed, so the UI can flag comparability.
    judge_model: Mapped[str | None] = mapped_column(String(160))
    judge_kappa: Mapped[float | None] = mapped_column(Reward)

    trial: Mapped[Trial] = relationship(back_populates="score")


class TrialEvent(Base):
    """The trajectory, one row per step. Partitioned monthly by ``ts`` on Postgres.

    This is the only table that grows without bound: after 30 days each trial's
    events roll into a single gzipped JSONL artifact in object storage and the
    partition is dropped. Full trajectories stay retrievable, one hop slower.
    """

    __tablename__ = "trial_event"
    __table_args__ = (
        UniqueConstraint("trial_id", "seq", name="uq_trial_event_seq"),
        Index("ix_trial_event_trial_seq", "trial_id", "seq"),
    )

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trial.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Already redacted at write time — see core.security.redact_payload.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer)
    cumulative_tokens: Mapped[int | None] = mapped_column(Integer)

    trial: Mapped[Trial] = relationship(back_populates="events")


class Artifact(Base):
    __tablename__ = "artifact"
    __table_args__ = (Index("ix_artifact_trial_kind", "trial_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    trial_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("trial.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Object-store key. Never the bytes — those belong nowhere near Postgres.
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="text/plain")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    trial: Mapped[Trial] = relationship(back_populates="artifacts")


class Worker(Base, _Timestamped):
    __tablename__ = "worker"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    executor: Mapped[str] = mapped_column(String(32), nullable=False)
    lanes: Mapped[dict[str, Any]] = mapped_column(JSONBType, nullable=False, default=dict)
    active_trials: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_heartbeat: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utcnow, index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# ---------------------------------------------------------------------------
# control plane
# ---------------------------------------------------------------------------


class WorkspaceUser(Base, _Timestamped):
    """A Google identity and its role in this deployment's single workspace."""

    __tablename__ = "workspace_user"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="viewer")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class WebSession(Base):
    __tablename__ = "web_session"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspace_user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OAuthFlow(Base):
    __tablename__ = "oauth_flow"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)


class ApiKey(Base, _Timestamped):
    __tablename__ = "api_key"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    #: Clear-text prefix so lookup is an index hit, not an Argon2 sweep.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(StringArray, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditLog(Base):
    """Every mutating call. Actor, action, target, timestamp, request hash."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_ts", "ts"),)

    id: Mapped[int] = mapped_column(AutoBigInt, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str | None] = mapped_column(Text)
    request_hash: Mapped[str | None] = mapped_column(String(80))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONBType)


__all__ = [
    "Agent",
    "ApiKey",
    "Artifact",
    "ArtifactKind",
    "AuditLog",
    "Base",
    "EventKind",
    "ExecutionMode",
    "Model",
    "Run",
    "RunStatus",
    "Score",
    "Suite",
    "SuiteMember",
    "Task",
    "TaskVersion",
    "Trial",
    "TrialEvent",
    "TrialStatus",
    "Worker",
]
