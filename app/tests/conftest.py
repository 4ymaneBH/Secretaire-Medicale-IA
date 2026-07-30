"""Test fixtures: env vars for Settings() and an in-memory SQLite DB session."""
import os

# Populate required settings before any app module is imported.
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test_token")
os.environ.setdefault("TWILIO_PHONE_NUMBER", "+10000000000")
os.environ.setdefault("TWILIO_WEBHOOK_BASE_URL", "https://example.test")
os.environ.setdefault("HUMAN_TRANSFER_NUMBER", "+19999999999")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")
os.environ.setdefault("ELEVENLABS_VOICE_ID_FR", "voice-fr")
os.environ.setdefault("ELEVENLABS_VOICE_ID_EN", "voice-en")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.db.base import Base
from app.models.call_session import CallSession, Patient
from app.models.appointment import Appointment
from app.models.transcript import Transcript
from app.models.outcome import Outcome

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Only tables without pgvector columns — SQLite has no native vector type.
_TEST_TABLES = [
    Patient.__table__,
    CallSession.__table__,
    Appointment.__table__,
    Transcript.__table__,
    Outcome.__table__,
]


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_TEST_TABLES))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()
