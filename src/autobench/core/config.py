"""12-factor settings.

Everything is overridable by environment variable with the ``AUTOBENCH_`` prefix.
Nested settings use ``__`` as the delimiter, e.g. ``AUTOBENCH_WORKER__MAX_CONTAINERS=12``.

Two settings decide how much infrastructure you need to run Autobench:

``executor``
    ``docker`` is the real thing. ``local`` runs each trial in a throwaway host
    directory with a subprocess shell — no isolation guarantees, but it lets the
    whole control plane, scheduler, scoring stack and UI run on a laptop that has
    no Docker daemon. ``local`` is refused when ``environment != "development"``.

``queue``
    ``redis`` is the real thing. ``memory`` runs an in-process queue so
    ``autobench run`` works as a single self-contained command.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ExecutorKind = Literal["docker", "local"]
QueueKind = Literal["redis", "memory"]
Environment = Literal["development", "staging", "production"]


def _default_state_dir() -> Path:
    return Path(os.environ.get("AUTOBENCH_STATE_DIR", Path.cwd() / ".autobench"))


class WorkerSettings(BaseSettings):
    """The three-tier concurrency budget (PRD §8.2).

    One global concurrency number is always wrong: container work is CPU/disk
    bound, model calls are network bound, and the Docker daemon serialises
    creates. Each bottleneck gets its own lane.
    """

    model_config = SettingsConfigDict(env_prefix="AUTOBENCH_WORKER__", extra="ignore")

    id: str | None = None
    #: Containers alive at once on this worker. Rule of thumb: cores / task.cpus - 1.
    max_containers: int = Field(default=8, ge=1, le=256)
    #: The Docker daemon serialises container creates badly past ~4 concurrent.
    max_docker_ops: int = Field(default=4, ge=1, le=32)
    #: Verifiers are short and CPU-hungry; their own lane stops a verify burst
    #: from starving agent tool execution.
    max_verifiers: int = Field(default=4, ge=1, le=64)
    #: Trials leased per poll.
    lease_batch: int = Field(default=4, ge=1, le=64)
    #: Lease visibility window as a multiple of the task's agent timeout.
    lease_multiplier: float = Field(default=2.0, ge=1.1, le=10.0)
    heartbeat_interval_sec: float = Field(default=15.0, ge=1.0)
    poll_interval_sec: float = Field(default=1.0, ge=0.05)
    #: Hard ceiling on infra retries before a trial is marked infra_abandoned.
    max_infra_attempts: int = Field(default=3, ge=1, le=10)


class ProviderLimits(BaseSettings):
    """Shared, distributed provider budget. Enforced in Redis, not per-process."""

    model_config = SettingsConfigDict(env_prefix="AUTOBENCH_PROVIDER__", extra="ignore")

    requests_per_minute: int = Field(default=1_000, ge=1)
    tokens_per_minute: int = Field(default=2_000_000, ge=1)
    max_concurrent: int = Field(default=32, ge=1)
    #: Retries on 429/5xx before the trial is failed as infra_provider.
    max_retries: int = Field(default=5, ge=0, le=12)
    backoff_base_sec: float = Field(default=1.0, ge=0.1)
    backoff_max_sec: float = Field(default=60.0, ge=1.0)


class SandboxSettings(BaseSettings):
    """Defaults for every sandbox. Overridable per task, within limits."""

    model_config = SettingsConfigDict(env_prefix="AUTOBENCH_SANDBOX__", extra="ignore")

    default_cpus: float = Field(default=2.0, gt=0)
    default_memory: str = "4g"
    default_pids_limit: int = Field(default=512, ge=32)
    default_tmpfs_size: str = "512m"
    seccomp_profile: str = "profiles/autobench-default.json"
    runtime: str | None = None
    require_network_none: bool = False
    #: Wall-clock ceiling no task may exceed, whatever its task.toml says.
    max_agent_timeout_sec: int = Field(default=3_600, ge=30)
    max_verifier_timeout_sec: int = Field(default=900, ge=10)
    #: Cap on the captured filesystem diff. Beyond this we store a truncation note.
    max_diff_bytes: int = Field(default=16 * 1024 * 1024, ge=1024)
    verifier_image_default: str = "autobench/verifier-python:3.12"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUTOBENCH_",
        env_nested_delimiter="__",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "development"
    debug: bool = False
    service_name: str = "autobench"

    # --- state layer -------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./.autobench/autobench.db"
    database_pool_size: int = Field(default=10, ge=1)
    database_max_overflow: int = Field(default=20, ge=0)
    database_echo: bool = False

    redis_url: str = "redis://localhost:6379/0"
    queue: QueueKind = "memory"
    queue_name: str = "autobench:trials"

    # --- object store ------------------------------------------------------
    #: "s3" talks to MinIO/S3; "local" writes under state_dir/artifacts.
    artifact_backend: Literal["s3", "local"] = "local"
    s3_endpoint_url: str | None = None
    s3_bucket: str = "autobench"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_create_bucket: bool = False
    s3_region: str = "us-east-1"
    presign_ttl_sec: int = Field(default=900, ge=60, le=86_400)

    # --- execution ---------------------------------------------------------
    executor: ExecutorKind = "local"
    docker_host: str | None = None
    image_namespace: str = "autobench"
    #: Where task directories are read from and where the local executor works.
    tasks_dir: Path = Field(default_factory=lambda: Path.cwd() / "tasks")
    state_dir: Path = Field(default_factory=_default_state_dir)

    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    provider: ProviderLimits = Field(default_factory=ProviderLimits)
    sandbox: SandboxSettings = Field(default_factory=SandboxSettings)

    # --- control plane -----------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - bound inside a container by design
    api_port: int = 8000
    api_base_url: str = "http://localhost:8000"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    #: When unset, auth is disabled — acceptable only in development.
    bootstrap_api_key: str | None = None
    auth_required: bool = False
    public_url: str = "http://localhost:3000"
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_workspace_domains: list[str] = Field(default_factory=list)
    google_bootstrap_admins: list[str] = Field(default_factory=list)
    session_ttl_sec: int = Field(default=28_800, ge=300, le=86_400)

    @property
    def google_sso_enabled(self) -> bool:
        return bool(
            self.google_oauth_client_id
            and self.google_oauth_client_secret
            and self.google_workspace_domains
        )

    # --- model providers ---------------------------------------------------
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    openai_compat_base_url: str | None = None
    openai_compat_api_key: str | None = None

    # --- observability -----------------------------------------------------
    otlp_endpoint: str | None = None
    #: Prompt/completion text in span events. Off by default — see PRD §13.1.
    capture_content: bool = False
    capture_content_chars: int = Field(default=1_000, ge=64, le=32_000)
    metrics_enabled: bool = True
    log_level: str = "INFO"

    # --- reporting ---------------------------------------------------------
    bootstrap_resamples: int = Field(default=10_000, ge=200, le=200_000)
    confidence_level: float = Field(default=0.95, gt=0.5, lt=1.0)
    #: Runs below this k are labelled "indicative, not reportable" in the UI.
    reportable_min_k: int = Field(default=3, ge=1)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [part.strip() for part in v.split(",") if part.strip()]
        return v

    @field_validator("tasks_dir", "state_dir", mode="before")
    @classmethod
    def _expand(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(v).expanduser()
        return v

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if bool(self.s3_access_key) != bool(self.s3_secret_key):
            raise ValueError("S3 access and secret keys must both be provided or both omitted.")
        if self.google_sso_enabled:
            from urllib.parse import urlsplit

            public = urlsplit(self.public_url)
            if public.query or public.fragment or public.path not in ("", "/") or not public.netloc:
                raise ValueError("public_url must be an origin without a path, query or fragment.")
            if self.environment != "development" and public.scheme != "https":
                raise ValueError("Google SSO requires an HTTPS public_url outside development.")
        if self.environment == "production":
            if self.executor == "local":
                raise ValueError(
                    "executor='local' provides no isolation and is refused outside "
                    "development. Set AUTOBENCH_EXECUTOR=docker."
                )
            if self.queue == "memory":
                raise ValueError(
                    "queue='memory' loses trials on restart. Set AUTOBENCH_QUEUE=redis."
                )
            if not self.auth_required:
                raise ValueError("auth_required must be true in production.")
        return self

    # --- derived -----------------------------------------------------------

    @property
    def artifacts_dir(self) -> Path:
        return self.state_dir / "artifacts"

    @property
    def workspaces_dir(self) -> Path:
        return self.state_dir / "workspaces"

    @property
    def image_cache_dir(self) -> Path:
        return self.state_dir / "images"

    def ensure_dirs(self) -> None:
        for path in (self.state_dir, self.artifacts_dir, self.workspaces_dir, self.image_cache_dir):
            path.mkdir(parents=True, exist_ok=True)

    def provider_key(self, provider: str) -> str | None:
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "google": self.google_api_key,
            "openai_compat": self.openai_compat_api_key,
        }.get(provider)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """Test hook — settings are cached for the process lifetime otherwise."""
    get_settings.cache_clear()
