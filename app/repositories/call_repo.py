"""Data access for CallSession records."""
from typing import List, Optional
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.call_session import CallSession, CallStatus
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class CallRepository:
    """Repository for CallSession operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, call_data: dict) -> CallSession:
        call = CallSession(**call_data)
        self.db.add(call)
        await self.db.commit()
        await self.db.refresh(call)
        logger.info(f"Created call: {call.call_sid}")
        return call

    async def get_by_id(self, call_id: int) -> Optional[CallSession]:
        result = await self.db.execute(
            select(CallSession).where(CallSession.id == call_id)
        )
        return result.scalar_one_or_none()

    async def get_by_sid(self, call_sid: str) -> Optional[CallSession]:
        result = await self.db.execute(
            select(CallSession)
            .options(
                selectinload(CallSession.transcripts),
                selectinload(CallSession.outcome),
            )
            .where(CallSession.call_sid == call_sid)
        )
        return result.scalar_one_or_none()

    async def update(self, call_id: int, call_data: dict) -> Optional[CallSession]:
        call = await self.get_by_id(call_id)
        if not call:
            return None
        for field, value in call_data.items():
            setattr(call, field, value)
        await self.db.commit()
        await self.db.refresh(call)
        logger.info(f"Updated call: {call.call_sid}")
        return call

    async def get_recent_calls(
        self, limit: int = 100, status: Optional[CallStatus] = None
    ) -> List[CallSession]:
        query = select(CallSession).order_by(desc(CallSession.created_at)).limit(limit)
        if status:
            query = query.where(CallSession.status == status)
        result = await self.db.execute(query)
        return list(result.scalars().all())
