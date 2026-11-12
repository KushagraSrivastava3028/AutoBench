"""End-user console contracts: persisted budgets, access scopes and honest trends."""

import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autobench.api.deps import Principal, current_principal
from autobench.api.main import create_app
from autobench.cli.bootstrap import bootstrap
from autobench.core.ids import utcnow
from autobench.core.security import Scope
from autobench.db.models import Run, Score, Suite, SuiteMember, TaskVersion, Trial

pytestmark = pytest.mark.integration


@pytest.fixture
async def console_client(session, settings):
    await bootstrap(session, settings)
    await session.commit()
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, app


async def test_budget_persists_and_read_scope_cannot_change_it(console_client):
    client, app = console_client
    response = await client.put("/v1/console/budget", json={"weekly_limit_usd": 250})
    assert response.status_code == 200
    assert (await client.get("/v1/console/budget")).json()["weekly_limit_usd"] == 250
    assert (await client.get("/v1/console/overview")).json()["budget"]["weekly_limit_usd"] == 250
    app.dependency_overrides[current_principal] = lambda: Principal(
        "reader", "Reader", {Scope.READ}
    )
    assert (
        await client.put("/v1/console/budget", json={"weekly_limit_usd": 300})
    ).status_code == 403
    app.dependency_overrides.clear()
    assert (
        await client.put("/v1/console/budget", json={"weekly_limit_usd": -1})
    ).status_code == 422


async def test_key_is_only_shown_on_creation_and_revocation_removes_access(
    console_client, settings
):
    client, _ = console_client
    created = await client.post(
        "/v1/console/keys", json={"name": "read-only integration", "scope": "read"}
    )
    assert created.status_code == 201
    key = created.json()
    assert all("key" not in item for item in (await client.get("/v1/console/keys")).json())
    settings.auth_required = True
    try:
        assert (await client.get("/v1/console/budget")).status_code == 401
        assert (await client.get("/v1/artifacts/private/report.json")).status_code == 401
        assert (
            await client.get("/v1/console/budget", headers={"x-api-key": key["key"]})
        ).status_code == 200
        assert (
            await client.get("/v1/console/keys", headers={"x-api-key": key["key"]})
        ).status_code == 403
    finally:
        settings.auth_required = False
    assert (await client.delete(f"/v1/console/keys/{key['id']}")).status_code == 204
    settings.auth_required = True
    try:
        assert (
            await client.get("/v1/console/session", headers={"x-api-key": key["key"]})
        ).status_code == 401
    finally:
        settings.auth_required = False
    assert key["key"] not in (await client.get("/v1/console/audit")).text


async def test_trend_uses_one_comparable_cohort_and_excludes_broken_harness(
    console_client, session
):
    client, _ = console_client
    version = (await session.scalars(select(TaskVersion))).first()
    suite = Suite(slug=f"console-{uuid.uuid4().hex[:8]}", name="Console cohort")
    session.add(suite)
    await session.flush()
    session.add(SuiteMember(suite_id=suite.id, task_version_id=version.id))
    await session.commit()
    ids = []
    for index, k in enumerate([3, 5, 5]):
        response = await client.post(
            "/v1/runs",
            json={"suite": suite.slug, "agent": "oracle", "model": "mock/mock-1", "k": k},
        )
        run_id = uuid.UUID(response.json()["id"])
        ids.append(str(run_id))
        run = await session.get(Run, run_id)
        run.status = "completed"
        run.finished_at = utcnow() - timedelta(minutes=3 - index)
        run.trials_done = run.trials_total
        for trial in (await session.scalars(select(Trial).where(Trial.run_id == run_id))).all():
            trial.status = "error" if index == 2 else "scored"
            if index == 2:
                trial.error_class = "infra_docker"
            else:
                session.add(Score(trial_id=trial.id, reward=1, binary_pass=True))
        await session.commit()
    response = (await client.get(f"/v1/console/overview?suite={suite.slug}")).json()
    assert response["trend"]["k"] == 5
    assert [point["run_id"] for point in response["trend"]["points"]] == [ids[1]]
    assert any(alert["id"] == ids[2] and alert["tone"] == "infra" for alert in response["alerts"])
    valid_comparison = await client.get(f"/v1/stats/compare?a={ids[0]}&b={ids[1]}")
    assert valid_comparison.status_code == 200
    assert valid_comparison.json()["baseline_metrics"]["k"] == 3
    assert valid_comparison.json()["candidate_metrics"]["k"] == 5
    invalid_comparison = await client.get(f"/v1/stats/compare?a={ids[0]}&b={ids[2]}")
    assert invalid_comparison.status_code == 422


async def test_durable_events_resume_after_broker_restart(console_client, settings, monkeypatch):
    import asyncio

    from autobench.api import events

    client, _ = console_client
    response = await client.post(
        "/v1/runs",
        json={"suite": "core-reference", "agent": "oracle", "model": "mock/mock-1", "k": 1},
    )
    run_id = response.json()["id"]
    await events.publish(settings, run_id, {"event": "trial_update", "status": "setup"})
    monkeypatch.setattr(events, "_broker", events.InProcessBroker())
    stream = events.subscribe(settings, run_id)
    first = await asyncio.wait_for(anext(stream), timeout=2)
    await stream.aclose()
    assert first["status"] == "setup"
    await events.publish(settings, run_id, {"event": "trial_update", "status": "scored"})
    resumed = events.subscribe(settings, run_id, last_event_id=first["seq"])
    second = await asyncio.wait_for(anext(resumed), timeout=2)
    await resumed.aclose()
    assert second["status"] == "scored"
    assert second["seq"] > first["seq"]


async def test_key_without_any_scope_cannot_read_workspace(console_client, session, settings):
    from autobench.core.security import generate_api_key
    from autobench.db.models import ApiKey

    client, _ = console_client
    plaintext, prefix, hashed = generate_api_key()
    session.add(ApiKey(name="no permission", prefix=prefix, key_hash=hashed, scopes=[]))
    await session.commit()
    settings.auth_required = True
    try:
        response = await client.get("/v1/console/budget", headers={"x-api-key": plaintext})
        assert response.status_code == 403
    finally:
        settings.auth_required = False
