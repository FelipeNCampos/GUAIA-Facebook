from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from db import models  # noqa: F401
from db.base import Base
from db.migration_runtime import connect_with_retry
from face.config import get_settings
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
if not settings.database_url:
    raise RuntimeError("DATABASE_URL must be configured before running Alembic migrations")
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    retry_attempts = int(os.getenv("ALEMBIC_CONNECT_RETRIES", "10"))
    retry_delay = float(os.getenv("ALEMBIC_CONNECT_RETRY_DELAY", "3"))

    with connect_with_retry(
        lambda: connectable.connect(),
        attempts=retry_attempts,
        delay_seconds=retry_delay,
    ) as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
