"""Deployment guardrails that catch exposed ports and auth/isolation regressions."""

import importlib.util
from pathlib import Path

import pytest

from autobench.core.config import Settings
from autobench.executor.base import SandboxSpec
from autobench.executor.docker_provider import DockerProvider
from autobench.taskspec.schema import NetworkMode


async def test_network_policy_denies_before_contacting_docker(monkeypatch):
    settings = Settings()
    settings.sandbox.require_network_none = True
    monkeypatch.setattr("autobench.executor.docker_provider.get_settings", lambda: settings)
    provider = DockerProvider()
    from autobench.core.errors import SandboxError

    with pytest.raises(SandboxError, match="network-isolated"):
        await provider.create(SandboxSpec(image="test", network=NetworkMode.FULL))


def test_port_and_auth_regressions_rejected():
    path = Path(__file__).resolve().parents[2] / "scripts" / "validate_ec2_config.py"
    spec = importlib.util.spec_from_file_location("ec2_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    errors = module.validate({"services": {"postgres": {"ports": [{"published": "5432"}]}}})
    assert "postgres publishes a host port" in errors
    assert "UI authentication must be required" in errors


def test_s3_uses_instance_credentials_by_default(monkeypatch):
    from autobench.artifacts.store import S3ArtifactStore

    received = {}

    def client(service, **kwargs):
        received.update(kwargs)
        return object()

    monkeypatch.setattr("boto3.client", client)
    S3ArtifactStore(Settings(artifact_backend="s3"))._boto()
    assert "aws_access_key_id" not in received
    assert "aws_secret_access_key" not in received
    assert received["endpoint_url"] is None
