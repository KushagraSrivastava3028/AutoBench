"""Console overview and persisted deployment budget, using warehouse data only."""

from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from autobench.api.deps import PrincipalDep, SessionDep, SettingsDep, write_audit
from autobench.api.schemas import (
    ApiKeyCreatedOut,
    ApiKeyOut,
    AuditPageOut,
    BudgetOut,
    ConsoleConfigOut,
    OverviewOut,
    SessionOut,
)
from autobench.api.services import _run_payload, aggregate_for_run, run_select, worker_health
from autobench.core.errors import NotFoundError
from autobench.core.ids import utcnow
from autobench.core.security import Scope, generate_api_key
from autobench.db.models import ApiKey, AuditLog, ConsoleSetting, Run, Score, Trial

router = APIRouter(prefix="/v1/console", tags=["console"])


@router.get("/session", response_model=SessionOut)
async def session_info(principal: PrincipalDep) -> dict[str, Any]:
    return {"name": principal.name, "scopes": sorted(s.value for s in principal.scopes)}


@router.get("/config", response_model=ConsoleConfigOut)
async def configuration(settings: SettingsDep, principal: PrincipalDep) -> dict[str, Any]:
    return {
        "environment": settings.environment,
        "executor": settings.executor,
        "auth_required": settings.auth_required,
        "google_sso_enabled": settings.google_sso_enabled,
        "queue": settings.queue,
        "database": "postgresql" if settings.database_url.startswith("postgresql") else "sqlite",
        "artifact_backend": settings.artifact_backend,
        "providers": [
            {"name": name, "configured": bool(settings.provider_key(name))}
            for name in ("anthropic", "openai", "google", "openai_compat")
        ],
        "provider_limits": settings.provider.model_dump(),
        "scopes": sorted(s.value for s in principal.scopes),
    }


class KeyInput(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    scope: Scope = Scope.READ


def key_summary(row: ApiKey) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "prefix": row.prefix,
        "scopes": row.scopes,
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
    }


@router.get("/keys", response_model=list[ApiKeyOut])
async def list_keys(session: SessionDep, principal: PrincipalDep) -> list[dict[str, Any]]:
    principal.require(Scope.ADMIN)
    return [
        key_summary(row)
        for row in (await session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))).all()
    ]


@router.post("/keys", status_code=201, response_model=ApiKeyCreatedOut)
async def create_key(
    body: KeyInput, session: SessionDep, principal: PrincipalDep, response: Response
) -> dict[str, Any]:
    principal.require(Scope.ADMIN)
    response.headers["Cache-Control"] = "no-store"
    plaintext, prefix, hashed = generate_api_key()
    row = ApiKey(name=body.name.strip(), prefix=prefix, key_hash=hashed, scopes=[body.scope.value])
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        principal,
        "key.create",
        str(row.id),
        detail={"name": row.name, "scope": body.scope.value},
    )
    return {**key_summary(row), "key": plaintext}


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: UUID, session: SessionDep, principal: PrincipalDep) -> None:
    principal.require(Scope.ADMIN)
    row = await session.get(ApiKey, key_id)
    if row is None:
        raise NotFoundError("API key not found")
    row.revoked_at = utcnow()
    await write_audit(session, principal, "key.revoke", str(key_id))


@router.get("/audit", response_model=AuditPageOut)
async def audit_log(
    session: SessionDep, principal: PrincipalDep, before: int | None = None
) -> dict[str, Any]:
    principal.require(Scope.ADMIN)
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(50)
    if before is not None:
        query = query.where(AuditLog.id < before)
    rows = (await session.scalars(query)).all()
    return {
        "items": [
            {"id": r.id, "at": r.ts, "actor": r.actor, "action": r.action, "target": r.target}
            for r in rows
        ],
        "next_cursor": rows[-1].id if len(rows) == 50 else None,
    }


class BudgetInput(BaseModel):
    weekly_limit_usd: float | None = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)


async def budget_state(session: AsyncSession) -> dict[str, Any]:
    now = utcnow()
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await session.get(ConsoleSetting, "budget")
    spent = await session.scalar(
        select(func.coalesce(func.sum(Score.cost_usd), 0))
        .join(Trial, Trial.id == Score.trial_id)
        .where(Trial.finished_at >= start)
    )
    return {
        "spent_usd": float(spent or 0),
        "weekly_limit_usd": row.value.get("weekly_limit_usd") if row else None,
        "period_start": start,
        "period_end": start + timedelta(days=7),
        "timezone": "UTC",
    }


@router.get("/budget", response_model=BudgetOut)
async def get_budget(session: SessionDep, principal: PrincipalDep) -> dict[str, Any]:
    return await budget_state(session)


@router.put("/budget", response_model=BudgetOut)
async def update_budget(
    body: BudgetInput, session: SessionDep, principal: PrincipalDep
) -> dict[str, Any]:
    principal.require(Scope.ADMIN)
    row = await session.get(ConsoleSetting, "budget")
    if row is None:
        session.add(ConsoleSetting(key="budget", value=body.model_dump()))
    else:
        row.value = body.model_dump()
    await write_audit(session, principal, "budget.update", "deployment", detail=body.model_dump())
    await session.flush()
    return await budget_state(session)


@router.get("/overview", response_model=OverviewOut)
async def overview(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    days: Annotated[int, Query(ge=1, le=30)] = 7,
    suite: str | None = None,
) -> dict[str, Any]:
    now = utcnow()
    recent = list(
        (await session.scalars(run_select().order_by(Run.created_at.desc()).limit(8))).all()
    )
    active = (
        await session.scalars(
            run_select().where(Run.status == "running").order_by(Run.created_at.desc()).limit(1)
        )
    ).first()
    counts = {
        status: count
        for status, count in (
            await session.execute(select(Run.status, func.count()).group_by(Run.status))
        ).all()
    }
    candidates_query = run_select().where(
        Run.status == "completed", Run.finished_at >= now - timedelta(days=days)
    )
    if suite:
        from autobench.db.models import Suite

        candidates_query = candidates_query.join(Suite).where(Suite.slug == suite)
    candidates = list(
        (await session.scalars(candidates_query.order_by(Run.finished_at.desc()).limit(100))).all()
    )
    points = []
    series = None
    alerts = []
    for run in candidates:
        aggregate = await aggregate_for_run(session, run, settings)
        invalid = aggregate.infra_errors / max(run.trials_total, 1) > 0.02
        if invalid:
            alerts.append(
                {
                    "id": str(run.id),
                    "tone": "infra",
                    "title": "Run invalid — harness errors",
                    "detail": (
                        f"{run.label or run.suite.slug}: {aggregate.infra_errors} "
                        "infrastructure errors. Excluded from benchmark trend."
                    ),
                    "href": f"/runs/{run.id}",
                }
            )
            continue
        if any(task.attempts < run.k for task in aggregate.tasks) or not aggregate.total_trials:
            continue
        identity = (str(run.suite_id), str(run.agent_id), str(run.model_id), run.k)
        if series is None:
            series = {
                "identity": identity,
                "suite": run.suite.slug,
                "agent": run.agent.slug,
                "model": run.model.slug,
                "k": run.k,
            }
        if identity == series["identity"]:
            points.append(
                {
                    "run_id": str(run.id),
                    "at": run.finished_at,
                    "pass_at_k": aggregate.pass_at_k.to_json(),
                    "pass_caret_k": aggregate.pass_caret_k.to_json(),
                    "reportable": aggregate.reportable,
                }
            )
    workers = await worker_health(session)
    healthy_workers = [w for w in workers if not w["stale"]]
    if not healthy_workers:
        alerts.insert(
            0,
            {
                "id": "workers",
                "tone": "warn",
                "title": "No workers available",
                "detail": "Evaluations will stay queued until a worker connects.",
                "href": "/ops",
            },
        )
    budget = await budget_state(session)
    limit = budget["weekly_limit_usd"]
    if limit and budget["spent_usd"] >= limit * 0.8:
        alerts.append(
            {
                "id": "budget",
                "tone": "warn",
                "title": "Weekly budget needs attention",
                "detail": "Spend has reached at least 80% of the weekly target.",
                "href": "/settings/budgets",
            }
        )
    return {
        "active_run": _run_payload(active) if active else None,
        "recent_runs": [_run_payload(r) for r in recent],
        "running_count": counts.get("running", 0),
        "queued_count": counts.get("queued", 0),
        "budget": budget,
        "trend": {
            **{k: v for k, v in (series or {}).items() if k != "identity"},
            "points": list(reversed(points)),
        },
        "alerts": alerts[:8],
        "generated_at": now,
        "workers_active": len(healthy_workers),
        "executor": settings.executor,
        "auth_required": settings.auth_required,
        "instance_name": "AutoBench",
        "scopes": sorted(s.value for s in principal.scopes),
    }
