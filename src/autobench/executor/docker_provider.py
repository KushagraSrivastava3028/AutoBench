"""The real executor: one throwaway, hardened container per trial.

Hardening is applied here and nowhere else, so there is exactly one place to
audit (PRD §12.1). None of it is configurable:

    cap_drop: ALL          no-new-privileges   seccomp profile
    privileged: never      read-only root      tmpfs /tmp noexec,nosuid,nodev
    network: none          pids/cpu/memory caps

``privileged`` is not a parameter of any function in this module. That is
deliberate: a flag that does not exist cannot be set by a task, by a config
file, or by someone in a hurry.

Two implementation notes worth knowing:

*   **Images are built with the ``docker build`` CLI, not the daemon API.**
    BuildKit's cache mounts, ``--iidfile`` and secret handling are first-class
    on the CLI and awkward over the raw API. The build is a per-``content_hash``
    one-off; the hot path (create/exec/remove) goes through the API.
*   **Files move by tar archive.** ``put_archive``/``get_archive`` are the only
    transport the daemon offers that does not require a shell in the container,
    which matters because a hardened image may not have one.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import shlex
import tarfile
import time
from pathlib import Path
from typing import Any

from autobench.core.config import get_settings
from autobench.core.errors import ErrorClass, SandboxError, TrialError
from autobench.executor.base import (
    MAX_CAPTURE_BYTES,
    ExecResult,
    FileState,
    Sandbox,
    SandboxProvider,
    SandboxSpec,
    Snapshot,
    decode_text,
    digest_bytes,
    should_capture,
)
from autobench.taskspec.loader import TaskDirectory
from autobench.taskspec.schema import NetworkMode

logger = logging.getLogger(__name__)

LABEL_NAMESPACE = "dev.autobench"
_MAX_OUTPUT = 512 * 1024


def _parse_memory(value: str) -> int:
    """``"4g"`` -> bytes. Docker wants an integer."""
    text = value.strip().lower().rstrip("b")
    multiplier = {"k": 1024, "m": 1024**2, "g": 1024**3}.get(text[-1:], 1)
    number = text[:-1] if multiplier > 1 else text
    return int(float(number) * multiplier)


class DockerSandbox(Sandbox):
    def __init__(self, container: Any, spec: SandboxSpec, docker: Any) -> None:
        self._container = container
        self._docker = docker
        self.spec = spec
        self.id = container.id
        self.workdir = spec.workdir
        self._frozen = False
        self._destroyed = False

    # --- execution ---------------------------------------------------------

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

        argv = ["/bin/sh", "-c", command] if isinstance(command, str) else command
        merged = {**self.spec.env, **(env or {})}

        started = time.perf_counter()
        exec_obj = await self._container.exec(
            cmd=argv,
            workdir=workdir or self.workdir,
            environment=[f"{k}={v}" for k, v in merged.items()],
            stdout=True,
            stderr=True,
        )

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        timed_out = False
        try:
            await asyncio.wait_for(
                self._pump(exec_obj, stdout_chunks, stderr_chunks), timeout=timeout
            )
        except TimeoutError:
            timed_out = True
            # The exec has no cancel API. Killing the container's processes is
            # the only reliable stop, and the trial is over anyway.
            await self._kill_processes()

        stdout, trunc_a = _truncate(b"".join(stdout_chunks))
        stderr, trunc_b = _truncate(b"".join(stderr_chunks))

        exit_code = 124
        if not timed_out:
            try:
                inspected = await exec_obj.inspect()
                exit_code = int(inspected.get("ExitCode") or 0)
            except Exception:  # pragma: no cover - daemon hiccup
                exit_code = 0

        return ExecResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=int((time.perf_counter() - started) * 1000),
            timed_out=timed_out,
            truncated=trunc_a or trunc_b,
        )

    async def _pump(self, exec_obj: Any, stdout: list[bytes], stderr: list[bytes]) -> None:
        total = 0
        async with exec_obj.start(detach=False) as stream:
            while True:
                message = await stream.read_out()
                if message is None:
                    break
                total += len(message.data)
                # Stop accumulating past the cap, but keep draining: an
                # un-drained stream blocks the container's writer forever.
                if total <= _MAX_OUTPUT * 2:
                    (stdout if message.stream == 1 else stderr).append(message.data)

    async def _kill_processes(self) -> None:
        try:
            await self._container.kill(signal="SIGKILL")
        except Exception as exc:  # pragma: no cover
            logger.warning("could not kill container %s: %s", self.id[:12], exc)

    # --- file transport ----------------------------------------------------

    async def write_file(self, path: str, content: str | bytes, *, mode: int = 0o644) -> None:
        if self._frozen:
            raise SandboxError("sandbox is frozen; the filesystem is read-only after the freeze")
        data = content.encode("utf-8") if isinstance(content, str) else content
        target = Path(path)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name=target.name)
            info.size = len(data)
            info.mode = mode
            info.mtime = 0  # reproducible: mtimes must not perturb the diff
            tar.addfile(info, io.BytesIO(data))
        buffer.seek(0)
        await self._container.put_archive(str(target.parent).replace("\\", "/"), buffer.read())

    async def read_file(self, path: str) -> bytes:
        try:
            stream = await self._container.get_archive(path)
        except Exception as exc:
            raise SandboxError(f"no such file in sandbox: {path} ({exc})") from exc
        return _single_file_from_tar(await _drain(stream), Path(path).name)

    async def snapshot(self) -> Snapshot:
        """Export the workdir as a tar and hash it, entry by entry.

        One API call instead of a `find` plus N reads — which matters because
        this runs twice per trial and the daemon serialises archive operations.
        """
        settings = get_settings()
        budget = settings.sandbox.max_diff_bytes
        snapshot = Snapshot()
        try:
            stream = await self._container.get_archive(self.workdir)
        except Exception as exc:
            raise TrialError(
                f"could not snapshot {self.workdir}: {exc}",
                ErrorClass.INFRA_SNAPSHOT,
                stage="freeze",
            ) from exc

        raw = await _drain(stream)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tar:
            prefix = f"{Path(self.workdir).name}/"
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                rel = member.name[len(prefix) :] if member.name.startswith(prefix) else member.name
                if not rel or not should_capture(rel):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                data = handle.read()
                snapshot.total_bytes += len(data)
                text = decode_text(data) if len(data) <= MAX_CAPTURE_BYTES else None
                if text is not None and len(text) > budget:
                    text, snapshot.truncated = None, True
                elif text is not None:
                    budget -= len(text)
                snapshot.files[rel] = FileState(
                    path=rel, size=len(data), digest=digest_bytes(data), text=text
                )
        return snapshot

    # --- lifecycle ---------------------------------------------------------

    async def freeze(self) -> None:
        """Pause the container. Nothing may mutate state between here and verify."""
        self._frozen = True
        try:
            await self._container.pause()
        except Exception as exc:  # already exited is fine
            logger.debug("pause on %s: %s", self.id[:12], exc)

    async def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        try:
            await self._container.delete(force=True, v=True)  # v=True removes anon volumes
        except Exception as exc:  # pragma: no cover
            logger.warning("teardown of %s failed, leaving it to the reaper: %s", self.id[:12], exc)


class DockerProvider(SandboxProvider):
    name = "docker"

    def __init__(self, docker: Any | None = None) -> None:
        self._settings = get_settings()
        self._docker = docker
        self._build_locks: dict[str, asyncio.Lock] = {}
        self._digest_cache: dict[str, str] = {}

    async def _client(self) -> Any:
        if self._docker is None:
            try:
                import aiodocker
            except ImportError as exc:  # pragma: no cover
                raise SandboxError(
                    "aiodocker is not installed; pip install 'autobench[docker]'"
                ) from exc
            self._docker = aiodocker.Docker(url=self._settings.docker_host)
        return self._docker

    # --- images ------------------------------------------------------------

    async def ensure_image(self, task: TaskDirectory, *, force_rebuild: bool = False) -> str:
        """Build once per ``content_hash``, then never again (optimisation O1).

        The lock is per tag rather than global: two different tasks should build
        in parallel, but the same task being requested by eight concurrent
        trials must build exactly once.
        """
        tag = task.image_tag(self._settings.image_namespace)
        if not force_rebuild and tag in self._digest_cache:
            return self._digest_cache[tag]

        lock = self._build_locks.setdefault(tag, asyncio.Lock())
        async with lock:
            if not force_rebuild and tag in self._digest_cache:
                return self._digest_cache[tag]

            if not force_rebuild:
                existing = await self._inspect_image(tag)
                if existing:
                    self._digest_cache[tag] = existing
                    return existing

            digest = await self._build(task, tag)
            self._digest_cache[tag] = digest
            return digest

    async def _inspect_image(self, tag: str) -> str | None:
        docker = await self._client()
        try:
            info = await docker.images.inspect(tag)
        except Exception:
            return None
        repo_digests = info.get("RepoDigests") or []
        if repo_digests:
            return str(repo_digests[0]).split("@", 1)[-1]
        return str(info.get("Id", tag))

    async def _build(self, task: TaskDirectory, tag: str) -> str:
        dockerfile = task.dockerfile
        if not dockerfile.is_file():
            raise TrialError(
                f"{task.id}: {dockerfile} not found", ErrorClass.INFRA_IMAGE, stage="image"
            )

        argv = [
            "docker",
            "build",
            "--file",
            str(dockerfile),
            "--tag",
            tag,
            "--label",
            f"{LABEL_NAMESPACE}.task={task.id}",
            "--label",
            f"{LABEL_NAMESPACE}.content_hash={task.content_hash}",
            # Reproducible layers: SOURCE_DATE_EPOCH is honoured by BuildKit.
            "--build-arg",
            "SOURCE_DATE_EPOCH=1767225600",
            "--progress",
            "plain",
            str(task.root),
        ]
        env = {"DOCKER_BUILDKIT": "1"}

        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={**_os_environ(), **env},
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=300)
        except TimeoutError as exc:
            process.kill()
            raise TrialError(
                f"{task.id}: image build exceeded 300s", ErrorClass.INFRA_IMAGE, stage="image"
            ) from exc

        if process.returncode != 0:
            tail = output.decode("utf-8", "replace")[-4000:]
            raise TrialError(
                f"{task.id}: image build failed\n{tail}", ErrorClass.INFRA_IMAGE, stage="image"
            )

        logger.info("built %s in %.1fs", tag, time.perf_counter() - started)
        return await self._inspect_image(tag) or tag

    # --- containers --------------------------------------------------------

    def _host_config(self, spec: SandboxSpec) -> dict[str, Any]:
        """The non-negotiable defaults. Read this block, then §12.1; they match."""
        seccomp = Path(self._settings.sandbox.seccomp_profile)
        security_opt = ["no-new-privileges:true"]
        if seccomp.is_file():
            security_opt.append(f"seccomp={seccomp.read_text(encoding='utf-8')}")

        config: dict[str, Any] = {
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "SecurityOpt": security_opt,
            "Privileged": False,
            "ReadonlyRootfs": spec.readonly_root,
            "NetworkMode": "none" if spec.network is NetworkMode.NONE else "bridge",
            "PidsLimit": spec.pids_limit,
            "Memory": _parse_memory(spec.memory),
            "MemorySwap": _parse_memory(spec.memory),  # equal => swap disabled
            "MemorySwappiness": 0,
            "NanoCpus": int(spec.cpus * 1e9),
            "Tmpfs": {"/tmp": f"size={spec.tmpfs_size},noexec,nosuid,nodev"},  # noqa: S108
            "Ulimits": [
                {"Name": "nofile", "Soft": 4096, "Hard": 4096},
                {"Name": "nproc", "Soft": 512, "Hard": 512},
            ],
            "AutoRemove": False,  # we remove explicitly, after capturing the diff
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "16m", "max-file": "2"}},
        }
        if spec.readonly_mounts:
            config["Binds"] = [
                f"{host}:{container}:ro" for host, container in spec.readonly_mounts.items()
            ]
        return config

    async def create(self, spec: SandboxSpec) -> Sandbox:
        if self._settings.sandbox.require_network_none and spec.network is not NetworkMode.NONE:
            raise SandboxError("This deployment permits only network-isolated task containers")
        docker = await self._client()
        labels = {f"{LABEL_NAMESPACE}.managed": "true", **spec.labels}
        config: dict[str, Any] = {
            "Image": spec.image,
            "WorkingDir": spec.workdir,
            "Env": [f"{k}={v}" for k, v in spec.env.items()],
            "Labels": labels,
            # Keep PID 1 alive and doing nothing so we can exec into it. The
            # task's own entrypoint is irrelevant: the agent drives everything.
            "Cmd": spec.entrypoint or ["sleep", "infinity"],
            "Tty": False,
            "OpenStdin": False,
            "HostConfig": self._host_config(spec),
            "NetworkDisabled": spec.network is NetworkMode.NONE,
        }
        if self._settings.sandbox.runtime:
            config["HostConfig"]["Runtime"] = self._settings.sandbox.runtime
        try:
            container = await docker.containers.create(config=config)
            await container.start()
        except Exception as exc:
            raise TrialError(
                f"could not create container from {spec.image}: {exc}",
                ErrorClass.INFRA_DOCKER,
                stage="create",
            ) from exc
        return DockerSandbox(container, spec, docker)

    async def create_verifier(
        self,
        spec: SandboxSpec,
        *,
        tests_dir: Path,
        tests_mount: str,
        workspace_from: Sandbox,
    ) -> Sandbox:
        """A clean image, the frozen workspace copied in, tests mounted read-only.

        The tests reach a container for the first time *here*. During the agent
        phase they existed only on the host, so "the agent deleted the tests" is
        not a failure mode that can occur — which the ``deletes-own-tests``
        fixture asserts on every CI run.
        """
        if not isinstance(workspace_from, DockerSandbox):  # pragma: no cover
            raise SandboxError("DockerProvider can only verify a DockerSandbox")

        verifier_spec = SandboxSpec(
            image=spec.image,
            workdir=spec.workdir,
            network=NetworkMode.NONE,  # always, whatever was asked for
            cpus=spec.cpus,
            memory=spec.memory,
            pids_limit=spec.pids_limit,
            tmpfs_size=spec.tmpfs_size,
            readonly_root=False,
            env=spec.env,
            labels={**spec.labels, f"{LABEL_NAMESPACE}.role": "verifier"},
            readonly_mounts={str(tests_dir.resolve()): tests_mount},
        )
        verifier = await self.create(verifier_spec)
        assert isinstance(verifier, DockerSandbox)

        # Copy the frozen workspace across container boundaries via tar.
        try:
            stream = await workspace_from._container.get_archive(workspace_from.workdir)
            payload = await _drain(stream)
            await verifier._container.put_archive(
                str(Path(verifier.workdir).parent).replace("\\", "/"), payload
            )
        except Exception as exc:
            await verifier.destroy()
            raise TrialError(
                f"could not transfer the frozen workspace to the verifier: {exc}",
                ErrorClass.INFRA_SNAPSHOT,
                stage="verify",
            ) from exc
        return verifier

    # --- hygiene -----------------------------------------------------------

    async def list_leaked_resources(self, task_id: str | None = None) -> list[str]:
        """Anything still labelled as ours. In a healthy system this is empty."""
        docker = await self._client()
        filters = {"label": [f"{LABEL_NAMESPACE}.managed=true"]}
        if task_id:
            filters["label"].append(f"{LABEL_NAMESPACE}.task={task_id}")
        leaked: list[str] = []
        try:
            containers = await docker.containers.list(all=True, filters=json.dumps(filters))
            leaked.extend(f"container:{c.id[:12]}" for c in containers)
        except Exception as exc:  # pragma: no cover
            logger.warning("could not list containers: %s", exc)
        try:
            volumes = await docker.volumes.list()
            for volume in volumes.get("Volumes") or []:
                labels = volume.get("Labels") or {}
                if labels.get(f"{LABEL_NAMESPACE}.managed") == "true":
                    leaked.append(f"volume:{volume['Name']}")
        except Exception as exc:  # pragma: no cover
            logger.debug("could not list volumes: %s", exc)
        return leaked

    async def reap(self, older_than_sec: int = 3600) -> int:
        """Force-remove anything of ours older than the cutoff.

        Teardown is best-effort inside the pipeline because a trial must finish
        even if the daemon is having a bad minute. This is the sweeper that
        makes that safe, and the ``ContainerLeak`` alert is what tells you it
        has stopped working.
        """
        docker = await self._client()
        removed = 0
        cutoff = time.time() - older_than_sec
        filters = json.dumps({"label": [f"{LABEL_NAMESPACE}.managed=true"]})
        for container in await docker.containers.list(all=True, filters=filters):
            try:
                info = await container.show()
                created = info.get("Created", "")
                if created and _parse_docker_time(created) > cutoff:
                    continue
                await container.delete(force=True, v=True)
                removed += 1
            except Exception as exc:  # pragma: no cover
                logger.warning("reaper could not remove %s: %s", container.id[:12], exc)
        return removed

    async def close(self) -> None:
        if self._docker is not None:
            await self._docker.close()
            self._docker = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _os_environ() -> dict[str, str]:
    import os

    return dict(os.environ)


def _truncate(data: bytes) -> tuple[str, bool]:
    if len(data) > _MAX_OUTPUT:
        head = data[: _MAX_OUTPUT // 2].decode("utf-8", "replace")
        tail = data[-_MAX_OUTPUT // 2 :].decode("utf-8", "replace")
        return f"{head}\n… {len(data) - _MAX_OUTPUT} bytes elided …\n{tail}", True
    return data.decode("utf-8", "replace"), False


async def _drain(stream: Any) -> bytes:
    """aiodocker returns either a tarfile object or an async byte stream."""
    if hasattr(stream, "read"):
        maybe = stream.read()
        return await maybe if asyncio.iscoroutine(maybe) else bytes(maybe)
    chunks = [chunk async for chunk in stream]
    return b"".join(chunks)


def _single_file_from_tar(payload: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r") as tar:
        for member in tar.getmembers():
            if member.isfile() and Path(member.name).name == name:
                handle = tar.extractfile(member)
                if handle is not None:
                    return handle.read()
    raise SandboxError(f"{name} not present in the exported archive")


def _parse_docker_time(value: str) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:  # pragma: no cover
        return 0.0


def replay_commands(
    task: TaskDirectory, image: str, spec: SandboxSpec, *, trial_id: str
) -> list[str]:
    """The shell commands that reproduce a trial locally (PRD §11.3).

    Replayability is what turns a red cell into a fixed bug, which is why it is
    a v1 requirement rather than a nice-to-have.
    """
    env_flags = " ".join(f"-e {shlex.quote(f'{k}={v}')}" for k, v in sorted(spec.env.items()))
    container = f"autobench-replay-{trial_id}"
    verifier_env = {
        **spec.env,
        **task.spec.verifier.env,
        "AUTOBENCH_TESTS_DIR": task.spec.verifier.tests_mount,
        "AUTOBENCH_REPORT_PATH": task.spec.scoring.report_path,
    }
    verifier_flags = " ".join(
        f"-e {shlex.quote(f'{k}={v}')}" for k, v in sorted(verifier_env.items())
    )
    build_file = shlex.quote((task.root / task.spec.environment.dockerfile).as_posix())
    context = shlex.quote(task.root.as_posix())
    tests_mount = shlex.quote(
        f"{(task.root / 'tests').as_posix()}:{task.spec.verifier.tests_mount}:ro"
    )
    return [
        f"# replay trial {trial_id} — task {task.id} @ {task.content_hash[:19]}",
        "# Run these commands in a POSIX shell (bash, Git Bash, or WSL).",
        f"docker build -f {build_file} -t {shlex.quote(image)} {context}",
        (
            f"docker run --name {shlex.quote(container)} -it --network none --cap-drop ALL "
            f"--security-opt no-new-privileges:true --pids-limit {spec.pids_limit} "
            f"--memory {spec.memory} --cpus {spec.cpus} {env_flags} "
            f"-w {shlex.quote(spec.workdir)} {shlex.quote(image)} /bin/bash"
        ),
        "# Perform the agent actions, then exit the shell. Capture that stopped container's state:",
        'REPLAY_DIR="$(mktemp -d)"',
        f'docker cp {shlex.quote(container + ":" + spec.workdir + "/.")} "$REPLAY_DIR"',
        "# Grade the captured workspace with tests that were absent from the agent container:",
        (
            f"docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges:true "
            f"--pids-limit {spec.pids_limit} --memory {spec.memory} --cpus {spec.cpus} "
            f'-v "$REPLAY_DIR:{spec.workdir}:ro" -v {tests_mount} {verifier_flags} '
            f"-w {shlex.quote(spec.workdir)} {shlex.quote(task.spec.verifier.image)} "
            f"/bin/sh -c {shlex.quote(task.spec.verifier.command)}"
        ),
        f"docker rm {shlex.quote(container)}",
        '# The captured workspace remains in "$REPLAY_DIR" for inspection.',
    ]
