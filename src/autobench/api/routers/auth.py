"""Google Workspace sign-in. Google tokens never leave the control plane."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode
from uuid import UUID

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select

from autobench.api.deps import Principal, PrincipalDep, SessionDep, SettingsDep, write_audit
from autobench.core.config import Settings
from autobench.core.errors import ForbiddenError, NotFoundError, ValidationFailure
from autobench.core.ids import utcnow
from autobench.core.security import Scope
from autobench.db.models import OAuthFlow, WebSession, WorkspaceUser

router = APIRouter(prefix="/v1/auth", tags=["authentication"])
FLOW_COOKIE = "autobench-oauth"
SESSION_COOKIE = "autobench-session"
Role = Literal["viewer", "runner", "author", "admin"]


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def callback_url(settings: Settings) -> str:
    return settings.public_url.rstrip("/") + "/api/auth/google/callback"


class AuthOptions(BaseModel):
    google_enabled: bool


@router.get("/options", response_model=AuthOptions)
async def options(settings: SettingsDep) -> dict[str, bool]:
    return {"google_enabled": settings.google_sso_enabled}


@router.get("/google/start")
async def google_start(session: SessionDep, settings: SettingsDep) -> RedirectResponse:
    if not settings.google_sso_enabled:
        raise NotFoundError("Google Workspace sign-in is not configured")
    state, browser, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(4))
    now = utcnow()
    await session.execute(delete(OAuthFlow).where(OAuthFlow.expires_at <= now))
    await session.execute(delete(WebSession).where(WebSession.expires_at <= now))
    session.add(
        OAuthFlow(
            state_hash=digest(state),
            browser_hash=digest(browser),
            nonce=nonce,
            verifier=verifier,
            expires_at=now + timedelta(minutes=10),
        )
    )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    query = urlencode(
        {
            "client_id": settings.google_oauth_client_id,
            "redirect_uri": callback_url(settings),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + query, 302)
    response.set_cookie(
        FLOW_COOKIE,
        browser,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.public_url.startswith("https://"),
        path="/api/auth/google",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


async def exchange_identity(code: str, verifier: str, settings: Settings) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15) as client:
        result = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "redirect_uri": callback_url(settings),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
        result.raise_for_status()
        token = result.json().get("id_token")
        if not isinstance(token, str):
            raise ValueError("Google did not return an ID token")
    return await verify_google_id_token(token, settings)


async def verify_google_id_token(token: str, settings: Settings) -> dict[str, Any]:
    # Official verifier checks signature, issuer, audience and expiry. No refresh
    # or access token is persisted; sign-in requests no offline access.
    from functools import partial

    from google.auth.exceptions import GoogleAuthError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.id_token import verify_oauth2_token

    try:
        claims: dict[str, Any] = await asyncio.to_thread(
            verify_oauth2_token,
            token,
            partial(GoogleRequest(), timeout=10),
            settings.google_oauth_client_id,
            clock_skew_in_seconds=30,
        )
    except GoogleAuthError as exc:
        raise ValueError("Google identity verification unavailable") from exc
    return claims


@router.get("/google/callback")
async def google_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    state: str = "",
    code: str = "",
) -> RedirectResponse:
    failure = RedirectResponse(settings.public_url.rstrip("/") + "/login?error=google", 303)
    failure.delete_cookie(FLOW_COOKIE, path="/api/auth/google")
    failure.headers["Cache-Control"] = "no-store"
    failure.headers["Referrer-Policy"] = "no-referrer"
    browser = request.cookies.get(FLOW_COOKIE, "")
    if not settings.google_sso_enabled or not state or not browser or not code:
        return failure
    flow = (
        await session.execute(
            delete(OAuthFlow)
            .where(
                OAuthFlow.state_hash == digest(state),
                OAuthFlow.browser_hash == digest(browser),
                OAuthFlow.expires_at > utcnow(),
            )
            .returning(OAuthFlow)
        )
    ).scalar_one_or_none()
    if flow is None:
        return failure
    # Consume before calling Google, including for a failed exchange or identity.
    await session.commit()
    try:
        claims = await exchange_identity(code, flow.verifier, settings)
        domain = str(claims.get("hd", "")).lower()
        email = str(claims.get("email", "")).lower()
        subject = claims.get("sub")
        if (
            not secrets.compare_digest(str(claims.get("nonce", "")), flow.nonce)
            or claims.get("email_verified") is not True
            or domain not in {d.lower() for d in settings.google_workspace_domains}
            or not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or not email
            or len(email) > 254
        ):
            return failure
    except (httpx.HTTPError, ValueError, KeyError):
        return failure
    user = (
        await session.scalars(
            select(WorkspaceUser).where(WorkspaceUser.google_sub == subject).with_for_update()
        )
    ).one_or_none()
    if user is None:
        user = WorkspaceUser(
            google_sub=subject,
            email=email,
            domain=domain,
            display_name=str(claims.get("name", email))[:160],
            role="admin"
            if email in {e.lower() for e in settings.google_bootstrap_admins}
            else "viewer",
        )
        session.add(user)
        await session.flush()
    if not user.active:
        return failure
    user.email, user.domain, user.last_login_at = email, domain, utcnow()
    token = "as_" + secrets.token_urlsafe(32)
    previous = request.cookies.get(SESSION_COOKIE, "")
    if previous.startswith("as_"):
        await session.execute(delete(WebSession).where(WebSession.token_hash == digest(previous)))
    session.add(
        WebSession(
            token_hash=digest(token),
            user_id=user.id,
            expires_at=utcnow() + timedelta(seconds=settings.session_ttl_sec),
        )
    )
    await write_audit(
        session,
        Principal(str(user.id), user.email, set(), str(user.id)),
        "session.login",
        str(user.id),
    )
    response = RedirectResponse(settings.public_url.rstrip("/") + "/", 303)
    response.delete_cookie(FLOW_COOKIE, path="/api/auth/google")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=settings.public_url.startswith("https://"),
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_sec,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request, session: SessionDep, principal: PrincipalDep) -> None:
    token = request.headers.get("x-api-key", "")
    if not token:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if token.startswith("as_"):
        row = await session.get(WebSession, digest(token))
        if row is not None:
            row.revoked_at = utcnow()
            await write_audit(session, principal, "session.logout", principal.user_id)


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    email: str
    display_name: str
    role: str
    active: bool
    last_login_at: datetime


class MemberUpdate(BaseModel):
    role: Role
    active: bool


@router.get("/members", response_model=list[MemberOut])
async def members(session: SessionDep, principal: PrincipalDep, response: Response) -> Any:
    principal.require(Scope.ADMIN)
    response.headers["Cache-Control"] = "no-store"
    return (await session.scalars(select(WorkspaceUser).order_by(WorkspaceUser.email))).all()


@router.patch("/members/{user_id}", response_model=MemberOut)
async def update_member(
    user_id: UUID,
    body: MemberUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> WorkspaceUser:
    principal.require(Scope.ADMIN)
    # Lock all active administrators in deterministic order to serialize changes
    # that could otherwise concurrently remove the final administrator.
    admins = (
        await session.scalars(
            select(WorkspaceUser)
            .where(
                WorkspaceUser.role == "admin",
                WorkspaceUser.active.is_(True),
            )
            .order_by(WorkspaceUser.id)
            .with_for_update()
        )
    ).all()
    user = await session.get(WorkspaceUser, user_id, with_for_update=True)
    if user is None:
        raise NotFoundError("Workspace member not found")
    if str(user_id) == principal.user_id and (not body.active or body.role != "admin"):
        raise ForbiddenError("Ask another administrator to change your access")
    if user in admins and len(admins) == 1 and (not body.active or body.role != "admin"):
        raise ValidationFailure("The workspace must retain an active administrator")
    user.role, user.active = body.role, body.active
    await write_audit(session, principal, "member.update", str(user.id), detail=body.model_dump())
    if not body.active:
        await session.execute(delete(WebSession).where(WebSession.user_id == user.id))
    return user
