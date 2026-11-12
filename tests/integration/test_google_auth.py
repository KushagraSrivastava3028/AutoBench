"""Browser-bound OAuth flow, live role enforcement and revocable sessions."""

import hashlib
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from autobench.api.main import create_app
from autobench.api.routers import auth
from autobench.core.ids import utcnow
from autobench.db.models import OAuthFlow, WebSession, WorkspaceUser


@pytest.fixture
async def google_client(session, settings, monkeypatch):
    for name, value in {
        "auth_required": True,
        "google_oauth_client_id": "test-client",
        "google_oauth_client_secret": "test-secret",
        "google_workspace_domains": ["example.org"],
        "google_bootstrap_admins": ["owner@example.org"],
        "public_url": "http://test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(settings)), base_url="http://test"
    ) as client:
        yield client


async def begin(client):
    response = await client.get("/v1/auth/google/start")
    assert response.status_code == 302
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == ["http://test/api/auth/google/callback"]
    assert "offline" not in response.headers["location"]
    return params, response.cookies.get(auth.FLOW_COOKIE)


async def sign_in(
    client, monkeypatch, *, email="member@example.org", subject="member", **overrides
):
    params, browser = await begin(client)

    async def exchange(code, verifier, settings):
        assert code == "test-code" and len(verifier) >= 43
        return {
            "sub": subject,
            "email": email,
            "name": "Member",
            "hd": "example.org",
            "email_verified": True,
            "nonce": params["nonce"][0],
            **overrides,
        }

    monkeypatch.setattr(auth, "exchange_identity", exchange)
    response = await client.get(
        "/v1/auth/google/callback",
        params={"state": params["state"][0], "code": "test-code"},
        headers={"cookie": f"{auth.FLOW_COOKIE}={browser}"},
    )
    return response, params, browser


async def test_login_defaults_to_viewer_and_logout_revokes(google_client, session, monkeypatch):
    client = google_client
    response, params, browser = await sign_in(client, monkeypatch)
    token = response.cookies.get(auth.SESSION_COOKIE)
    assert token.startswith("as_") and len(token) > 43
    assert "HttpOnly" in response.headers["set-cookie"] or any(
        "HttpOnly" in cookie for cookie in response.headers.get_list("set-cookie")
    )
    stored = await session.get(WebSession, hashlib.sha256(token.encode()).hexdigest())
    assert stored is not None and token != stored.token_hash
    headers = {"x-api-key": token}
    identity = await client.get("/v1/console/session", headers=headers)
    assert identity.status_code == 200 and identity.json()["scopes"] == ["read"]
    assert (
        await client.put("/v1/console/budget", json={"weekly_limit_usd": 1}, headers=headers)
    ).status_code == 403
    replay = await client.get(
        "/v1/auth/google/callback",
        params={"state": params["state"][0], "code": "test-code"},
        headers={"cookie": f"{auth.FLOW_COOKIE}={browser}"},
    )
    assert "error=google" in replay.headers["location"]
    assert (await client.post("/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/v1/console/session", headers=headers)).status_code == 401


@pytest.mark.parametrize(
    "claims",
    [
        {"hd": "attacker.org"},
        {"hd": ""},
        {"nonce": "wrong"},
        {"email_verified": False},
        {"sub": ""},
    ],
)
async def test_untrusted_identity_rejected(google_client, monkeypatch, claims):
    response, _, _ = await sign_in(google_client, monkeypatch, **claims)
    assert "error=google" in response.headers["location"]
    assert auth.SESSION_COOKIE not in response.cookies


async def test_wrong_browser_and_expired_state_never_exchange(google_client, session, monkeypatch):
    client = google_client
    params, browser = await begin(client)

    async def forbidden(*args):
        pytest.fail("Unbound or expired flow must not reach Google")

    monkeypatch.setattr(auth, "exchange_identity", forbidden)
    url_params = {"state": params["state"][0], "code": "test-code"}
    response = await client.get(
        "/v1/auth/google/callback",
        params=url_params,
        headers={"cookie": f"{auth.FLOW_COOKIE}=other-browser"},
    )
    assert "error=google" in response.headers["location"]
    flow = await session.get(OAuthFlow, auth.digest(params["state"][0]))
    flow.expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    response = await client.get(
        "/v1/auth/google/callback",
        params=url_params,
        headers={"cookie": f"{auth.FLOW_COOKIE}={browser}"},
    )
    assert "error=google" in response.headers["location"]


async def test_admin_changes_role_and_disable_is_immediate(google_client, session, monkeypatch):
    client = google_client
    owner_response, _, _ = await sign_in(
        client, monkeypatch, email="owner@example.org", subject="owner"
    )
    admin_headers = {"x-api-key": owner_response.cookies.get(auth.SESSION_COOKIE)}
    member_response, _, _ = await sign_in(
        client, monkeypatch, email="runner@example.org", subject="second-member"
    )
    member_headers = {"x-api-key": member_response.cookies.get(auth.SESSION_COOKIE)}
    members = (await client.get("/v1/auth/members", headers=admin_headers)).json()
    member = next(item for item in members if item["email"] == "runner@example.org")
    assert (await client.get("/v1/auth/members", headers=member_headers)).status_code == 403
    response = await client.patch(
        f"/v1/auth/members/{member['id']}",
        json={"role": "runner", "active": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert (await client.get("/v1/console/session", headers=member_headers)).json()["scopes"] == [
        "read",
        "run",
    ]
    assert (
        await client.patch(
            f"/v1/auth/members/{member['id']}",
            json={"role": "runner", "active": False},
            headers=admin_headers,
        )
    ).status_code == 200
    assert (await client.get("/v1/console/session", headers=member_headers)).status_code == 401
    owner = next(item for item in members if item["email"] == "owner@example.org")
    assert (
        await client.patch(
            f"/v1/auth/members/{owner['id']}",
            json={"role": "viewer", "active": False},
            headers=admin_headers,
        )
    ).status_code == 403
    assert (
        await session.scalars(select(WorkspaceUser).where(WorkspaceUser.google_sub == "owner"))
    ).one().role == "admin"


async def test_expired_session_is_rejected(google_client, session, monkeypatch):
    response, _, _ = await sign_in(google_client, monkeypatch, subject="expiry-member")
    token = response.cookies.get(auth.SESSION_COOKIE)
    stored = await session.get(WebSession, auth.digest(token))
    stored.expires_at = utcnow() - timedelta(seconds=1)
    await session.commit()
    assert (
        await google_client.get("/v1/console/session", headers={"x-api-key": token})
    ).status_code == 401
