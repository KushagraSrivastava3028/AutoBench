"""Typed exceptions that render as RFC 9457 ``application/problem+json``.

Two rules encoded here, and they are the same rule seen from two sides:

1.  Every error carries an :class:`ErrorClass`. The class decides whether the
    trial is retried and whether it counts against the score.
2.  Infrastructure failures are retried and contribute nothing to the score.
    Agent failures are never retried and always score 0.0. Conflating the two
    is how benchmarks quietly inflate.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorClass(str, Enum):
    """``trial.error_class``. The ``infra_`` prefix is load-bearing.

    Anything starting with ``infra_`` is a harness problem: retry it, exclude it
    from the score, and surface it as a separate counter in the UI. Everything
    else is the agent's result and is final.
    """

    # --- infrastructure: retryable, never scored ---------------------------
    INFRA_IMAGE = "infra_image"
    INFRA_DOCKER = "infra_docker"
    INFRA_SETUP = "infra_setup"
    INFRA_SNAPSHOT = "infra_snapshot"
    INFRA_PERSIST = "infra_persist"
    INFRA_PROVIDER = "infra_provider"
    INFRA_ABANDONED = "infra_abandoned"
    VERIFIER_CRASH = "verifier_crash"

    # --- agent outcomes: final, scored 0.0 ---------------------------------
    AGENT_TIMEOUT = "agent_timeout"
    AGENT_TOOL_CAP = "agent_tool_cap"
    AGENT_TOKEN_CAP = "agent_token_cap"
    AGENT_ERROR = "agent_error"
    AGENT_REFUSED = "agent_refused"

    # --- operator actions ---------------------------------------------------
    CANCELLED = "cancelled"

    @property
    def is_infra(self) -> bool:
        return self.value.startswith("infra_") or self is ErrorClass.VERIFIER_CRASH

    @property
    def is_retryable(self) -> bool:
        return self.is_infra and self is not ErrorClass.INFRA_ABANDONED

    @property
    def counts_as_failure(self) -> bool:
        """True when the trial should be scored 0.0 rather than re-queued."""
        return not self.is_infra and self is not ErrorClass.CANCELLED


class AutobenchError(Exception):
    """Base for everything Autobench raises on purpose."""

    status_code: int = 500
    problem_type: str = "about:blank"
    title: str = "Internal error"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": self.problem_type,
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
        }
        if instance:
            problem["instance"] = instance
        problem.update(self.extra)
        return problem


# --- control plane ---------------------------------------------------------


class NotFoundError(AutobenchError):
    status_code = 404
    problem_type = "https://autobench.dev/problems/not-found"
    title = "Resource not found"


class ConflictError(AutobenchError):
    status_code = 409
    problem_type = "https://autobench.dev/problems/conflict"
    title = "Conflicting state"


class ValidationFailure(AutobenchError):
    status_code = 422
    problem_type = "https://autobench.dev/problems/validation-failed"
    title = "Validation failed"


class AuthError(AutobenchError):
    status_code = 401
    problem_type = "https://autobench.dev/problems/unauthenticated"
    title = "Authentication required"


class ForbiddenError(AutobenchError):
    status_code = 403
    problem_type = "https://autobench.dev/problems/forbidden"
    title = "Insufficient scope"


class RateLimited(AutobenchError):
    status_code = 429
    problem_type = "https://autobench.dev/problems/rate-limited"
    title = "Rate limited"


# --- task specification ----------------------------------------------------


class TaskSpecError(ValidationFailure):
    problem_type = "https://autobench.dev/problems/task-spec"
    title = "Invalid task specification"


class SecurityPolicyError(ValidationFailure):
    """A task asked for something §12.1 prohibits. Never overridable by config."""

    problem_type = "https://autobench.dev/problems/security-policy"
    title = "Task violates the sandbox security policy"


# --- execution -------------------------------------------------------------


class TrialError(AutobenchError):
    """Raised inside the pipeline; carries the classification that decides retry."""

    def __init__(
        self,
        detail: str,
        error_class: ErrorClass,
        *,
        stage: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail, **extra)
        self.error_class = error_class
        self.stage = stage

    @property
    def retryable(self) -> bool:
        return self.error_class.is_retryable

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"TrialError({self.error_class.value}, stage={self.stage!r}, {self.detail!r})"


class SandboxError(TrialError):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail, ErrorClass.INFRA_DOCKER, **extra)


class AgentTimeout(TrialError):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail, ErrorClass.AGENT_TIMEOUT, stage="agent", **extra)


class ToolCapExceeded(TrialError):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail, ErrorClass.AGENT_TOOL_CAP, stage="agent", **extra)


class TokenCapExceeded(TrialError):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail, ErrorClass.AGENT_TOKEN_CAP, stage="agent", **extra)


class ProviderError(TrialError):
    def __init__(self, detail: str, *, status_code: int | None = None, **extra: Any) -> None:
        super().__init__(detail, ErrorClass.INFRA_PROVIDER, stage="agent", **extra)
        self.provider_status = status_code


class VerifierError(TrialError):
    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail, ErrorClass.VERIFIER_CRASH, stage="verify", **extra)


class TrialCancelled(TrialError):
    def __init__(self, detail: str = "cancelled by operator", **extra: Any) -> None:
        super().__init__(detail, ErrorClass.CANCELLED, **extra)
