"""Execution plane. Everything above talks to :class:`SandboxProvider`."""

from __future__ import annotations

from autobench.core.config import Settings, get_settings
from autobench.executor.base import (
    DiffResult,
    ExecResult,
    Sandbox,
    SandboxProvider,
    SandboxSpec,
    Snapshot,
    diff_snapshots,
)

_provider: SandboxProvider | None = None


def get_provider(settings: Settings | None = None) -> SandboxProvider:
    """Process-wide provider. One Docker client per worker, not one per trial."""
    global _provider
    if _provider is None:
        settings = settings or get_settings()
        if settings.executor == "docker":
            from autobench.executor.docker_provider import DockerProvider

            _provider = DockerProvider()
        else:
            from autobench.executor.local_provider import LocalProvider

            _provider = LocalProvider()
    return _provider


def set_provider(provider: SandboxProvider | None) -> None:
    """Test hook, and the seam a v2 Kubernetes or microVM provider plugs into."""
    global _provider
    _provider = provider


async def close_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.close()
        _provider = None


__all__ = [
    "DiffResult",
    "ExecResult",
    "Sandbox",
    "SandboxProvider",
    "SandboxSpec",
    "Snapshot",
    "close_provider",
    "diff_snapshots",
    "get_provider",
    "set_provider",
]
