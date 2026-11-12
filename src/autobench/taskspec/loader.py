"""Read a task directory from disk (or a tarball) into a :class:`TaskDirectory`.

Errors here are addressed to a human author, not a stack trace reader: they name
the file, and where TOML gives one, the line.
"""

from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from autobench.core.errors import TaskSpecError
from autobench.taskspec.hashing import file_manifest, hash_task_directory, image_tag
from autobench.taskspec.schema import TaskSpec

TASK_TOML = "task.toml"
INSTRUCTION_MD = "instruction.md"
REQUIRED_FILES = (TASK_TOML, INSTRUCTION_MD)
#: One of these must exist — the test entrypoint the verifier runs.
TEST_ENTRYPOINTS = ("tests/run-tests.sh", "tests/test.sh")
MAX_UPLOAD_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_UPLOAD_MEMBERS = 5_000
#: One of these must exist — the oracle.
SOLUTION_ENTRYPOINTS = ("solution/solve.sh", "solution/solution.sh")


@dataclass(frozen=True, slots=True)
class TaskDirectory:
    """A parsed, on-disk task. Immutable by construction."""

    root: Path
    spec: TaskSpec
    instruction: str
    content_hash: str
    manifest: dict[str, str]

    @property
    def id(self) -> str:
        return self.spec.task.id

    @property
    def semver(self) -> str:
        return self.spec.task.version

    @property
    def dockerfile(self) -> Path:
        return self.root / self.spec.environment.dockerfile

    @property
    def compose_file(self) -> Path | None:
        compose = self.spec.environment.compose
        return self.root / compose if compose else None

    @property
    def tests_dir(self) -> Path:
        return self.root / "tests"

    @property
    def solution_dir(self) -> Path:
        return self.root / "solution"

    def test_entrypoint(self) -> Path:
        return _first_existing(self.root, TEST_ENTRYPOINTS, "test entrypoint")

    def solution_entrypoint(self) -> Path:
        return _first_existing(self.root, SOLUTION_ENTRYPOINTS, "oracle solution")

    def image_tag(self, namespace: str = "autobench") -> str:
        return image_tag(self.id, self.content_hash, namespace)

    def read_tests(self) -> dict[str, str]:
        """Every test file as text. Used by the leak lint and the AST lint."""
        if not self.tests_dir.is_dir():
            return {}
        out: dict[str, str] = {}
        for path in sorted(self.tests_dir.rglob("*")):
            if path.is_file():
                try:
                    out[path.relative_to(self.root).as_posix()] = path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:  # pragma: no cover
                    continue
        return out

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "semver": self.semver,
            "content_hash": self.content_hash,
            "spec": self.spec.to_json(),
            "instruction": self.instruction,
            "manifest": self.manifest,
        }


def _first_existing(root: Path, candidates: tuple[str, ...], label: str) -> Path:
    for rel in candidates:
        path = root / rel
        if path.is_file():
            return path
    raise TaskSpecError(
        f"missing {label}: expected one of {', '.join(candidates)}",
        missing=list(candidates),
    )


def load_task(root: Path | str) -> TaskDirectory:
    """Parse a task directory. Raises :class:`TaskSpecError` with a human message."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise TaskSpecError(f"{root} is not a directory")

    for required in REQUIRED_FILES:
        if not (root / required).is_file():
            raise TaskSpecError(
                f"{root.name}/ is missing required file {required}", missing=required
            )

    raw_text = (root / TASK_TOML).read_text(encoding="utf-8-sig")
    try:
        raw: dict[str, Any] = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as exc:
        raise TaskSpecError(f"{TASK_TOML} is not valid TOML: {exc}", file=TASK_TOML) from exc

    raw = _apply_harbor_compat(raw, root)

    try:
        spec = TaskSpec.model_validate(raw)
    except ValidationError as exc:
        raise TaskSpecError(
            f"{TASK_TOML} failed validation:\n{_format_errors(exc)}",
            file=TASK_TOML,
            errors=exc.errors(include_url=False),
        ) from exc

    instruction = (root / INSTRUCTION_MD).read_text(encoding="utf-8-sig").strip()
    if not instruction:
        raise TaskSpecError(f"{INSTRUCTION_MD} is empty — the agent needs a prompt")

    return TaskDirectory(
        root=root,
        spec=spec,
        instruction=instruction,
        content_hash=hash_task_directory(root),
        manifest=file_manifest(root),
    )


def _apply_harbor_compat(raw: dict[str, Any], root: Path) -> dict[str, Any]:
    """Accept a Harbor task directory unmodified.

    Harbor puts author/difficulty/category at the top level of ``task.toml`` and
    spells its timeouts ``max_agent_timeout_sec`` / ``max_test_timeout_sec``.
    Autobench reads those, then applies its own defaults on top — which is what
    "superset of the Harbor layout" has to mean in practice if the Harbor Hub
    corpus is going to load without a migration step.
    """
    raw = dict(raw)
    task = dict(raw.get("task") or {})

    for key in ("id", "name", "title", "category", "difficulty", "tags", "author", "version"):
        if key in raw and key not in task:
            task[key] = raw.pop(key)
    if "name" in task and "title" not in task:
        task["title"] = task.pop("name")
    task.setdefault("id", root.name)
    task.setdefault("title", task["id"].replace("-", " ").title())

    agent = dict(raw.get("agent") or {})
    if "max_agent_timeout_sec" in raw:
        agent.setdefault("timeout_sec", int(raw.pop("max_agent_timeout_sec")))
    verifier = dict(raw.get("verifier") or {})
    if "max_test_timeout_sec" in raw:
        verifier.setdefault("timeout_sec", int(raw.pop("max_test_timeout_sec")))

    # Harbor's entrypoint is tests/test.sh; ours is tests/run-tests.sh.
    if (root / "tests" / "test.sh").is_file() and not (root / "tests" / "run-tests.sh").is_file():
        verifier.setdefault("command", 'bash "$AUTOBENCH_TESTS_DIR/test.sh"')

    raw["task"] = task
    if agent:
        raw["agent"] = agent
    if verifier:
        raw["verifier"] = verifier
    # Harbor carries keys Autobench has no opinion about; drop rather than reject.
    for unknown in ("parser_name", "run_tests_in_same_shell", "disable_asciinema"):
        raw.pop(unknown, None)
    return raw


def _format_errors(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors(include_url=False):
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  [{location}] {err['msg']}")
    return "\n".join(lines)


def load_task_from_tar(data: bytes) -> tuple[TaskDirectory, Path]:
    """Extract an uploaded tarball and parse it.

    Returns the parsed task and the temp directory, which the caller owns and
    must clean up. Path traversal (``../``) and absolute members are refused —
    an upload endpoint that writes outside its extraction root is a remote file
    write.
    """
    tmp = Path(tempfile.mkdtemp(prefix="autobench-upload-"))
    try:
        try:
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
                expanded = 0
                for count, member in enumerate(tar, start=1):
                    if count > MAX_UPLOAD_MEMBERS:
                        raise TaskSpecError(
                            "archive contains too many files (maximum 5,000 members)"
                        )
                    if member.size < 0:
                        raise TaskSpecError("archive contains an invalid file size")
                    expanded += member.size
                    if expanded > MAX_UPLOAD_EXPANDED_BYTES:
                        raise TaskSpecError("archive exceeds the 128 MiB expanded size limit")
                    target = (tmp / member.name.replace("\\", "/")).resolve()
                    if not target.is_relative_to(tmp.resolve()):
                        raise TaskSpecError(
                            f"archive member escapes the extraction root: {member.name}"
                        )
                    if member.issym() or member.islnk():
                        raise TaskSpecError(f"archive contains a link member: {member.name}")
                    if not member.isfile() and not member.isdir():
                        raise TaskSpecError("archive contains a special file")
                tar.extractall(tmp, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise TaskSpecError("upload must be a readable tar or tar.gz archive") from exc

        # Tolerate both task.toml at the root and one wrapping directory.
        root = tmp
        if not (root / TASK_TOML).is_file():
            children = [p for p in tmp.iterdir() if p.is_dir()]
            if len(children) == 1 and (children[0] / TASK_TOML).is_file():
                root = children[0]
        return load_task(root), tmp
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def discover_tasks(tasks_dir: Path) -> list[Path]:
    """Every directory under ``tasks_dir`` that contains a ``task.toml``."""
    tasks_dir = Path(tasks_dir)
    if not tasks_dir.is_dir():
        return []
    found = {p.parent for p in tasks_dir.rglob(TASK_TOML)}
    return sorted(found, key=lambda p: p.as_posix())
