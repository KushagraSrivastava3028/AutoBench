"""The sandbox interface.

Everything above this line — the planner, the agent loop, the scorer, the API —
talks to :class:`SandboxProvider` and never to Docker. That is what makes
Kubernetes a v2 swap rather than a v1 rewrite, and it is what lets the whole
control plane run on a laptop with no daemon.

Two implementations ship in v1:

``DockerProvider``
    The real one. Digest-pinned images, all capabilities dropped, no network
    during the agent phase, resource caps enforced by the kernel.

``LocalProvider``
    A throwaway host directory and a subprocess. **No isolation guarantees.**
    It exists so the control plane, scheduler, statistics and UI are runnable
    and testable without Docker, and :class:`Settings` refuses it outside
    development.
"""

from __future__ import annotations

import fnmatch
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autobench.core.ids import hash_seed
from autobench.taskspec.loader import TaskDirectory
from autobench.taskspec.schema import NetworkMode

#: Never captured in a filesystem diff: noise that hides the actual change.
DIFF_EXCLUDES = (
    "*.pyc",
    "__pycache__/*",
    "*/__pycache__/*",
    ".git/*",
    "*/.git/*",
    "node_modules/*",
    "*/node_modules/*",
    ".pytest_cache/*",
    "*/.pytest_cache/*",
    "*.log",
    ".venv/*",
    "*/.venv/*",
    "*.egg-info/*",
    # Harness scratch: the agent did not write these, so they are not its diff.
    ".autobench-*",
    "tmp/*",
    "opt/tests/*",
)

MAX_CAPTURE_BYTES = 256 * 1024


@dataclass(slots=True)
class SandboxSpec:
    """Everything needed to stand up one trial's world.

    The security fields have no "off" switch by design: ``privileged`` is not a
    field, ``cap_drop=ALL`` is not overridable, and the Docker socket is never
    mountable. A task that wants those is rejected at publish time (§12.1), so
    by the time a spec exists the question has already been settled.
    """

    image: str
    workdir: str = "/app"
    network: NetworkMode = NetworkMode.NONE
    cpus: float = 2.0
    memory: str = "4g"
    pids_limit: int = 512
    tmpfs_size: str = "512m"
    readonly_root: bool = False
    env: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    #: Mode B only: the FQDNs the egress proxy will forward.
    egress_allowlist: list[str] = field(default_factory=list)
    #: Read-only host mounts. The verifier's test mount is the only user in v1.
    readonly_mounts: dict[str, str] = field(default_factory=dict)
    entrypoint: list[str] | None = None

    @classmethod
    def from_task(
        cls,
        task: TaskDirectory,
        image: str,
        *,
        network: NetworkMode | None = None,
        seed: int | None = None,
        extra_env: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
    ) -> SandboxSpec:
        env = dict(task.spec.environment.env)
        env.update(_determinism_env(task))
        if seed is not None:
            env.update(AUTOBENCH_SEED=str(seed), PYTHONHASHSEED=str(hash_seed(seed)))
        # Every sandbox knows where its workspace is. Task scripts reference
        # ${AUTOBENCH_WORKDIR} rather than a hard-coded /app so that the same
        # script runs under the Docker executor and the local one.
        env["AUTOBENCH_WORKDIR"] = task.spec.environment.workdir
        if extra_env:
            env.update(extra_env)
        return cls(
            image=image,
            workdir=task.spec.environment.workdir,
            network=network or task.spec.environment.network,
            cpus=task.spec.environment.cpus,
            memory=task.spec.environment.memory,
            pids_limit=task.spec.environment.pids_limit,
            tmpfs_size=task.spec.environment.tmpfs_size,
            readonly_root=task.spec.environment.readonly_root,
            env=env,
            labels={"autobench.task": task.id, **(labels or {})},
        )


def _determinism_env(task: TaskDirectory) -> dict[str, str]:
    """Controls 5 and 6 from PRD §10, as environment variables.

    ``PYTHONHASHSEED`` alone removes a surprising amount of run-to-run
    variance: dict and set iteration order is otherwise randomised per process,
    and any task whose solution walks a set can flip on it.
    """
    det = task.spec.determinism
    env = {
        "AUTOBENCH_SEED": str(det.seed),
        "PYTHONHASHSEED": str(hash_seed(det.seed)),
        "PYTHONDONTWRITEBYTECODE": "1",
        "SOURCE_DATE_EPOCH": "1767225600",  # 2026-01-01T00:00:00Z
        "TZ": "UTC",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1" if det.forbid_network_in_tests else "0",
    }
    if det.freeze_time:
        env["AUTOBENCH_FREEZE_TIME"] = det.freeze_time
        env["FAKETIME"] = f"@{det.freeze_time}"
    env.update(det.env)
    return env


@dataclass(slots=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def combined(self, limit: int = 4_000) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(f"[stderr]\n{self.stderr.strip()}")
        text = "\n".join(parts) or "(no output)"
        if len(text) > limit:
            head = text[: limit // 2]
            tail = text[-limit // 2 :]
            omitted = len(text) - len(head) - len(tail)
            return f"{head}\n… {omitted} bytes elided …\n{tail}"
        return text


@dataclass(slots=True)
class FileState:
    """One file's identity at a point in time. Content only for text, capped."""

    path: str
    size: int
    digest: str
    text: str | None = None


@dataclass(slots=True)
class Snapshot:
    """The workdir at a moment. Two of these make a diff."""

    files: dict[str, FileState] = field(default_factory=dict)
    total_bytes: int = 0
    truncated: bool = False


@dataclass(slots=True)
class FileDiff:
    path: str
    change: str  # added | modified | deleted
    before: str | None = None
    after: str | None = None
    size_before: int = 0
    size_after: int = 0
    binary: bool = False


@dataclass(slots=True)
class DiffResult:
    """What the agent actually changed. Usually where the answer is."""

    files: list[FileDiff] = field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False

    @property
    def counts(self) -> dict[str, int]:
        out = {"added": 0, "modified": 0, "deleted": 0}
        for entry in self.files:
            out[entry.change] += 1
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "counts": self.counts,
            "total_bytes": self.total_bytes,
            "truncated": self.truncated,
            "files": [
                {
                    "path": f.path,
                    "change": f.change,
                    "before": f.before,
                    "after": f.after,
                    "size_before": f.size_before,
                    "size_after": f.size_after,
                    "binary": f.binary,
                }
                for f in self.files
            ],
        }


def should_capture(relative_path: str) -> bool:
    return not any(fnmatch.fnmatch(relative_path, pattern) for pattern in DIFF_EXCLUDES)


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def decode_text(data: bytes) -> str | None:
    """Return text, or ``None`` when the bytes are not plausibly text.

    A NUL byte in the first 8KB is the same heuristic git uses, and it is right
    often enough that nobody has to look at a binary blob in the diff viewer.
    """
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def diff_snapshots(before: Snapshot, after: Snapshot, *, max_bytes: int) -> DiffResult:
    """Compute added / modified / deleted between two snapshots."""
    result = DiffResult(truncated=before.truncated or after.truncated)
    budget = max_bytes

    for path, state in sorted(after.files.items()):
        previous = before.files.get(path)
        if previous is None:
            entry = FileDiff(
                path=path,
                change="added",
                after=state.text,
                size_after=state.size,
                binary=state.text is None,
            )
        elif previous.digest != state.digest:
            entry = FileDiff(
                path=path,
                change="modified",
                before=previous.text,
                after=state.text,
                size_before=previous.size,
                size_after=state.size,
                binary=state.text is None and previous.text is None,
            )
        else:
            continue

        cost = len(entry.before or "") + len(entry.after or "")
        if cost > budget:
            entry.before = entry.after = None
            entry.binary = True
            result.truncated = True
        else:
            budget -= cost
        result.files.append(entry)
        result.total_bytes += entry.size_after

    for path, state in sorted(before.files.items()):
        if path not in after.files:
            result.files.append(
                FileDiff(path=path, change="deleted", before=state.text, size_before=state.size)
            )
    return result


def snapshot_directory(root: Path, *, max_bytes: int = 8 * 1024 * 1024) -> Snapshot:
    """Snapshot a host directory. Used by LocalProvider and by the Docker
    provider after it has exported the container's workdir."""
    snapshot = Snapshot()
    budget = max_bytes
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(root).as_posix()
        if not should_capture(rel):
            continue
        try:
            data = path.read_bytes()
        except OSError:  # pragma: no cover - races with a running agent
            continue
        snapshot.total_bytes += len(data)
        text = decode_text(data) if len(data) <= MAX_CAPTURE_BYTES else None
        if text is not None and len(text) > budget:
            text = None
            snapshot.truncated = True
        elif text is not None:
            budget -= len(text)
        snapshot.files[rel] = FileState(
            path=rel, size=len(data), digest=digest_bytes(data), text=text
        )
    return snapshot


class Sandbox(ABC):
    """One trial's container. Created, used once, destroyed. Never reused."""

    id: str
    workdir: str

    @abstractmethod
    async def exec(
        self,
        command: str | list[str],
        *,
        timeout: float,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult: ...

    @abstractmethod
    async def write_file(self, path: str, content: str | bytes, *, mode: int = 0o644) -> None: ...

    @abstractmethod
    async def read_file(self, path: str) -> bytes: ...

    @abstractmethod
    async def snapshot(self) -> Snapshot: ...

    @abstractmethod
    async def freeze(self) -> None:
        """Stop the world. Nothing may mutate state between here and verification."""

    @abstractmethod
    async def destroy(self) -> None:
        """Idempotent, and must succeed even after a partial failure."""

    async def __aenter__(self) -> Sandbox:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.destroy()


class SandboxProvider(ABC):
    """Swap this out for Kubernetes, microVMs or a remote sandbox service."""

    name: str = "base"

    @abstractmethod
    async def ensure_image(self, task: TaskDirectory, *, force_rebuild: bool = False) -> str:
        """Build or pull, returning a digest. Idempotent per ``content_hash`` (O1)."""

    @abstractmethod
    async def create(self, spec: SandboxSpec) -> Sandbox: ...

    @abstractmethod
    async def create_verifier(
        self,
        spec: SandboxSpec,
        *,
        tests_dir: Path,
        tests_mount: str,
        workspace_from: Sandbox,
    ) -> Sandbox:
        """A clean container with the frozen workspace and the tests mounted read-only.

        The tests arrive **here**, after the agent phase, and nowhere else. The
        agent could not read them, could not chmod them, and could not overfit
        to them, because during its phase they did not exist inside its world.
        """

    @abstractmethod
    async def list_leaked_resources(self, task_id: str | None = None) -> list[str]:
        """Containers/volumes/networks that outlived their trial. Should be empty."""

    async def prefetch(self, tasks: list[TaskDirectory]) -> dict[str, str]:
        """Warm the image cache before trial 1 leases (optimisation O2)."""
        digests: dict[str, str] = {}
        for task in tasks:
            digests[task.id] = await self.ensure_image(task)
        return digests

    async def close(self) -> None:
        return None
