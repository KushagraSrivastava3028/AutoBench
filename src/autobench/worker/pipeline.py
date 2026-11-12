"""The nine stages of a trial (PRD §7.1).

    1 lease  2 image  3 create  4 setup  5 agent
    6 freeze+diff  7 verify  8 persist  9 teardown

**The retry rule, stated once and enforced here:** infrastructure failures are
retried and contribute nothing to the score. Agent failures — timeout, tool-call
cap, token cap, wrong answer — are never retried and always score 0.0.
Conflating the two is how benchmarks quietly inflate, so the distinction is
carried in :class:`ErrorClass` rather than in an if-statement someone can get
wrong later.

Teardown runs in a ``finally``. A trial that fails at stage 5 still removes its
container at stage 9, because the alternative is a disk that fills overnight and
a ``ContainerLeak`` alert nobody can act on.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from autobench.agents import build_agent, requires_model
from autobench.agents.base import AgentContext, AgentResult
from autobench.artifacts.store import ArtifactStore, get_store, trial_key
from autobench.artifacts.trajectory import TrajectoryWriter
from autobench.core.config import Settings, get_settings
from autobench.core.errors import ErrorClass, TrialCancelled, TrialError
from autobench.core.ids import hash_seed
from autobench.core.telemetry import span
from autobench.db.models import ArtifactKind, EventKind, ExecutionMode, TrialStatus
from autobench.executor import SandboxProvider, SandboxSpec, diff_snapshots, get_provider
from autobench.executor.base import DiffResult, Sandbox, Snapshot
from autobench.models.router import ModelRef, ModelRouter
from autobench.models.types import Usage
from autobench.scoring.report import ScoreReport
from autobench.scoring.verifier import verify
from autobench.taskspec.loader import TaskDirectory
from autobench.taskspec.schema import NetworkMode

logger = logging.getLogger(__name__)

SETUP_TIMEOUT = 120
SNAPSHOT_TIMEOUT = 60


@dataclass(slots=True)
class TrialRequest:
    """One unit of work. Everything needed to run a trial, and nothing more."""

    trial_id: str
    task: TaskDirectory
    agent_slug: str
    seed: int
    model: ModelRef | None = None
    agent_config: dict[str, Any] = field(default_factory=dict)
    network_override: NetworkMode | None = None
    run_id: str | None = None
    attempt_index: int = 0


@dataclass(slots=True)
class TrialOutcome:
    """The complete result. Populated even when the trial failed."""

    trial_id: str
    status: TrialStatus
    report: ScoreReport
    error_class: ErrorClass | None = None
    error_detail: str | None = None

    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    cost_usd: float = 0.0

    setup_ms: int = 0
    agent_ms: int = 0
    verify_ms: int = 0
    duration_ms: int = 0

    container_id: str | None = None
    image_digest: str | None = None
    diff: DiffResult | None = None
    trajectory: TrajectoryWriter | None = None
    artifacts: dict[str, str] = field(default_factory=dict)

    @property
    def retryable(self) -> bool:
        return self.error_class is not None and self.error_class.is_retryable


#: Called after every stage transition so the API can stream progress.
StatusCallback = Callable[[TrialStatus], Any]
#: Called for every trajectory event, so the UI updates before the trial ends.
EventCallback = Callable[[EventKind, dict[str, Any], int], Any]


class TrialPipeline:
    """Runs one trial, end to end, and cleans up after itself."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        provider: SandboxProvider | None = None,
        router: ModelRouter | None = None,
        store: ArtifactStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.provider = provider or get_provider(self.settings)
        self.router = router
        self.store = store or get_store(self.settings)

    async def run(
        self,
        request: TrialRequest,
        *,
        on_status: StatusCallback | None = None,
        on_event: EventCallback | None = None,
        should_cancel: Callable[[], bool] = lambda: False,
        renew_lease: Callable[[], Any] | None = None,
    ) -> TrialOutcome:
        started = time.perf_counter()
        trajectory = TrajectoryWriter(trial_id=request.trial_id)
        outcome = TrialOutcome(
            trial_id=request.trial_id,
            status=TrialStatus.SCORED,
            report=ScoreReport(),
            trajectory=trajectory,
        )

        sandbox: Sandbox | None = None
        baseline: Snapshot | None = None

        async def emit(kind: EventKind, payload: dict[str, Any]) -> None:
            event = trajectory.append(kind, payload)
            if event is not None and on_event is not None:
                await _maybe_await(on_event(kind, event.payload, event.seq))
            if renew_lease is not None:
                # The lease is renewed from inside the agent loop, so a worker
                # that is merely slow (a long model call) keeps its trial, while
                # one that is SIGKILLed loses it within the visibility window.
                await _maybe_await(renew_lease())

        try:
            with span(
                "autobench.trial",
                **{
                    "trial.id": request.trial_id,
                    "task.id": request.task.id,
                    "run.id": request.run_id or "",
                    "attempt.index": request.attempt_index,
                },
            ):
                # --- 2. image -------------------------------------------------
                await _notify(on_status, TrialStatus.SETUP)
                setup_started = time.perf_counter()
                with span("autobench.setup", **{"task.id": request.task.id}):
                    image = await self._resolve_image(request.task)
                    outcome.image_digest = image

                    # --- 3. create --------------------------------------------
                    sandbox = await self._create_sandbox(request, image)
                    outcome.container_id = sandbox.id

                    # --- 4. setup ---------------------------------------------
                    await self._run_setup(request, sandbox, emit)
                    baseline = await sandbox.snapshot()
                outcome.setup_ms = int((time.perf_counter() - setup_started) * 1000)

                if should_cancel():
                    raise TrialCancelled()

                # --- 5. agent -------------------------------------------------
                await _notify(on_status, TrialStatus.AGENT)
                agent_started = time.perf_counter()
                agent_result = await self._run_agent(request, sandbox, emit, should_cancel)
                outcome.agent_ms = int((time.perf_counter() - agent_started) * 1000)
                outcome.tool_calls = agent_result.tool_calls
                outcome.usage = agent_result.usage
                outcome.cost_usd = agent_result.cost_usd

                # --- 6. freeze + diff ----------------------------------------
                with span("autobench.freeze"):
                    await sandbox.freeze()
                    final = await sandbox.snapshot()
                    outcome.diff = diff_snapshots(
                        baseline, final, max_bytes=self.settings.sandbox.max_diff_bytes
                    )

                # --- 7. verify ------------------------------------------------
                await _notify(on_status, TrialStatus.VERIFY)
                with span("autobench.verify", **{"task.id": request.task.id}):
                    verification = await verify(self.provider, request.task, sandbox)
                outcome.report = verification.report
                outcome.verify_ms = verification.duration_ms

                await emit(
                    EventKind.VERDICT,
                    {
                        "reward": verification.report.reward,
                        "binary_pass": verification.report.binary_pass,
                        "items": [i.model_dump() for i in verification.report.items],
                        "verifier_exit": verification.exit_code,
                    },
                )
                outcome.status = TrialStatus.SCORED

        except TrialCancelled as exc:
            outcome.status = TrialStatus.CANCELLED
            outcome.error_class = exc.error_class
            outcome.error_detail = exc.detail
            outcome.report = ScoreReport.from_failure("cancelled by operator")

        except TrialError as exc:
            outcome.error_class = exc.error_class
            outcome.error_detail = exc.detail
            await emit(
                EventKind.ERROR,
                {"error_class": exc.error_class.value, "detail": exc.detail, "stage": exc.stage},
            )
            if exc.error_class.counts_as_failure:
                # An agent failure IS the result: reward 0.0, and no retry.
                outcome.status = (
                    TrialStatus.TIMEOUT
                    if exc.error_class is ErrorClass.AGENT_TIMEOUT
                    else TrialStatus.SCORED
                )
                outcome.report = ScoreReport.from_failure(exc.detail)
            else:
                # Infrastructure: the trial produced no measurement at all.
                outcome.status = TrialStatus.ERROR
                outcome.report = ScoreReport.from_failure(exc.detail)

        except Exception as exc:  # pragma: no cover - defence in depth
            logger.exception("unhandled error in trial %s", request.trial_id)
            outcome.status = TrialStatus.ERROR
            outcome.error_class = ErrorClass.INFRA_DOCKER
            outcome.error_detail = f"{type(exc).__name__}: {exc}"
            outcome.report = ScoreReport.from_failure(str(exc))

        finally:
            # --- 9. teardown, always ------------------------------------------
            if sandbox is not None:
                try:
                    await sandbox.destroy()
                except Exception as exc:  # pragma: no cover
                    logger.warning(
                        "teardown of %s failed; the reaper will collect it: %s",
                        sandbox.id[:12],
                        exc,
                    )

        # --- 8. persist ---------------------------------------------------
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            outcome.artifacts = await self._persist(request, outcome)
        except Exception as exc:
            logger.warning("artifact persistence failed for %s: %s", request.trial_id, exc)

        return outcome

    # --- stages ------------------------------------------------------------

    async def _resolve_image(self, task: TaskDirectory) -> str:
        try:
            return await self.provider.ensure_image(task)
        except TrialError:
            raise
        except Exception as exc:
            raise TrialError(
                f"could not resolve an image for {task.id}: {exc}",
                ErrorClass.INFRA_IMAGE,
                stage="image",
            ) from exc

    async def _create_sandbox(self, request: TrialRequest, image: str) -> Sandbox:
        task = request.task
        spec = SandboxSpec.from_task(
            task,
            image,
            network=request.network_override,
            extra_env={
                "AUTOBENCH_TRIAL_ID": request.trial_id,
                "AUTOBENCH_SEED": str(request.seed),
                "PYTHONHASHSEED": str(hash_seed(request.seed)),
            },
            labels={
                "autobench.trial": request.trial_id,
                "autobench.run": request.run_id or "adhoc",
                # The local executor seeds its workspace from here; Docker bakes
                # the same files in at build time via COPY.
                "autobench.seed_dir": str(task.root / "environment" / "seed"),
            },
        )
        agent = build_agent(request.agent_slug, request.agent_config)
        if agent.mode is ExecutionMode.IN_CONTAINER:
            # Mode B: the container needs a route to the model, but only to the
            # proxy, and only to allowlisted FQDNs beyond it.
            spec.network = NetworkMode.PROXY
            spec.egress_allowlist = list(agent.egress_allowlist)
        return await self.provider.create(spec)

    async def _run_setup(self, request: TrialRequest, sandbox: Sandbox, emit: Any) -> None:
        setup_script = request.task.root / "environment" / "setup.sh"
        if setup_script.is_file():
            target = "/tmp/autobench-setup.sh"  # noqa: S108 - inside the sandbox
            await sandbox.write_file(target, setup_script.read_text(encoding="utf-8"), mode=0o755)
            result = await sandbox.exec(
                'bash "$AUTOBENCH_SETUP_SCRIPT"',
                timeout=SETUP_TIMEOUT,
                env={"AUTOBENCH_SETUP_SCRIPT": target},
            )
            if not result.ok:
                raise TrialError(
                    f"setup.sh exited {result.exit_code}:\n{result.combined(2000)}",
                    ErrorClass.INFRA_SETUP,
                    stage="setup",
                )
            await emit(EventKind.STATUS, {"message": "setup complete", "stage": "setup"})

        agent = build_agent(request.agent_slug, request.agent_config)
        context = self._context(request, sandbox, emit, lambda: False)
        await agent.setup(context)

    async def _run_agent(
        self,
        request: TrialRequest,
        sandbox: Sandbox,
        emit: Any,
        should_cancel: Callable[[], bool],
    ) -> AgentResult:
        agent = build_agent(request.agent_slug, request.agent_config)
        if requires_model(request.agent_slug) and request.model is None:
            raise TrialError(
                f"agent {request.agent_slug!r} needs a model; none was supplied",
                ErrorClass.INFRA_SETUP,
                stage="agent",
            )
        context = self._context(request, sandbox, emit, should_cancel)
        return await agent.run(context)

    def _context(
        self,
        request: TrialRequest,
        sandbox: Sandbox,
        emit: Any,
        should_cancel: Callable[[], bool],
    ) -> AgentContext:
        return AgentContext(
            task=request.task,
            sandbox=sandbox,
            instruction=request.task.instruction,
            seed=request.seed,
            trial_id=request.trial_id,
            router=self.router,
            model=request.model,
            emit=emit,
            should_cancel=should_cancel,
        )

    async def _persist(self, request: TrialRequest, outcome: TrialOutcome) -> dict[str, str]:
        """Write the artifacts. Failure here never changes the score."""
        keys: dict[str, str] = {}

        if outcome.trajectory is not None and outcome.trajectory.events:
            stored = await self.store.put(
                trial_key(request.trial_id, "trajectory.jsonl"),
                outcome.trajectory.to_jsonl(),
                content_type="application/x-ndjson",
            )
            keys[ArtifactKind.TRAJECTORY.value] = stored.key

        if outcome.diff is not None and outcome.diff.files:
            import json

            stored = await self.store.put(
                trial_key(request.trial_id, "diff.json"),
                json.dumps(outcome.diff.to_json(), indent=2),
                content_type="application/json",
            )
            keys[ArtifactKind.DIFF.value] = stored.key

        import json as _json

        stored = await self.store.put(
            trial_key(request.trial_id, "report.json"),
            _json.dumps(outcome.report.to_json(), indent=2),
            content_type="application/json",
        )
        keys[ArtifactKind.REPORT.value] = stored.key
        return keys


async def _notify(callback: StatusCallback | None, status: TrialStatus) -> None:
    if callback is not None:
        await _maybe_await(callback(status))


async def _maybe_await(value: Any) -> Any:
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# calibration runner — the validator's dynamic gates plug in here
# ---------------------------------------------------------------------------


class PipelineCalibrationRunner:
    """Adapts :class:`TrialPipeline` to the validator's ``CalibrationRunner``.

    Kept separate so the validator keeps its "no import dependency on the
    execution plane" property: the static gates still run in a CI job that has
    no Docker daemon and no worker.
    """

    def __init__(self, pipeline: TrialPipeline | None = None) -> None:
        self.pipeline = pipeline or TrialPipeline()

    async def __call__(
        self,
        task: TaskDirectory,
        *,
        agent: str,
        seed: int,
        network: NetworkMode | None = None,
    ) -> ScoreReport:
        import uuid

        outcome = await self.pipeline.run(
            TrialRequest(
                trial_id=str(uuid.uuid4()),
                task=task,
                agent_slug=agent,
                seed=seed,
                network_override=network,
            )
        )
        if outcome.error_class is not None and outcome.error_class.is_infra:
            raise TrialError(
                outcome.error_detail or "infrastructure failure during calibration",
                outcome.error_class,
            )
        return outcome.report
