"""A Docker-free executor for development and CI-without-a-daemon.

**This provides no isolation.** Commands run as the host user in a throwaway
directory. It exists because the interesting engineering in Autobench — the
scheduler, the three-tier budget, the lease/reap protocol, the statistics, the
UI — should be runnable and testable without a Docker daemon, and because a
contributor on Windows should be able to run the test suite.

What it *does* preserve, so that tests written against it mean something:

* one fresh workspace per trial, destroyed afterwards
* the tests directory materialised only in the verifier phase, never in the
  agent's workspace
* the freeze: after ``freeze()``, writes are refused
* timeouts, exit codes, output capture, and the snapshot/diff contract

:class:`Settings` refuses this provider outside ``environment=development``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from autobench.core.config import get_settings
from autobench.core.errors import SandboxError
from autobench.executor.base import (
    ExecResult,
    Sandbox,
    SandboxProvider,
    SandboxSpec,
    Snapshot,
    snapshot_directory,
)
from autobench.taskspec.loader import TaskDirectory

logger = logging.getLogger(__name__)

_MAX_OUTPUT = 512 * 1024

#: Variables whose values are container paths and must be remapped to the host.
_MAPPED_PATH_VARS = (
    "AUTOBENCH_WORKDIR",
    "AUTOBENCH_TESTS_DIR",
    "AUTOBENCH_REPORT_PATH",
    "AUTOBENCH_SOLUTION_DIR",
    "AUTOBENCH_SETUP_SCRIPT",
)


def _bash_path(path: Path) -> str:
    r"""``C:\Users\x`` -> ``/c/Users/x``.

    Git Bash on Windows understands the MSYS form but not the native one, and a
    backslash path interpolated into a task script becomes an escape-sequence bug.
    """
    text = str(path)
    if os.name == "nt" and len(text) > 2 and text[1] == ":":
        return "/" + text[0].lower() + text[2:].replace("\\", "/")
    return text.replace("\\", "/")


class LocalSandbox(Sandbox):
    def __init__(self, root: Path, spec: SandboxSpec, sandbox_id: str) -> None:
        self.id = sandbox_id
        self.root = root
        self.spec = spec
        self.workdir = spec.workdir
        self._frozen = False
        self._destroyed = False

    # --- path mapping ------------------------------------------------------

    def _host_path(self, container_path: str) -> Path:
        """Map a container-absolute path onto the workspace.

        Path traversal is refused rather than clamped: a task that writes to
        ``../../etc`` is telling you something, and silently rewriting it would
        hide it.
        """
        normalised = container_path.replace("\\", "/")
        if normalised.startswith(self.workdir):
            relative = normalised[len(self.workdir) :].lstrip("/")
        elif normalised.startswith("/"):
            relative = normalised.lstrip("/")
        else:
            relative = normalised
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root.resolve()):
            raise SandboxError(f"path {container_path!r} escapes the sandbox workspace")
        return candidate

    @property
    def _work_root(self) -> Path:
        return self.root

    def _shim_dir(self) -> Path:
        """Provide the interpreters a container image would have provided.

        Written once per sandbox, under a ``.autobench-`` prefix so it is
        excluded from the filesystem diff — the agent did not create it, so it
        is not part of what the agent changed.
        """
        shim = self.root / ".autobench-bin"
        if shim.is_dir():
            return shim
        shim.mkdir(parents=True, exist_ok=True)
        interpreter = _bash_path(Path(sys.executable))
        for name in ("python3", "python"):
            script = shim / name
            script.write_text(
                f'#!/usr/bin/env bash\nexec "{interpreter}" "$@"\n',
                encoding="utf-8",
                newline="\n",
            )
            if os.name != "nt":
                script.chmod(0o755)
        return shim

    # --- Sandbox -----------------------------------------------------------

    async def exec(
        self,
        command: str | list[str],
        *,
        timeout: float,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        if self._destroyed:
            raise SandboxError("sandbox has been destroyed")
        if self._frozen:
            raise SandboxError("sandbox is frozen; no execution after the freeze")

        cwd = self._host_path(workdir) if workdir else self._work_root
        cwd.mkdir(parents=True, exist_ok=True)

        merged = {**os.environ, **self.spec.env, **(env or {})}
        # Container-absolute paths do not exist on the host, so the well-known
        # location variables are remapped into the workspace. Tasks that write
        # `${AUTOBENCH_WORKDIR}/out.txt` rather than `/app/out.txt` therefore run
        # unchanged under both executors — which is why the reference tasks are
        # authored that way and why task-authoring.md insists on it.
        for key in _MAPPED_PATH_VARS:
            value = merged.get(key)
            if value:
                merged[key] = _bash_path(self._host_path(value))
        merged["AUTOBENCH_EXECUTOR"] = "local"
        # The local executor cannot enforce --network none, so at least make the
        # usual suspects fail fast rather than silently reaching the internet.
        if self.spec.network.value == "none":
            merged.update(
                {
                    "http_proxy": "http://127.0.0.1:9",
                    "https_proxy": "http://127.0.0.1:9",
                    "HTTP_PROXY": "http://127.0.0.1:9",
                    "HTTPS_PROXY": "http://127.0.0.1:9",
                    "no_proxy": "",
                }
            )

        # A task image would provide `python3`; the host may not, especially on
        # Windows. The shim directory supplies the interpreters a task script
        # expects, so a task written for a container runs here unchanged.
        merged["PATH"] = f"{self._shim_dir()}{os.pathsep}{merged.get('PATH', '')}"

        shell = _shell_prefix()
        if isinstance(command, str):
            command = self._map_workdir(command)
        argv = [*shell, command] if isinstance(command, str) else command

        started = time.perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                env=merged,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # Own process group, so a timeout kills the children too. A task
                # that backgrounds a server and exits must not leave it running.
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            raise SandboxError(f"could not start command: {exc}") from exc

        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            timed_out = True
            _kill_tree(process)
            try:
                stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=5)
            except (TimeoutError, ProcessLookupError):  # pragma: no cover
                stdout_b, stderr_b = b"", b""

        stdout, truncated_out = _truncate(stdout_b)
        stderr, truncated_err = _truncate(stderr_b)
        return ExecResult(
            exit_code=124 if timed_out else (process.returncode or 0),
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=timed_out,
            truncated=truncated_out or truncated_err,
        )

    def _map_workdir(self, command: str) -> str:
        """Rewrite the declared container workdir to its host equivalent.

        A model told "your working directory is /app" will write
        ``> /app/out.txt``, and on the host that path does not exist. Rewriting
        it is a development-executor affordance, not a general one: the Docker
        executor needs nothing here because /app really is /app.

        The lookahead keeps ``/application`` from matching ``/app``.
        """
        if not self.workdir.startswith("/") or self.workdir == "/":
            return command
        pattern = re.compile(re.escape(self.workdir) + r"(?=[/\s\"'|;)&]|$)")

        def mapped(match: re.Match[str]) -> str:
            path = _bash_path(self.root)
            return (
                path
                if match.start() > 0 and command[match.start() - 1] in "\"'"
                else shlex.quote(path)
            )

        return pattern.sub(mapped, command)

    async def write_file(self, path: str, content: str | bytes, *, mode: int = 0o644) -> None:
        if self._frozen:
            raise SandboxError("sandbox is frozen; the filesystem is read-only after the freeze")
        target = self._host_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        target.write_bytes(data)
        if os.name != "nt":
            target.chmod(mode)

    async def read_file(self, path: str) -> bytes:
        target = self._host_path(path)
        if not target.is_file():
            raise SandboxError(f"no such file in sandbox: {path}")
        return target.read_bytes()

    async def snapshot(self) -> Snapshot:
        settings = get_settings()
        return await asyncio.to_thread(
            snapshot_directory, self._work_root, max_bytes=settings.sandbox.max_diff_bytes
        )

    async def freeze(self) -> None:
        self._frozen = True

    async def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        await asyncio.to_thread(shutil.rmtree, self.root, True)


class LocalProvider(SandboxProvider):
    name = "local"

    def __init__(self) -> None:
        self._settings = get_settings()
        self._live: dict[str, LocalSandbox] = {}

    async def ensure_image(self, task: TaskDirectory, *, force_rebuild: bool = False) -> str:
        """No image to build; the content hash *is* the identity.

        Returned in the same ``sha256:`` shape as a real digest so that nothing
        downstream has to branch on which provider produced it.
        """
        return task.content_hash

    async def create(self, spec: SandboxSpec) -> Sandbox:
        self._settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="trial-", dir=str(self._settings.workspaces_dir)))
        sandbox_id = f"local-{root.name}"
        sandbox = LocalSandbox(root, spec, sandbox_id)
        self._live[sandbox_id] = sandbox

        # Seed the workspace the way the Dockerfile's COPY would have.
        seed = spec.labels.get("autobench.seed_dir")
        if seed and Path(seed).is_dir():
            await asyncio.to_thread(shutil.copytree, seed, str(root), dirs_exist_ok=True)
        return sandbox

    async def create_verifier(
        self,
        spec: SandboxSpec,
        *,
        tests_dir: Path,
        tests_mount: str,
        workspace_from: Sandbox,
    ) -> Sandbox:
        """Clean workspace + frozen agent output + tests, materialised only now."""
        if not isinstance(workspace_from, LocalSandbox):  # pragma: no cover
            raise SandboxError("LocalProvider can only verify a LocalSandbox")

        verifier = await self.create(spec)
        assert isinstance(verifier, LocalSandbox)

        # Copy the frozen workspace across. A copy, not a mount: the verifier
        # must not be able to hand the agent's container anything.
        await asyncio.to_thread(
            shutil.copytree, str(workspace_from.root), str(verifier.root), dirs_exist_ok=True
        )

        if tests_dir.is_dir():
            mount_target = verifier._host_path(tests_mount)
            await asyncio.to_thread(
                shutil.copytree, str(tests_dir), str(mount_target), dirs_exist_ok=True
            )
            if os.name != "nt":
                for path in mount_target.rglob("*"):
                    if path.is_file():
                        path.chmod(0o555)
        return verifier

    async def list_leaked_resources(self, task_id: str | None = None) -> list[str]:
        """Workspaces that outlived their trial.

        On the Docker provider this catches containers and volumes; here it
        catches directories, which is the same failure with a different name:
        teardown did not run, and the disk fills.
        """
        leaked = [sid for sid, sandbox in self._live.items() if not sandbox._destroyed]
        workspaces = self._settings.workspaces_dir
        if workspaces.is_dir():
            cutoff = time.time() - 3600
            leaked.extend(
                str(path.name)
                for path in workspaces.iterdir()
                if path.is_dir() and path.stat().st_mtime < cutoff
            )
        return sorted(set(leaked))

    async def close(self) -> None:
        for sandbox in list(self._live.values()):
            await sandbox.destroy()
        self._live.clear()


def _truncate(data: bytes) -> tuple[str, bool]:
    """Decode and cap. An agent that cats a 2GB file must not OOM the worker."""
    if len(data) > _MAX_OUTPUT:
        head = data[: _MAX_OUTPUT // 2].decode("utf-8", "replace")
        tail = data[-_MAX_OUTPUT // 2 :].decode("utf-8", "replace")
        elided = len(data) - _MAX_OUTPUT
        return f"{head}\n… {elided} bytes elided …\n{tail}", True
    return data.decode("utf-8", "replace"), False


def _shell_prefix() -> list[str]:
    """A POSIX shell, because every task script is written for one.

    On Windows that means Git Bash or WSL. Falling back to ``cmd.exe`` would
    silently change the semantics of every ``&&`` and every quote in a task's
    scripts, so we fail loudly instead.
    """
    if os.name != "nt":
        return ["/bin/sh", "-c"]
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash"),
    ):
        if candidate and Path(candidate).exists():
            return [candidate, "-c"]
    raise SandboxError(
        "LocalProvider needs a POSIX shell. Install Git for Windows (which ships "
        "bash), use WSL, or switch to AUTOBENCH_EXECUTOR=docker."
    )


def _kill_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the process *group*. A task that spawns children must not orphan them."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), 9)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except ProcessLookupError:  # pragma: no cover
            pass


if sys.platform == "win32":  # pragma: no cover - platform specific
    # Subprocess creation on Windows needs the Proactor loop, which is the
    # default from 3.8 on; assert it rather than discovering it at runtime.
    _ = asyncio.ProactorEventLoop
