"""Async engine and session factory.

SQLite needs two pieces of persuasion to behave like a database rather than a
file: WAL journalling so a reader does not block the writer, and
``foreign_keys=ON`` because SQLite ignores foreign keys by default and would
silently let cascades rot. Both are applied on connect.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from autobench.core.config import Settings, get_settings
from autobench.db.models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _prepare_sqlite_path(url: str) -> None:
    """``sqlite+aiosqlite:///./.autobench/autobench.db`` needs its directory to exist."""
    marker = ":///"
    if marker not in url:
        return
    raw = url.split(marker, 1)[1]
    if not raw or raw == ":memory:":
        return
    Path(raw).expanduser().parent.mkdir(parents=True, exist_ok=True)


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    url = settings.database_url
    kwargs: dict[str, Any] = {"echo": settings.database_echo, "future": True}

    if url.startswith("sqlite"):
        _prepare_sqlite_path(url)
        # NullPool: aiosqlite connections are cheap and pooling them across
        # tasks invites "database is locked" under concurrent writers.
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"timeout": 30}
    else:
        kwargs["pool_size"] = settings.database_pool_size
        kwargs["max_overflow"] = settings.database_max_overflow
        kwargs["pool_pre_ping"] = True

    engine = create_async_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings)
    return _engine


def get_sessionmaker(settings: Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(settings),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope(settings: Settings | None = None) -> AsyncIterator[AsyncSession]:
    """Commit on success, roll back on exception, always close."""
    factory = get_sessionmaker(settings)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_models(settings: Settings | None = None) -> None:
    """Create the schema directly.

    For local development, tests and the SQLite path. Postgres deployments run
    ``alembic upgrade head`` instead — once ``trial`` has millions of rows,
    ``create_all`` is not a migration strategy.
    """
    engine = get_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all(settings: Settings | None = None) -> None:
    engine = get_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
