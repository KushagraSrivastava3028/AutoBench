"""``task.toml`` — the typed contract (PRD §5.2).

Everything here is validated at parse time, not at run time. A task that would
violate the sandbox policy, declare an impossible timeout, or weight its scoring
items to something other than 1.0 is rejected before it ever reaches a worker.

The security prohibitions in :class:`EnvironmentSpec` are the ones §12.1 calls
non-negotiable. They are fields of the schema rather than config keys precisely
so that there is no place to turn them off.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0"

_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")
_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9\-]{1,62}[a-z0-9]$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{12,64}$")
_SIZE = re.compile(r"^\d+(?:\.\d+)?\s*[kKmMgG]?[bB]?$")

#: Rejected at publish time, whatever the justification. See PRD §12.1.
FORBIDDEN_CAPABILITIES = frozenset(
    {"SYS_ADMIN", "SYS_PTRACE", "NET_ADMIN", "SYS_MODULE", "SYS_RAWIO", "ALL"}
)
FORBIDDEN_MOUNT_SUBSTRINGS = ("/var/run/docker.sock", "/run/docker.sock", "docker.sock")


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


class NetworkMode(str, Enum):
    NONE = "none"
    PROXY = "proxy"
    #: Permitted only for local authoring. The validator refuses to publish it.
    FULL = "full"


class ScoringMode(str, Enum):
    BINARY = "binary"
    WEIGHTED = "weighted"
    RUBRIC = "rubric"


SizeString = Annotated[str, Field(pattern=_SIZE.pattern)]


class _Strict(BaseModel):
    """Unknown keys are errors, not silently-ignored typos.

    ``[verifer]`` instead of ``[verifier]`` would otherwise mean a task quietly
    ran with default timeouts and a default image. That is the kind of thing
    that turns into "the model got worse this week".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskMeta(_Strict):
    id: str
    version: str = "1.0.0"
    title: str
    category: str = "general"
    difficulty: Difficulty = Difficulty.MEDIUM
    tags: list[str] = Field(default_factory=list)
    author: str | None = None
    #: Forbids LLM-judge as the sole scorer. See PRD §9.4.
    deterministic: bool = True

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not _TASK_ID.match(v):
            raise ValueError(
                f"task id {v!r} must be kebab-case, 3-64 chars, [a-z0-9-] only "
                "(it becomes an image tag and a URL path segment)"
            )
        return v

    @field_validator("version")
    @classmethod
    def _check_semver(cls, v: str) -> str:
        if not _SEMVER.match(v):
            raise ValueError(f"version {v!r} is not semver (e.g. 1.3.0)")
        return v


class EnvironmentSpec(_Strict):
    """The world the agent wakes up in."""

    dockerfile: str = "environment/Dockerfile"
    compose: str | None = None
    workdir: str = "/app"
    network: NetworkMode = NetworkMode.NONE
    cpus: float = Field(default=2.0, gt=0, le=64)
    memory: SizeString = "4g"
    pids_limit: int = Field(default=512, ge=16, le=16_384)
    tmpfs_size: SizeString = "512m"
    #: ``/app`` stays writable regardless; this covers everything else.
    readonly_root: bool = False
    #: Extra env for the agent's container. Never secrets — see PRD §12.3.
    env: dict[str, str] = Field(default_factory=dict)
    #: Reviewed by hand, and never one of FORBIDDEN_CAPABILITIES.
    cap_add: list[str] = Field(default_factory=list)
    #: Host paths the task wants mounted. Only its own scratch dir is legal.
    mounts: list[str] = Field(default_factory=list)
    privileged: Literal[False] = False

    @field_validator("cap_add")
    @classmethod
    def _check_caps(cls, v: list[str]) -> list[str]:
        for cap in v:
            name = cap.upper().removeprefix("CAP_")
            if name in FORBIDDEN_CAPABILITIES:
                raise ValueError(
                    f"capability CAP_{name} is prohibited: it is a container-escape "
                    "primitive and no task needs it"
                )
        return v

    @field_validator("mounts")
    @classmethod
    def _check_mounts(cls, v: list[str]) -> list[str]:
        for mount in v:
            lowered = mount.lower()
            if any(bad in lowered for bad in FORBIDDEN_MOUNT_SUBSTRINGS):
                raise ValueError(
                    f"mount {mount!r} exposes the Docker socket — that is a root "
                    "shell on the host, not a sandbox"
                )
            if mount.startswith("/") and not mount.startswith("/app"):
                raise ValueError(f"host bind mount {mount!r} is outside the task scratch directory")
        return v

    @field_validator("env")
    @classmethod
    def _check_env(cls, v: dict[str, str]) -> dict[str, str]:
        for key in v:
            if re.search(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", key.upper()):
                raise ValueError(
                    f"environment key {key!r} looks like a credential; provider keys "
                    "are injected into the egress proxy, never into the task container"
                )
        return v


class AgentLimits(_Strict):
    """Caps that define what "the agent failed" means.

    Exceeding any of these is an agent failure: scored 0.0, never retried.
    """

    timeout_sec: int = Field(default=900, ge=10, le=14_400)
    max_tool_calls: int = Field(default=120, ge=1, le=10_000)
    max_tokens: int = Field(default=400_000, ge=1_000)
    #: Per-model-call ceiling, distinct from the trial-wide max_tokens.
    max_output_tokens: int = Field(default=8_192, ge=64)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class VerifierSpec(_Strict):
    """A separate, clean container. Tests are mounted read-only, after the freeze."""

    image: str = "autobench/verifier-python:3.12"
    timeout_sec: int = Field(default=180, ge=5, le=3_600)
    tests_mount: str = "/opt/tests"
    #: Referenced through $AUTOBENCH_TESTS_DIR rather than a literal path so
    #: the same task runs under both executors. The variable always resolves to
    #: `tests_mount` inside a container.
    command: str = 'bash "$AUTOBENCH_TESTS_DIR/run-tests.sh"'
    network: NetworkMode = NetworkMode.NONE
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("network")
    @classmethod
    def _no_network(cls, v: NetworkMode) -> NetworkMode:
        if v is not NetworkMode.NONE:
            raise ValueError(
                "the verifier must run with network='none'. A verifier that can "
                "reach the internet can be a verifier that fetches its own answer."
            )
        return v


class ScoringItem(_Strict):
    id: str
    weight: float = Field(default=1.0, ge=0.0, le=1.0)
    description: str | None = None


class ScoringSpec(_Strict):
    mode: ScoringMode = ScoringMode.BINARY
    report_path: str = "/tmp/autobench-report.json"  # noqa: S108 - inside the sandbox
    items: list[ScoringItem] = Field(default_factory=list)
    #: reward >= this is a binary pass. 1.0 means "everything, exactly".
    pass_threshold: float = Field(default=1.0, ge=0.0, le=1.0)
    #: Rubric mode only — the pinned judge. A judge change breaks comparability.
    judge_model: str | None = None
    rubric: str | None = None

    @model_validator(mode="after")
    def _check_weights(self) -> ScoringSpec:
        if self.mode is ScoringMode.WEIGHTED:
            if not self.items:
                raise ValueError("scoring.mode='weighted' requires [[scoring.items]]")
            total = sum(item.weight for item in self.items)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(
                    f"scoring item weights sum to {total:.4f}, not 1.0 — a reward "
                    "that cannot reach 1.0 makes the oracle gate unsatisfiable"
                )
            ids = [item.id for item in self.items]
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate scoring item ids")
        if self.mode is ScoringMode.RUBRIC and not self.judge_model:
            raise ValueError("scoring.mode='rubric' requires a pinned scoring.judge_model")
        return self


class DeterminismSpec(_Strict):
    """Controls 2, 5, 6 and 8 from PRD §10, declared per task."""

    seed: int = 1337
    #: ``FROM python:3.12-slim@sha256:...`` — enforced by the validator.
    pinned_base_digest: str | None = None
    #: libfaketime target, so a date-dependent test cannot rot.
    freeze_time: str | None = None
    forbid_network_in_tests: bool = True
    #: Extra env the setup phase exports, on top of PYTHONHASHSEED etc.
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("pinned_base_digest")
    @classmethod
    def _check_digest(cls, v: str | None) -> str | None:
        if v is not None and not _DIGEST.match(v):
            raise ValueError(f"pinned_base_digest {v!r} must look like 'sha256:<hex>'")
        return v


class TaskSpec(_Strict):
    """A fully parsed ``task.toml``."""

    schema_version: str = SCHEMA_VERSION
    task: TaskMeta
    environment: EnvironmentSpec = Field(default_factory=EnvironmentSpec)
    agent: AgentLimits = Field(default_factory=AgentLimits)
    verifier: VerifierSpec = Field(default_factory=VerifierSpec)
    scoring: ScoringSpec = Field(default_factory=ScoringSpec)
    determinism: DeterminismSpec = Field(default_factory=DeterminismSpec)

    @field_validator("schema_version")
    @classmethod
    def _check_schema(cls, v: str) -> str:
        major = v.split(".", 1)[0]
        if major != SCHEMA_VERSION.split(".", 1)[0]:
            raise ValueError(
                f"schema_version {v!r} is incompatible with this Autobench "
                f"(expected {SCHEMA_VERSION[0]}.x)"
            )
        return v

    @model_validator(mode="after")
    def _cross_field_checks(self) -> TaskSpec:
        if self.task.deterministic and self.scoring.mode is ScoringMode.RUBRIC:
            raise ValueError(
                "task.deterministic=true forbids scoring.mode='rubric'. An LLM judge "
                "is never the sole scorer for a task that claims to be deterministic."
            )
        return self

    # --- convenience -------------------------------------------------------

    @property
    def id(self) -> str:
        return self.task.id

    @property
    def uses_compose(self) -> bool:
        return self.environment.compose is not None

    def to_json(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
