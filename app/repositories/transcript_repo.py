"""Data access for Transcript records."""
from typing import List, Dict, Any, Optional
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transcript import Transcript


class TranscriptRepository:
    """Repository for Transcript operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, transcript_data: dict) -> Transcript:
        transcript = Transcript(**transcript_data)
        self.db.add(transcript)
        await self.db.commit()
        await self.db.refresh(transcript)
        return transcript

    async def get_by_call_id(self, call_id: int) -> List[Transcript]:
        result = await self.db.execute(
            select(Transcript)
            .where(Transcript.call_session_id == call_id)
            .order_by(Transcript.timestamp)
        )
        return list(result.scalars().all())

    async def get_conversation(self, call_id: int) -> List[Dict[str, Any]]:
        transcripts = await self.get_by_call_id(call_id)
        return [
            {
                "speaker": t.speaker,
                "text": t.text,
                "timestamp": t.timestamp,
                "sentiment": t.sentiment_score,
                "intent": t.intent,
            }
            for t in transcripts
        ]

    async def get_average_sentiment(self, call_id: int) -> Optional[float]:
        result = await self.db.execute(
            select(func.avg(Transcript.sentiment_score)).where(
                and_(
                    Transcript.call_session_id == call_id,
                    Transcript.sentiment_score.isnot(None),
                )
            )
        )
        return result.scalar()
