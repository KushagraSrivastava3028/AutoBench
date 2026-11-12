"""Task specification: hashing, schema, the security policy and the lints."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from autobench.core.errors import TaskSpecError
from autobench.taskspec.hashing import hash_task_directory, image_tag
from autobench.taskspec.lints import (
    lint_leak,
    lint_security,
    lint_state_based,
    lint_tolerance,
    ngram_overlap,
)
from autobench.taskspec.loader import load_task
from autobench.taskspec.schema import EnvironmentSpec, ScoringSpec, TaskSpec

# ---------------------------------------------------------------------------
# hashing
# ---------------------------------------------------------------------------


def test_hash_is_stable_across_line_endings(tmp_path: Path) -> None:
    """A Windows checkout must not create a phantom new task version."""
    unix = tmp_path / "unix"
    windows = tmp_path / "windows"
    for root, newline in ((unix, "\n"), (windows, "\r\n")):
        root.mkdir()
        (root / "a.py").write_bytes(f"print('x'){newline}print('y'){newline}".encode())
    assert hash_task_directory(unix) == hash_task_directory(windows)


def test_hash_ignores_readme_but_not_tests(tmp_path: Path) -> None:
    root = tmp_path / "task"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "check.py").write_text("assert True\n")
    (root / "README.md").write_text("notes\n")
    before = hash_task_directory(root)

    (root / "README.md").write_text("different notes\n")
    assert hash_task_directory(root) == before, "prose changes must not bump the version"

    # Changing the grader changes what the score means, so it must bump.
    (root / "tests" / "check.py").write_text("assert False\n")
    assert hash_task_directory(root) != before


def test_hash_is_order_independent_but_content_sensitive(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root in (a, b):
        root.mkdir()
    (a / "x.txt").write_text("ab")
    (a / "y.txt").write_text("c")
    (b / "x.txt").write_text("a")
    (b / "y.txt").write_text("bc")
    # Length-prefixing is what stops ("ab","c") colliding with ("a","bc").
    assert hash_task_directory(a) != hash_task_directory(b)


def test_image_tag_is_deterministic() -> None:
    tag = image_tag("my-task", "sha256:" + "a" * 64)
    assert tag == "autobench/task-my-task:aaaaaaaaaaaaaaaa"


# ---------------------------------------------------------------------------
# schema and the security policy
# ---------------------------------------------------------------------------


def test_prohibits_docker_socket_mount() -> None:
    with pytest.raises(ValidationError, match="Docker socket"):
        EnvironmentSpec(mounts=["/var/run/docker.sock:/var/run/docker.sock"])


def test_prohibits_escape_capabilities() -> None:
    for capability in ("CAP_SYS_ADMIN", "SYS_PTRACE", "cap_net_admin"):
        with pytest.raises(ValidationError, match="prohibited"):
            EnvironmentSpec(cap_add=[capability])


def test_privileged_is_not_a_settable_field() -> None:
    with pytest.raises(ValidationError):
        EnvironmentSpec(privileged=True)


def test_prohibits_host_mounts_outside_the_scratch_dir() -> None:
    with pytest.raises(ValidationError, match="outside the task scratch"):
        EnvironmentSpec(mounts=["/etc:/etc"])


def test_refuses_credentials_in_task_env() -> None:
    with pytest.raises(ValidationError, match="credential"):
        EnvironmentSpec(env={"ANTHROPIC_API_KEY": "sk-ant-whatever"})


def test_verifier_cannot_have_network() -> None:
    from autobench.taskspec.schema import VerifierSpec

    with pytest.raises(ValidationError, match="network='none'"):
        VerifierSpec(network="proxy")


def test_weighted_scoring_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to"):
        ScoringSpec(
            mode="weighted",
            items=[{"id": "a", "weight": 0.5}, {"id": "b", "weight": 0.2}],
        )


def test_deterministic_task_cannot_use_a_judge() -> None:
    with pytest.raises(ValidationError, match="never the sole scorer"):
        TaskSpec.model_validate(
            {
                "task": {"id": "x-task", "title": "X", "deterministic": True},
                "scoring": {"mode": "rubric", "judge_model": "anthropic/claude-opus-5"},
            }
        )


def test_unknown_keys_are_rejected() -> None:
    """`[verifer]` must not silently mean default timeouts."""
    with pytest.raises(ValidationError):
        TaskSpec.model_validate(
            {"task": {"id": "x-task", "title": "X"}, "verifer": {"timeout_sec": 10}}
        )


def test_task_id_must_be_kebab_case() -> None:
    with pytest.raises(ValidationError, match="kebab-case"):
        TaskSpec.model_validate({"task": {"id": "Not Valid!", "title": "X"}})


# ---------------------------------------------------------------------------
# lints
# ---------------------------------------------------------------------------


def test_state_based_lint_flags_output_grading() -> None:
    result = lint_state_based(
        {"tests/check.py": 'def test_it():\n    assert "SUCCESS" in agent_stdout\n'}
    )
    assert not result.ok
    assert result.errors[0].rule == "state-based"
    assert "agent-produced" in result.errors[0].message


def test_state_based_lint_accepts_filesystem_assertions() -> None:
    result = lint_state_based(
        {
            "tests/check.py": textwrap.dedent(
                """
                from pathlib import Path

                def test_it():
                    assert Path("/app/out.txt").read_text() == "ok"
                """
            )
        }
    )
    assert result.ok


def test_tolerance_lint_flags_bare_float_equality() -> None:
    """The CORE-Bench failure, caught as a lint rather than as a model result."""
    result = lint_tolerance({"tests/check.py": "def test_it():\n    assert value == 96.124991\n"})
    assert not result.ok
    assert "tolerance" in result.errors[0].message


def test_tolerance_lint_accepts_isclose() -> None:
    result = lint_tolerance(
        {
            "tests/check.py": (
                "import math\n"
                "def test_it():\n"
                "    assert math.isclose(value, 96.124991, rel_tol=1e-6)\n"
            )
        }
    )
    assert result.ok


def test_leak_lint_catches_a_quoted_grader() -> None:
    shared = " ".join(f"word{i}" for i in range(40))
    result = lint_leak(shared, {"tests/check.py": shared})
    assert not result.ok
    assert ngram_overlap(shared, {"tests/check.py": shared}) == pytest.approx(1.0)


def test_leak_lint_allows_independent_prose() -> None:
    result = lint_leak(
        "Fix the retry decorator so the attempt count stops depending on wall-clock time.",
        {"tests/check.py": "assert Path('/app/retry.py').exists()"},
    )
    assert result.ok


def test_security_lint_catches_prohibited_compose() -> None:
    result = lint_security(
        {
            "compose.yaml": textwrap.dedent(
                """
                services:
                  app:
                    privileged: true
                    network_mode: host
                    volumes:
                      - /var/run/docker.sock:/var/run/docker.sock
                """
            )
        }
    )
    messages = " ".join(f.message for f in result.errors)
    assert "privileged" in messages
    assert "host networking" in messages
    assert "Docker socket" in messages


# ---------------------------------------------------------------------------
# loader
# ---------------------------------------------------------------------------


def test_loader_reports_missing_files(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text('[task]\nid = "x-task"\ntitle = "X"\n')
    with pytest.raises(TaskSpecError, match=r"instruction.md"):
        load_task(tmp_path)


def test_loader_reports_bad_toml_with_the_filename(tmp_path: Path) -> None:
    (tmp_path / "task.toml").write_text("[task\nid =")
    (tmp_path / "instruction.md").write_text("do a thing")
    with pytest.raises(TaskSpecError, match=r"task.toml is not valid TOML"):
        load_task(tmp_path)


def test_harbor_layout_loads_unmodified(tmp_path: Path) -> None:
    """Superset-of-Harbor has to mean the Hub corpus loads without migration."""
    (tmp_path / "task.toml").write_text(
        textwrap.dedent(
            """
            id = "harbor-style"
            name = "A Harbor task"
            difficulty = "hard"
            category = "ported"
            author = "someone"
            max_agent_timeout_sec = 600
            max_test_timeout_sec = 90
            parser_name = "pytest"
            """
        )
    )
    (tmp_path / "instruction.md").write_text("Do the thing.")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test.sh").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "solution").mkdir()
    (tmp_path / "solution" / "solve.sh").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "environment").mkdir()
    (tmp_path / "environment" / "Dockerfile").write_text("FROM scratch\n")

    task = load_task(tmp_path)
    assert task.id == "harbor-style"
    assert task.spec.task.title == "A Harbor task"
    assert task.spec.agent.timeout_sec == 600
    assert task.spec.verifier.timeout_sec == 90
    assert "test.sh" in task.spec.verifier.command


def test_tar_upload_refuses_path_traversal() -> None:
    import io
    import tarfile

    from autobench.taskspec.loader import load_task_from_tar

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("../../evil.txt")
        info.size = 4
        tar.addfile(info, io.BytesIO(b"evil"))
    buffer.seek(0)

    with pytest.raises(TaskSpecError, match="escapes the extraction root"):
        load_task_from_tar(buffer.read())
