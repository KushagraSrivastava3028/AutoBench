"""Dependency injection: session, settings, and the authenticated principal."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from autobench.core.config import Settings, get_settings
from autobench.core.errors import AuthError, ForbiddenError
from autobench.core.ids import utcnow
from autobench.core.security import Scope, key_prefix, verify_api_key
from autobench.db.models import ApiKey, AuditLog, WebSession, WorkspaceUser
from autobench.db.session import get_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def settings_dep() -> Settings:
    return get_settings()


# An SSE response must release its authentication/query connection before
# streaming; the durable event reader opens short-lived sessions of its own.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(settings_dep)]


@dataclass(slots=True)
class Principal:
    """Who is calling, and what they may do."""

    key_id: str
    name: str
    scopes: set[Scope]
    user_id: str | None = None

    def require(self, scope: Scope) -> None:
        if scope not in self.scopes:
            raise ForbiddenError(
                f"this identity has {sorted(s.value for s in self.scopes)}; "
                f"{scope.value!r} is required"
            )

    @classmethod
    def anonymous(cls) -> Principal:
        """Development only — ``auth_required=False``. Full access, one identity."""
        return cls(key_id="anonymous", name="anonymous (auth disabled)", scopes=set(Scope))


async def current_principal(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    token = x_api_key or _bearer(authorization)

    if not settings.auth_required:
        return Principal.anonymous()
    if not token:
        raise AuthError("supply an API key in X-API-Key or Authorization: Bearer")

    if token.startswith("as_"):
        row_session = await session.get(WebSession, hashlib.sha256(token.encode()).hexdigest())
        if (
            row_session is None
            or row_session.revoked_at is not None
            or row_session.expires_at <= utcnow()
        ):
            raise AuthError("session expired or revoked")
        user = await session.get(WorkspaceUser, row_session.user_id)
        domains = {d.lower() for d in settings.google_workspace_domains}
        if user is None or not user.active or user.domain.lower() not in domains:
            raise AuthError("workspace access is disabled")
        role_scope = {"viewer": "read", "runner": "run", "author": "author", "admin": "admin"}
        scopes = Scope.implied_by({role_scope.get(user.role, "")})
        principal = Principal(str(user.id), user.email, scopes, user_id=str(user.id))
        principal.require(Scope.READ)
        return principal

    # Look up by clear-text prefix so this is an index hit rather than an
    # Argon2 verification against every row in the table.
    row = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == key_prefix(token)))
    ).scalar_one_or_none()

    if row is None or row.revoked_at is not None or not verify_api_key(token, row.key_hash):
        # Same message either way: distinguishing "no such key" from "wrong key"
        # tells an attacker which prefixes exist.
        raise AuthError("invalid or revoked API key")

    row.last_used_at = utcnow()
    principal = Principal(
        key_id=str(row.id), name=row.name, scopes=Scope.implied_by(set(row.scopes))
    )
    principal.require(Scope.READ)
    return principal


def _bearer(header: str | None) -> str | None:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


PrincipalDep = Annotated[Principal, Depends(current_principal)]


def require_scope(scope: Scope) -> object:
    """``Depends(require_scope(Scope.RUN))`` on any mutating route."""

    async def _guard(principal: PrincipalDep) -> Principal:
        principal.require(scope)
        return principal

    return Depends(_guard)


async def write_audit(
    session: AsyncSession,
    principal: Principal,
    action: str,
    target: str | None,
    request: Request | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    """Every mutating call leaves a row: actor, action, target, request hash."""
    request_hash = None
    if request is not None:
        request_hash = hashlib.sha256(
            f"{request.method}:{request.url.path}:{request.url.query}".encode()
        ).hexdigest()[:32]
    session.add(
        AuditLog(
            actor=f"user:{principal.user_id}" if principal.user_id else principal.name,
            action=action,
            target=target,
            request_hash=request_hash,
            detail=detail,
        )
    )
