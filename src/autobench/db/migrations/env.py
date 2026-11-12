"""Alembic environment.

The URL always comes from settings, never from ``alembic.ini`` — one source of
truth beats two that disagree at 3am. The async URL is downgraded to its sync
driver here because Alembic's migration context is synchronous.
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Literal

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy import engine_from_config, pool

from autobench.core.config import get_settings
from autobench.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    url = get_settings().database_url
    return (
        url.replace("+asyncpg", "+psycopg2")
        .replace("+aiosqlite", "")
        .replace("postgresql+psycopg2", "postgresql+psycopg2")
    )


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render Autobench's custom column types with a real, importable name.

    Without this, autogenerate emits a bare ``StringArray()`` and the migration
    does not import. Cheaper to fix here once than in every revision by hand.
    """
    if type_ == "type" and obj.__class__.__module__.startswith("autobench."):
        autogen_context.imports.add("import autobench.db.types")
        return f"autobench.db.types.{obj.__class__.__name__}()"
    return False


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    config.set_main_option("sqlalchemy.url", _sync_url())
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_item=render_item,
            # SQLite cannot ALTER most things; batch mode rewrites the table.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
