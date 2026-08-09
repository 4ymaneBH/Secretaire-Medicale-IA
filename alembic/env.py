from logging.config import fileConfig
import os
from pathlib import Path
from sqlalchemy import pool
from sqlalchemy.engine import make_url

from alembic import context
from app.db.base import Base

# Import all models to ensure they're registered with Base
from app.models import (
    CallSession, Patient, Transcript, Outcome, Appointment, PatientMemory
)

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Explicitly load the project-level .env file. Alembic only needs the database
# connection string; importing the application's Settings object here would make
# migrations depend on unrelated Twilio, OpenAI, ElevenLabs, and Redis secrets.
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


def get_database_url() -> str:
    """Return a synchronous database URL suitable for Alembic."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. Copy .env.example to .env and "
            "set DATABASE_URL before running Alembic."
        )

    url = make_url(database_url)
    if url.drivername == "postgresql+asyncpg":
        url = url.set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


# ConfigParser treats percent signs as interpolation markers, so escape them
# when passing a URL through Alembic's configuration object.
config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    from sqlalchemy import create_engine

    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
