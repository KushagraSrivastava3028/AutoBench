"""FastAPI app factory.

The control plane is stateless: it plans runs, serves the warehouse and streams
events. It never executes a trial. Two to four replicas behind a load balancer,
and a restart loses nothing but open SSE connections, which reconnect.

The reaper lives here rather than in a worker on purpose — it has to run even
when every worker is dead, which is exactly the situation it exists to detect.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from autobench import __version__
from autobench.api.routers import auth, catalog, console, runs, stats, system, trials
from autobench.core.config import Settings, get_settings
from autobench.core.errors import AutobenchError
from autobench.core.telemetry import configure_telemetry
from autobench.db.session import dispose_engine, init_models, session_scope
from autobench.worker.queue import REAPER_INTERVAL_SEC, reap_expired

logger = logging.getLogger(__name__)

DESCRIPTION = """
A containerised, parallel evaluation platform for LLM agents.

A trial is a pure function of (task version, agent version, model version, seed)
and it leaves nothing behind. Every endpoint here exists to defend that sentence.
"""


async def _reaper(settings: Settings) -> None:
    """Re-queue expired leases every 30s.

    A worker that is SIGKILLed renews nothing; its lease expires; this puts the
    trial back. No distributed lock, no consensus — a conditional UPDATE is the
    only critical section.
    """
    while True:
        try:
            await asyncio.sleep(REAPER_INTERVAL_SEC)
            async with session_scope(settings) as session:
                requeued, abandoned = await reap_expired(
                    session, max_attempts=settings.worker.max_infra_attempts
                )
            if requeued or abandoned:
                logger.info("reaper: requeued=%d abandoned=%d", requeued, abandoned)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover
            logger.warning("reaper iteration failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_telemetry(settings)

    if settings.database_url.startswith("sqlite"):
        # Local/dev convenience. Postgres deployments run `alembic upgrade head`
        # as a separate step, because create_all is not a migration strategy.
        await init_models(settings)

    if settings.auth_required and settings.bootstrap_api_key is None:
        logger.warning(
            "auth_required is set but no bootstrap key is configured; "
            "create one with `autobench keys create`"
        )

    reaper = asyncio.create_task(_reaper(settings))
    logger.info(
        "autobench %s ready (executor=%s queue=%s artifacts=%s)",
        __version__,
        settings.executor,
        settings.queue,
        settings.artifact_backend,
    )
    try:
        yield
    finally:
        reaper.cancel()
        try:
            await reaper
        except asyncio.CancelledError:
            pass
        from autobench.api.events import close_redis
        from autobench.executor import close_provider

        await close_redis()
        await close_provider()
        await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Autobench",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # The UI resumes a dropped SSE stream from this header.
        expose_headers=["Location", "Last-Event-ID"],
    )

    # --- errors: RFC 9457 problem+json, one shape for every failure --------

    @app.exception_handler(AutobenchError)
    async def _autobench_error(request: Request, exc: AutobenchError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_problem(str(request.url.path)),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "type": "https://autobench.dev/problems/request-invalid",
                "title": "Request validation failed",
                "status": 422,
                "detail": "One or more fields are invalid.",
                "instance": str(request.url.path),
                "errors": _sanitise(exc.errors()),
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "type": "about:blank",
                "title": "Internal error",
                "status": 500,
                # The message is deliberately generic: an exception string can
                # carry a connection URL, a file path or a key.
                "detail": "An unexpected error occurred. Check the server logs.",
                "instance": str(request.url.path),
            },
            media_type="application/problem+json",
        )

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(console.router)
    app.include_router(catalog.tasks_router)
    app.include_router(catalog.suites_router)
    app.include_router(catalog.catalog_router)
    app.include_router(runs.router)
    app.include_router(trials.router)
    app.include_router(stats.router)
    app.include_router(stats.export_router)

    return app


def _sanitise(errors: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop the submitted value from validation errors.

    Echoing ``input`` back is how a 422 ends up containing the API key someone
    put in the wrong field.
    """
    return [
        {
            "loc": [str(p) for p in err.get("loc", [])],
            "msg": err.get("msg"),
            "type": err.get("type"),
        }
        for err in errors
    ]


app = create_app()
