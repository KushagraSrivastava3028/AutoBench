"""Shared fixtures.

Every test runs against the local executor, an in-memory-ish SQLite file and a
local artifact store, so `pytest` needs no Docker daemon, no Postgres and no
API key. The integration tests that *do* need a daemon are marked and skipped
when one is absent.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_TASKS = REPO_ROOT / "tests" / "fixtures" / "tasks"
REFERENCE_TASKS = REPO_ROOT / "tasks" / "_reference"


@pytest.fixture(scope="session", autouse=True)
def _isolate_environment(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Point every setting at a throwaway directory before anything imports config."""
    state = tmp_path_factory.mktemp("autobench-state")
    os.environ.update(
        {
            "AUTOBENCH_ENVIRONMENT": "development",
            "AUTOBENCH_EXECUTOR": "local",
            "AUTOBENCH_QUEUE": "memory",
            "AUTOBENCH_ARTIFACT_BACKEND": "local",
            "AUTOBENCH_STATE_DIR": str(state),
            "AUTOBENCH_DATABASE_URL": f"sqlite+aiosqlite:///{(state / 'test.db').as_posix()}",
            "AUTOBENCH_TASKS_DIR": str(REFERENCE_TASKS),
            "AUTOBENCH_AUTH_REQUIRED": "false",
            "AUTOBENCH_LOG_LEVEL": "WARNING",
        }
    )
    from autobench.core.config import reset_settings_cache

    reset_settings_cache()
    yield


@pytest.fixture
def settings():
    from autobench.core.config import get_settings

    return get_settings()


@pytest.fixture
def fixture_task():
    """Load a fixture task by directory name."""
    from autobench.taskspec.loader import load_task

    def _load(name: str):
        root = FIXTURE_TASKS / name
        if not root.is_dir():
            root = REFERENCE_TASKS / name
        return load_task(root)

    return _load


@pytest.fixture
async def session() -> AsyncIterator[object]:
    """A committed-per-test session against a fresh schema."""
    from autobench.db.session import dispose_engine, init_models, session_scope

    await init_models()
    async with session_scope() as active:
        yield active
    await dispose_engine()


@pytest.fixture
def pipeline():
    from autobench.worker.pipeline import TrialPipeline

    return TrialPipeline()


def docker_available() -> bool:
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


requires_docker = pytest.mark.skipif(not docker_available(), reason="needs a running Docker daemon")
